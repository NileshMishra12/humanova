"""Performance and appraisal tools (owner: Lead).

Nexus already tracks work, so we only read its numbers (data/nexus_work.csv, data/sows.json) and add the HR
layer on top. Every rule and weight comes from policies/appraisal_policy.json via policy.py.
Every function returns a plain dict and never raises.
"""
import functools

import config
import llm
import nexus_client
import policy
import store

SOURCES = {"delivery": "Nexus", "on_time": "Nexus", "quality": "Nexus",
           "goals": "Goals", "review": "Last review", "attendance": "Perk (attendance)"}
LABELS = {"delivery": "Delivery", "on_time": "On-time delivery", "quality": "Quality",
          "goals": "Goals", "review": "Last review", "attendance": "Attendance", "trend": "Trend adjustment"}


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            return {"error": f"{fn.__name__} failed: {type(e).__name__}: {e}"}
    return wrapper


def _people():
    from tools import employee_tools  # Member A's module, imported here so this file loads without it
    return employee_tools


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _int(v):
    return int(_num(v) or 0)


def _fmt(v, digits=1, suffix=""):
    return "-" if v is None else f"{round(v, digits) + 0.0:.{digits}f}{suffix}"  # + 0.0 turns -0 into 0


# ---------------- Nexus numbers ----------------

def _nexus_rows(emp_id, weeks=12):
    rows = sorted(nexus_client.work_rows(emp_id), key=lambda r: r["week"])
    out = []
    for r in rows[-int(weeks):]:
        days, done = _int(r.get("days_worked")), _int(r.get("tasks_done"))
        out.append({"week": r["week"], "days_worked": days, "tasks_done": done,
                    "tasks_per_day": round(done / days, 2) if days else None,  # per day WORKED, so leave is free
                    "bugs_fixed": _int(r.get("bugs_fixed")), "bugs_reopened": _int(r.get("bugs_reopened")),
                    "on_time_pct": _num(r.get("on_time_pct")), "sow": r.get("sow")})
    return out


def _totals(rows):
    worked = [r for r in rows if r["days_worked"] > 0]  # a week fully on leave says nothing about delivery
    days = sum(r["days_worked"] for r in worked)
    tasks = sum(r["tasks_done"] for r in worked)
    on_time = [r["on_time_pct"] for r in worked if r["on_time_pct"] is not None]
    return {"days_worked": days, "tasks_done": tasks,
            "bugs_fixed": sum(r["bugs_fixed"] for r in worked),
            "bugs_reopened": sum(r["bugs_reopened"] for r in worked),
            "tasks_per_day": tasks / days if days else None,
            "on_time_pct": sum(on_time) / len(on_time) if on_time else None}


def _trend(rows):
    rule = policy.load()["rubric_rules"]["trend"]
    n = rule["window_weeks"]
    recent, prior = _totals(rows[-n:]), _totals(rows[-2 * n:-n])
    if len(rows) <= n or prior["tasks_per_day"] is None or recent["tasks_per_day"] is None:
        return {"trend": "stable", "recent": recent, "prior": prior,
                "summary": f"Not enough weeks of Nexus data to compare {n}-week windows: stable."}
    rel = (recent["tasks_per_day"] - prior["tasks_per_day"]) / prior["tasks_per_day"] * 100 \
        if prior["tasks_per_day"] else None
    pts = None
    if recent["on_time_pct"] is not None and prior["on_time_pct"] is not None:
        pts = recent["on_time_pct"] - prior["on_time_pct"]
    up_rel, up_pts = rule["productivity_change_pct"], rule["on_time_change_points"]
    up = (rel is not None and rel >= up_rel) or (pts is not None and pts >= up_pts)
    down = (rel is not None and rel <= -up_rel) or (pts is not None and pts <= -up_pts)
    trend = "improving" if up and not down else "declining" if down and not up else "stable"
    summary = (f"Tasks per day worked {_fmt(recent['tasks_per_day'], 2)} vs {_fmt(prior['tasks_per_day'], 2)} "
               f"({_fmt(rel, 0, '%')}), on-time {_fmt(recent['on_time_pct'], 0, '%')} vs "
               f"{_fmt(prior['on_time_pct'], 0, '%')} ({_fmt(pts, 0, ' pts')}): {trend}.")
    return {"trend": trend, "recent": recent, "prior": prior, "summary": summary}


