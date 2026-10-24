"""Employee tools over the Perk export: lookups, attendance, leave, attention flags, overview, summary.

Every function returns a plain dict and never raises; bad input comes back as {"error": "..."}.
All data is read through store.py, so every CSV value is a string and is converted here.
"""
import functools
import json
from collections import Counter
from datetime import date, datetime

import config
import llm
import store

ATTENDANCE_MIN = 85      # attendance % below this is flagged
LOW_RATING_MAX = 2       # latest rating at or below this is flagged
ATTENDANCE_DAYS = 30     # working days used for flags and the team overview

_FLAG_TOPICS = {
    "attendance": "ask what is behind the attendance and how to support them",
    "goal_overdue": "agree a realistic plan for the overdue goal(s)",
    "low_rating": "check progress on the actions agreed in the latest review",
}


class _Bad(Exception):
    """Bad input. The message goes back to the caller as {"error": ...}."""


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except _Bad as e:
            return {"error": str(e)}
        except Exception as e:
            return {"error": f"{fn.__name__} failed: {e}"}
    return wrapper


# ---- data ----

def _group(rows):
    grouped = {}
    for r in rows:
        grouped.setdefault(r["employee_id"], []).append(r)
    return grouped


def _to_date(value):
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _int(value, default=0):
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return default


class _Data:
    """One snapshot of the files, read once per tool call."""

    def __init__(self):
        self.today = date.fromisoformat(config.TODAY)
        self.employees = store.read_csv("employees.csv")
        if not self.employees:
            raise _Bad("No employee data found. Run scripts/make_dummy_data.py first.")
        self.attendance = {}  # employee_id -> {date: status}
        for r in store.read_csv("attendance.csv"):
            self.attendance.setdefault(r["employee_id"], {})[r["date"]] = r["status"].strip().lower()
        self.goals = _group(store.read_csv("goals.csv"))
        self.reviews = _group(store.read_csv("reviews.csv"))
        self.leaves = []  # approved only, with real dates
        for r in store.read_csv("leaves.csv"):
            start, end = _to_date(r["start"]), _to_date(r["end"])
            if r["status"].strip().lower() == "approved" and start and end:
                self.leaves.append({"employee_id": r["employee_id"], "type": r["type"], "start": start, "end": end})


def _members(d, team):
    team = str(team).strip() if team else ""
    if not team:
        return d.employees
    found = [e for e in d.employees if e["team"].lower() == team.lower()]
    if not found:
        raise _Bad(f"No team called '{team}'. Teams: {', '.join(sorted({e['team'] for e in d.employees}))}.")
    return found


def _find(d, text):
    """id or full name, then a whole first/last name, then part of a name; each only if it picks one person."""
    q = " ".join(str(text or "").lower().split())
    if not q:
        raise _Bad("Give an employee name or id.")
    tiers = (
        lambda e: q in (e["id"].lower(), e["name"].lower()),
        lambda e: q in e["name"].lower().split(),
        lambda e: q in e["name"].lower(),
    )
    ambiguous = None
    for matches in tiers:
        found = [e for e in d.employees if matches(e)]
        if len(found) == 1:
            return found[0]
        if len(found) > 1 and ambiguous is None:
            ambiguous = found
    if ambiguous:
        names = ", ".join(f"{e['name']} ({e['id']})" for e in ambiguous)
        raise _Bad(f"'{text}' matches more than one person: {names}. Use the full name or id.")
    raise _Bad(f"No employee found matching '{text}'.")


def _need_date(value, label):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    parsed = _to_date(value)
    if parsed is None:
        raise _Bad(f"{label} must be a date like 2026-10-23, got '{value}'.")
    return parsed


def _need_days(days):
    if days is None:
        return ATTENDANCE_DAYS
    try:
        n = int(float(str(days).strip()))
    except ValueError:
        n = 0
    if n < 1:
        raise _Bad(f"days must be a whole number of 1 or more, got '{days}'.")
    return n


def _count(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


# ---- per-person facts ----

def _status_today(d, emp_id):
    status = d.attendance.get(emp_id, {}).get(d.today.isoformat())
    if status:
        return status
    if any(l["employee_id"] == emp_id and l["start"] <= d.today <= l["end"] for l in d.leaves):
        return "leave"
    return "unknown"


def _attendance(d, emp_id, days):
    """Counts over the last `days` rows up to today. Leave is not held against anyone."""
    today = d.today.isoformat()
    rows = sorted((day, s) for day, s in d.attendance.get(emp_id, {}).items() if day <= today)[-days:]
    counts = Counter(s for _, s in rows)
    attended = counts["present"] + counts["wfh"]
    counted = attended + counts["absent"]
    return {"days": len(rows), "present": counts["present"], "wfh": counts["wfh"], "leave": counts["leave"],
            "absent": counts["absent"], "counted": counted,  # counted == 0: no attendance data, the 100.0 is a default
            "attendance_pct": round(attended / counted * 100, 1) if counted else 100.0}


def _is_done(goal):
    return goal["status"].strip().lower() == "done"


def _goals(d, emp_id):
    goals = []
    for g in d.goals.get(emp_id, []):
        target = _to_date(g["target_date"])
        goals.append({"id": g["id"], "employee_id": emp_id, "title": g["title"], "target_date": g["target_date"],
                      "progress_pct": _int(g["progress_pct"]), "status": g["status"],
                      "overdue": not _is_done(g) and target is not None and target < d.today})
    return goals


def _latest_review(d, emp_id):
    reviews = d.reviews.get(emp_id)
    if not reviews:
        return None
    r = max(reviews, key=lambda r: r["cycle"])
    return {"employee_id": r["employee_id"], "cycle": r["cycle"], "rating": _int(r["rating"], None),
            "notes": r["notes"], "reviewer_id": r["reviewer_id"]}


def _flags(emp, att, goals, review, today):
    flags = []

    def flag(rule, detail):
        flags.append({"employee": emp["name"], "employee_id": emp["id"], "rule": rule, "detail": detail})

    if att["attendance_pct"] < ATTENDANCE_MIN:
        flag("attendance", f"Attendance {att['attendance_pct']}% in last {att['days']} working days "
                           f"({_count(att['absent'], 'day')} absent)")
    for g in goals:
        if g["overdue"]:
            late = (today - _to_date(g["target_date"])).days
            flag("goal_overdue", f"Goal '{g['title']}' is {_count(late, 'day')} overdue "
                                 f"(due {g['target_date']}, {g['progress_pct']}% done)")
    if review and review["rating"] is not None and review["rating"] <= LOW_RATING_MAX:
        flag("low_rating", f"Latest rating {review['rating']} in the {review['cycle']} review")
    return flags


def _facts(d, emp):
    att = _attendance(d, emp["id"], ATTENDANCE_DAYS)
    goals = _goals(d, emp["id"])
    review = _latest_review(d, emp["id"])
    return {"attendance": att, "goals": goals, "review": review, "status_today": _status_today(d, emp["id"]),
            "flags": _flags(emp, att, goals, review, d.today)}


# ---- tools ----

@_safe
def list_employees(team=None):
    d = _Data()
    return {"employees": [{"id": e["id"], "name": e["name"], "role": e["role"], "team": e["team"],
                           "manager_id": e["manager_id"] or None, "status_today": _status_today(d, e["id"])}
                          for e in _members(d, team)]}


@_safe
def get_employee(name_or_id):
    d = _Data()
    e = _find(d, name_or_id)
    return {
        "employee": {
            "id": e["id"], "name": e["name"], "email": e["email"], "role": e["role"], "team": e["team"],
            "manager_id": e["manager_id"] or None, "join_date": e["join_date"], "location": e["location"],
            "skills": [s.strip() for s in e["skills"].split(";") if s.strip()],
            "leave_balance": {"casual": _int(e["casual_balance"]), "sick": _int(e["sick_balance"]),
                              "earned": _int(e["earned_balance"])},
            "status_today": _status_today(d, e["id"]),
        },
        "goals": _goals(d, e["id"]),
        "latest_review": _latest_review(d, e["id"]),
    }


@_safe
def who_is_on_leave(start, end, team=None):
    first, last = _need_date(start, "start"), _need_date(end, "end")
    if last < first:
        raise _Bad(f"end ({last}) is before start ({first}).")
    d = _Data()
    people = {e["id"]: e for e in _members(d, team)}
    hits = sorted((l for l in d.leaves if l["employee_id"] in people and l["start"] <= last and l["end"] >= first),
                  key=lambda l: (l["start"], people[l["employee_id"]]["name"]))
    return {"start": first.isoformat(), "end": last.isoformat(),
            "on_leave": [{"name": people[l["employee_id"]]["name"], "employee_id": l["employee_id"],
                          "team": people[l["employee_id"]]["team"], "type": l["type"],
                          "from": l["start"].isoformat(), "to": l["end"].isoformat()} for l in hits]}


@_safe
def get_attendance(name_or_id, days=ATTENDANCE_DAYS):
    n = _need_days(days)
    d = _Data()
    e = _find(d, name_or_id)
    return {"employee": e["name"], "employee_id": e["id"], **_attendance(d, e["id"], n)}


@_safe
def get_attention_flags(team=None):
    d = _Data()
    return {"flags": [flag for e in _members(d, team) for flag in _facts(d, e)["flags"]]}


@_safe
def team_overview(team=None):
    d = _Data()
    rows, present, on_leave, flagged = [], 0, 0, 0
    for e in _members(d, team):
        f = _facts(d, e)
        present += f["status_today"] in ("present", "wfh")
        on_leave += f["status_today"] == "leave"
        flagged += bool(f["flags"])
        rows.append({"name": e["name"], "role": e["role"], "team": e["team"], "status_today": f["status_today"],
                     "attendance_pct": f["attendance"]["attendance_pct"],
                     "attendance_known": f["attendance"]["counted"] > 0,
                     "goals_on_track": sum(1 for g in f["goals"] if not g["overdue"] and not _is_done(g)),
                     "goals_overdue": sum(1 for g in f["goals"] if g["overdue"]),
                     "latest_rating": f["review"]["rating"] if f["review"] else None})
    return {"headcount": len(rows), "present_today": present, "on_leave_today": on_leave, "flagged": flagged,
            "rows": rows}


# ---- summary ----

def _tenure(joined, today):
    start = _to_date(joined)
    if start is None:
        return "tenure unknown"
    months = (today.year - start.year) * 12 + today.month - start.month - (today.day < start.day)
    years, months = divmod(max(months, 0), 12)
    if years:
        return f"{_count(years, 'year')} {_count(months, 'month')}" if months else _count(years, "year")
    return _count(months, "month") if months else "under a month"


def _summary_facts(d, e):
    f = _facts(d, e)
    return {
        "name": e["name"], "employee_id": e["id"], "role": e["role"], "team": e["team"], "location": e["location"],
        "join_date": e["join_date"], "tenure": _tenure(e["join_date"], d.today), "status_today": f["status_today"],
        "leave_balance": {"casual": _int(e["casual_balance"]), "sick": _int(e["sick_balance"]),
                          "earned": _int(e["earned_balance"])},
        "attendance": f["attendance"],
        "goals": [{k: g[k] for k in ("title", "target_date", "progress_pct", "status", "overdue")}
                  for g in f["goals"]],
        "latest_review": f["review"],
        "flags": [{"rule": fl["rule"], "detail": fl["detail"]} for fl in f["flags"]],
    }


def _rules_summary(f):
    a, goals, review = f["attendance"], f["goals"], f["latest_review"]
    overdue = [g for g in goals if g["overdue"]]
    done = sum(1 for g in goals if _is_done(g))
    if goals:
        goal_line = (f"Goals: {len(goals)} in total, {len(goals) - len(overdue) - done} on track, {done} done, "
                     f"{len(overdue)} overdue")
        if overdue:
            goal_line += " (" + "; ".join(f"'{g['title']}' {g['progress_pct']}% done, due {g['target_date']}"
                                          for g in overdue) + ")"
        goal_line += "."
    else:
        goal_line = "Goals: none on file."
    topics = [_FLAG_TOPICS[r] for r in dict.fromkeys(fl["rule"] for fl in f["flags"])]
    focus = ("For the 1:1: " + "; ".join(topics) + "." if topics
             else "For the 1:1: nothing is flagged, so ask what is going well and what support they need.")
    review_line = (f"Latest review ({review['cycle']}): rating {review['rating']}. {review['notes']}" if review
                   else "No review on file yet.")
    bullets = [
        f"{f['name']}, {f['role']}, {f['team']} team ({f['location']}), joined {f['join_date']} "
        f"({f['tenure']}). Today: {f['status_today']}.",
        f"Attendance {a['attendance_pct']}% over the last {a['days']} working days "
        f"({a['absent']} absent, {a['wfh']} WFH, {a['leave']} on leave).",
        goal_line,
        review_line,
        focus,
    ]
    return "\n".join("- " + b for b in bullets)


def _llm_summary(facts):
    """Five bullets from Claude, or None when offline, the call fails or the reply is off-format."""
    if llm.MOCK:
        return None
    prompt = (
        "Write a 1:1 prep note for this employee's manager as exactly 5 bullet points, each starting with '- '. "
        "In order: role and context, Attendance, goals, latest review, what to discuss. "
        "Use only the facts below; do not add or guess anything. Be specific and supportive. "
        "The attendance bullet must contain the word \"Attendance\".\n\nFacts (JSON):\n"
        + json.dumps(facts, indent=2))
    try:
        reply = llm.complete_text(prompt, system="You are HumaNova, a careful HR assistant. Reply with the bullets only.")
    except Exception:
        return None
    bullets = [line.strip().lstrip("-*• ").strip() for line in reply.splitlines()
               if line.strip().startswith(("-", "*", "•"))]
    if len(bullets) != 5 or "attendance" not in reply.lower():
        return None
    return "\n".join("- " + b for b in bullets)


@_safe
def summarise_employee(name_or_id):
    d = _Data()
    facts = _summary_facts(d, _find(d, name_or_id))
    text = _llm_summary(facts)
    if text:
        return {"summary": text, "facts": facts, "source": "llm"}
    return {"summary": _rules_summary(facts), "facts": facts, "source": "rules"}