# ---------------- trend, team and projects ----------------

@_safe
def performance_trend(name_or_id, weeks=12):
    found = _people().get_employee(name_or_id)
    if "error" in found:
        return found
    emp = found["employee"]
    rows = _nexus_rows(emp["id"], weeks)
    if not rows:
        return {"error": f"No Nexus work data for {emp['name']}"}
    t = _trend(rows)
    return {"employee": emp["name"], "employee_id": emp["id"], "weeks": rows,
            "trend": t["trend"], "summary": t["summary"], "recent": t["recent"], "prior": t["prior"]}


def _team_rows(team):
    people = _people().list_employees(team)["employees"]
    rows = []
    for p in people:
        work = _nexus_rows(p["id"])
        if not work:
            rows.append({"name": p["name"], "employee_id": p["id"], "role": p["role"], "team": p["team"],
                         "tasks_per_day": None, "on_time_pct": None, "trend": None, "summary": "No Nexus data"})
            continue
        t, tot = _trend(work), _totals(work)
        rows.append({"name": p["name"], "employee_id": p["id"], "role": p["role"], "team": p["team"],
                     "tasks_per_day": tot["tasks_per_day"], "on_time_pct": tot["on_time_pct"],
                     "trend": t["trend"], "summary": t["summary"]})
    return rows


@_safe
def team_performance(team=None):
    rows = _team_rows(team)
    if not rows:
        return {"error": f"No employees found for team '{team}'"}
    pick = lambda trend: [{"name": r["name"], "employee_id": r["employee_id"], "team": r["team"],
                           "summary": r["summary"]} for r in rows if r["trend"] == trend]
    return {"team": team or "All teams", "headcount": len(rows),
            "improving": pick("improving"), "declining": pick("declining"),
            "stable": sum(1 for r in rows if r["trend"] == "stable"),
            "no_data": sum(1 for r in rows if r["trend"] is None)}


@_safe
def work_table(team=None):
    """For the Appraisals tab (not an agent tool): Nexus table per person, plus weekly tasks per day for
    the people whose trend changed."""
    rows = _team_rows(team)
    series = {}
    for r in rows:
        if r["trend"] in ("improving", "declining"):
            series[r["name"]] = {w["week"]: w["tasks_per_day"] for w in _nexus_rows(r["employee_id"])}
    return {"rows": rows, "series": series}


@_safe
def project_status(team=None):
    names = {p["id"]: p["name"] for p in _people().list_employees()["employees"]}
    projects = []
    for s in nexus_client.projects():
        if team and str(s.get("team", "")).lower() != team.lower():
            continue
        done = s.get("status") == "done" or (_num(s.get("progress_pct")) or 0) >= 100
        projects.append({"code": s["code"], "title": s["title"], "team": s.get("team"), "due": s["due"],
                         "progress_pct": s.get("progress_pct"), "status": s.get("status"),
                         "members": [names.get(m, m) for m in s.get("members", [])],
                         "overdue": bool(s["due"]) and s["due"] < config.TODAY and not done})
    projects.sort(key=lambda p: (not p["overdue"], p["due"]))
    return {"projects": projects, "overdue_count": sum(1 for p in projects if p["overdue"])}


@_safe
def team_capacity(team=None):
    """Who has room for new work and who is stretched, from Nexus projects and recent output (thresholds in policy)."""
    pol = policy.load()
    cap, targets = pol["capacity"], pol["productivity_target_tasks_per_day"]
    people = _people().list_employees(team)["employees"]
    if not people:
        return {"error": f"No employees found for team '{team}'"}
    projects = nexus_client.projects()
    order = {"available": 0, "balanced": 1, "at capacity": 2, "on leave today": 3, "no Nexus data": 4}
    rows = []
    for p in people:
        mine = [s for s in projects if p["id"] in s.get("members", [])
                and s.get("status") != "done" and (_num(s.get("progress_pct")) or 0) < 100]
        overdue = [s for s in mine if s["due"] and s["due"] < config.TODAY]
        output = _totals(_nexus_rows(p["id"], cap["recent_weeks"]))["tasks_per_day"]
        target = targets["manager" if "manager" in p["role"].lower() else "default"]
        utilisation = round(100 * output / target) if output is not None else None
        if p["status_today"] == "leave":
            status = "on leave today"
        elif len(mine) >= cap["max_active_projects"] or overdue:
            status = "at capacity"
        elif utilisation is None:
            status = "no Nexus data"
        else:
            status = "available" if utilisation < cap["available_below_utilisation_pct"] else "balanced"
        rows.append({"name": p["name"], "employee_id": p["id"], "team": p["team"], "role": p["role"],
                     "status": status, "active_projects": len(mine), "overdue_projects": len(overdue),
                     "projects": [s["code"] for s in mine],
                     "tasks_per_day": None if output is None else round(output, 2),
                     "utilisation_pct": utilisation})
    rows.sort(key=lambda r: (order[r["status"]], r["utilisation_pct"] if r["utilisation_pct"] is not None else 0))
    return {"team": team or "All teams", "people": rows,
            "available": [r["name"] for r in rows if r["status"] == "available"],
            "at_capacity": [r["name"] for r in rows if r["status"] == "at capacity"],
            "rule": f"At capacity: {cap['max_active_projects']}+ active projects or an overdue one. Available: "
                    f"output under {cap['available_below_utilisation_pct']}% of the policy target.",
            "source": nexus_client.source()}


# ---------------- appraisal evidence ----------------

def _score(emp, goals, review, att, rows):
    pol = policy.load()
    weights, rules = pol["rubric_weights"], pol["rubric_rules"]
    credit = rules["no_data_credit"]
    totals, trend = _totals(rows), _trend(rows)
    target = pol["productivity_target_tasks_per_day"]["manager" if "manager" in emp["role"].lower() else "default"]

    reopen = None
    if totals["tasks_per_day"] is not None:
        delivery = weights["delivery"] * min(totals["tasks_per_day"] / target, 1)
        on_time = weights["on_time"] * (totals["on_time_pct"] or 0) / 100
        handled = totals["tasks_done"] + totals["bugs_fixed"]
        reopen = totals["bugs_reopened"] / handled if handled else 0.0
        quality = weights["quality"] * (1 - min(reopen / rules["reopen_rate_cap"], 1))
    else:
        delivery, on_time, quality = (weights[k] * credit for k in ("delivery", "on_time", "quality"))

    if goals:
        average = sum(g["progress_pct"] for g in goals) / len(goals)
        overdue = sum(1 for g in goals if g["overdue"])
        goal_pts = max(weights["goals"] * average / 100 - rules["overdue_goal_penalty"] * overdue, 0)
    else:
        goal_pts = weights["goals"] * credit
    review_pts = weights["review"] * (review["rating"] / 5 if review else credit)
    tier = next(t for t in sorted(rules["attendance_tiers"], key=lambda t: -t["min_pct"])
                if att["attendance_pct"] >= t["min_pct"])
    attendance = weights["attendance"] * tier["credit"]
    adjust = pol["trend_adjustment_points"] * {"improving": 1, "declining": -1, "stable": 0}[trend["trend"]]

    breakdown = {"delivery": delivery, "on_time": on_time, "quality": quality, "goals": goal_pts,
                 "review": review_pts, "attendance": attendance, "trend": adjust}
    score = round(min(max(sum(breakdown.values()), 0), 100))  # whole points, so 64.8 reads (and bands) as 65
    max_points = {**weights, "trend": pol["trend_adjustment_points"]}
    return {"score": score, "breakdown": {k: round(v, 1) for k, v in breakdown.items()},
            "max_points": max_points, "totals": totals, "trend": trend, "reopen_rate": reopen, "target": target}


def _evidence_list(s, goals, review, att, flags):
    t = s["totals"]
    items = []
    if t["tasks_per_day"] is None:
        items.append({"fact": "No Nexus work data in the last 12 weeks", "source": "Nexus"})
    else:
        items += [
            {"fact": f"{t['tasks_per_day']:.2f} tasks per day worked over 12 weeks ({t['tasks_done']} tasks in "
                     f"{t['days_worked']} days; target {s['target']})", "source": "Nexus"},
            {"fact": f"On-time delivery {_fmt(t['on_time_pct'], 0, '%')}", "source": "Nexus"},
            {"fact": f"Reopen rate {s['reopen_rate'] * 100:.1f}% ({t['bugs_reopened']} bugs reopened out of "
                     f"{t['tasks_done'] + t['bugs_fixed']} tasks done and bugs fixed)", "source": "Nexus"},
            {"fact": s["trend"]["summary"], "source": "Nexus"},
        ]
    items.append({"fact": f"Attendance {att['attendance_pct']}% over {att['days']} working days "
                          f"({att['absent']} absent, {att['leave']} on leave, leave not counted against them)",
                  "source": "Perk (attendance)"})
    items += [{"fact": f"{g['title']}: {g['progress_pct']}% ({g['status']})" + (", OVERDUE" if g["overdue"] else ""),
               "source": "Goals"} for g in goals] or [{"fact": "No goals recorded", "source": "Goals"}]
    items.append({"fact": f"{review['cycle']}: rating {review['rating']}/5. {review['notes']}" if review
                  else "No review on record", "source": "Last review"})
    items += [{"fact": f["detail"], "source": "Attention flags"} for f in flags] or [
        {"fact": "No attention flags", "source": "Attention flags"}]
    return items


def _narrative_rules(s, flags, goals):
    strengths, areas = [], []
    for key, pts in s["breakdown"].items():
        if key == "trend":
            continue
        share = pts / s["max_points"][key]
        line = f"- {LABELS[key]}: {pts} of {s['max_points'][key]} points ({SOURCES[key]})"
        if share >= 0.8:
            strengths.append(line)
        elif share <= 0.6:
            areas.append(line)
    talking = [f"- {f['detail']} (Attention flags)" for f in flags]
    talking += [f"- Goal '{g['title']}' is overdue at {g['progress_pct']}% (Goals)" for g in goals if g["overdue"]]
    return "\n".join([
        "**Strengths**", *(strengths or ["- No area is clearly above target."]),
        "**Areas to improve**", *(areas or ["- No area is clearly below target."]),
        "**Trend**", f"- {s['trend']['summary']} (Nexus)",
        "**Talking points**", *(talking or ["- Nothing flagged: recognise the work and ask about next goals."]),
    ])


def _narrative(s, evidence, flags, goals, emp, score, band):
    if not llm.MOCK:
        facts = "\n".join(f"- [{e['source']}] {e['fact']}" for e in evidence)
        try:
            text = llm.complete_text(
                f"Appraisal evidence for {emp['name']} ({emp['role']}). Suggested band: {band}, score {score}/100.\n"
                f"EVIDENCE:\n{facts}\n\nWrite four short markdown sections: Strengths, Areas to improve, Trend, "
                "Talking points. Use ONLY the evidence above, cite the source in brackets after each fact, "
                "never mention money, salary or increments, and do not invent anything.",
                max_tokens=600, model=config.MODEL)
            return text, "llm"
        except llm.LLMError:
            pass
    return _narrative_rules(s, flags, goals), "rules"


@_safe
def appraisal_evidence(name_or_id, cycle="2026-H2", narrative=True, flags=None):
    """narrative=False skips the written summary (no Claude call); flags lets bulk callers pass them in once."""
    people = _people()
    found = people.get_employee(name_or_id)
    if "error" in found:
        return found
    emp, goals, review = found["employee"], found["goals"], found["latest_review"]
    att = people.get_attendance(emp["id"])
    if "error" in att:
        return att
    if flags is None:
        flags = [f for f in people.get_attention_flags(emp["team"])["flags"] if f["employee_id"] == emp["id"]]

    s = _score(emp, goals, review, att, _nexus_rows(emp["id"], 12))
    band = policy.band_for(s["score"])
    evidence = _evidence_list(s, goals, review, att, flags)
    text, source = (_narrative(s, evidence, flags, goals, emp, s["score"], band["name"])
                    if narrative else ("", "skipped"))
    return {
        "employee": emp["name"], "employee_id": emp["id"], "cycle": cycle,
        "team": emp["team"], "role": emp["role"], "join_date": emp["join_date"],
        "nexus_source": nexus_client.source(),
        "last_rating": review["rating"] if review else None,
        "score": s["score"], "band": band["name"], "action": band.get("action"),
        "increment_range": {"min_pct": band["increment_min_pct"], "max_pct": band["increment_max_pct"]},
        "breakdown": s["breakdown"], "max_points": s["max_points"],
        "trend": s["trend"]["trend"], "trend_summary": s["trend"]["summary"],
        "evidence": evidence, "narrative": text, "narrative_source": source,
        "increment_note": f"The band and increment range come from the policy file ({policy.load()['name']}). "
                          "Percentages only; HR approves every increment.",
    }


@_safe
def request_appraisal_approval(name_or_id, cycle, requested_by):
    ev = appraisal_evidence(name_or_id, cycle)
    if "error" in ev:
        return ev
    if any(a["target_id"] == ev["employee_id"] and a["change"].get("cycle") == cycle
           for a in store.list_approvals("pending") if a["kind"] == "appraisal"):
        return {"error": f"An appraisal approval for {ev['employee']} ({cycle}) is already waiting."}

    pct = None
    try:  # increment_recommendations is built in the next step, so guard the import
        from tools import hr_tools
        rec = hr_tools.increment_recommendations(cycle=cycle)
        row = next((r for r in rec.get("rows", []) if r.get("employee_id") == ev["employee_id"]), None)
        pct = row.get("recommended_pct") if row else None
    except Exception:
        pass

    change = {"band": ev["band"], "score": ev["score"], "cycle": cycle, "increment_pct": pct}
    summary = f"{ev['employee']}: {ev['band']}, score {ev['score']} ({cycle})" + (
        f", recommended increment {pct}%" if pct is not None else "")
    approval = store.create_approval("appraisal", ev["employee_id"], change, requested_by, summary)
    return {"approval_id": approval["id"], "status": "pending", "summary": summary}


# ---------------- reports ----------------

def _cell(text):
    return str(text).replace("|", "/").replace("\n", " ")


def _table(header, rows):
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header),
            *("| " + " | ".join(_cell(c) for c in row) + " |" for row in rows)]


@_safe
def appraisal_report(name_or_id, cycle="2026-H2"):
    ev = appraisal_evidence(name_or_id, cycle)
    if "error" in ev:
        return ev
    rng = ev["increment_range"]
    lines = [f"# Appraisal evidence: {ev['employee']} ({cycle})", "",
             f"**Suggested band:** {ev['band']} · **Score:** {ev['score']}/100 · "
             f"**Increment range:** {rng['min_pct']}-{rng['max_pct']}%"
             + (f" · **Action:** {ev['action']}" if ev["action"] else ""), "",
             "## Score breakdown",
             *_table(["Component", "Points", "Max"],
                     [(LABELS[k], v, ev["max_points"][k]) for k, v in ev["breakdown"].items()]), "",
             "## Evidence", *_table(["Source", "Fact"], [(e["source"], e["fact"]) for e in ev["evidence"]]), "",
             "## Narrative", ev["narrative"], "", f"_{ev['increment_note']}_"]
    return {"employee": ev["employee"], "filename": f"appraisal_{ev['employee_id']}_{cycle}.md",
            "report": "\n".join(lines), "evidence": ev}


@_safe
def team_report(team=None):
    rows = _team_rows(team)
    if not rows:
        return {"error": f"No employees found for team '{team}'"}
    projects = project_status(team)
    if "error" in projects:
        return projects
    count = lambda trend: sum(1 for r in rows if r["trend"] == trend)
    lines = [f"# Team work report: {team or 'All teams'} ({config.TODAY})", "",
             f"- Headcount: {len(rows)}",
             f"- Improving: {count('improving')} · Declining: {count('declining')} · Stable: {count('stable')}",
             f"- Overdue projects: {projects['overdue_count']}", "",
             "## Work from Nexus (last 12 weeks)",
             *_table(["Name", "Role", "Tasks/day worked", "On-time %", "Trend"],
                     [(r["name"], r["role"], _fmt(r["tasks_per_day"], 2), _fmt(r["on_time_pct"], 0),
                       r["trend"] or "no data") for r in rows]), "",
             "## Projects (overdue first)",
             *_table(["Code", "Project", "Due", "Progress", "Status", "Members"],
                     [(p["code"], p["title"], p["due"] + (" OVERDUE" if p["overdue"] else ""),
                       f"{p['progress_pct']}%", p["status"], ", ".join(p["members"]))
                      for p in projects["projects"]]), "",
             "_Numbers are read from Nexus; HumaNova does not re-measure them._"]
    return {"team": team or "All teams", "filename": f"team_report_{(team or 'all').lower()}.md",
            "report": "\n".join(lines)}
