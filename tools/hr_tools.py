"""HR decisions on top of the appraisal evidence (owner: Lead): increments, calibration, cycle status, report.

Bands, ranges, budget, eligibility and calibration rules all come from policies/appraisal_policy.json.
Percentages only: there is no salary data here. Every function returns a plain dict and never raises.
"""
import math
from collections import Counter
from datetime import date

import config
import policy
import store
from tools import performance_tools
from tools.performance_tools import _safe, _table


def _people():
    return performance_tools._people()


_CACHE = {}
_NOT_EVIDENCE = ("audit_log.json", "approvals.json", "appraisals.json", "feedback.json", "decisions.json")


def _evidence_all(team, cycle):
    """Evidence for everyone in scope, reused until a source data file or the policy changes."""
    stamp = (store.data_stamp(skip=_NOT_EVIDENCE), policy.POLICY_FILE.stat().st_mtime_ns)
    key = (team, cycle)
    if key not in _CACHE or _CACHE[key][0] != stamp:
        _CACHE[key] = (stamp, _build_evidence(team, cycle))
    return _CACHE[key][1]


def _build_evidence(team, cycle):
    """Flags are fetched once for all, not once per person."""
    people = _people()
    flags = {}
    for f in people.get_attention_flags(team)["flags"]:
        flags.setdefault(f["employee_id"], []).append(f)
    evs = []
    for e in people.list_employees(team)["employees"]:
        ev = performance_tools.appraisal_evidence(e["id"], cycle, narrative=False, flags=flags.get(e["id"], []))
        if "error" not in ev:
            evs.append(ev)
    return evs


def _no_evidence(team):
    return {"error": f"No appraisal evidence found for team '{team}'" if team else "No appraisal evidence found"}


def _floor1(x):
    return math.floor(x * 10 + 1e-9) / 10  # round down to 0.1 so rounding can never push us over budget


# ---------------- increments ----------------

def _service_months(join_date):
    j, t = date.fromisoformat(join_date), date.fromisoformat(config.TODAY)
    return (t.year - j.year) * 12 + t.month - j.month - (1 if t.day < j.day else 0)


def _ineligible_reason(ev, eligibility):
    months = _service_months(ev["join_date"])
    if eligibility.get("exclude_probation") and months < eligibility["probation_months"]:
        return f"Still in probation ({months} months of service; probation is {eligibility['probation_months']} months)"
    if months < eligibility["min_service_months"]:
        return f"Service is {months} months; the policy needs {eligibility['min_service_months']}"
    return None


def _increments(evs, cycle):
    pol = policy.load()
    bands, budget = pol["bands"], pol["increment_budget_pct"]
    rows = []
    for ev in evs:
        i = next(i for i, b in enumerate(bands) if b["name"] == ev["band"])
        band = bands[i]
        low = band["min_score"]
        high = 100 if i == 0 else bands[i - 1]["min_score"] - 1  # a band runs up to the next band's minimum - 1
        position = 1.0 if high <= low else min(max((ev["score"] - low) / (high - low), 0), 1)
        pct = band["increment_min_pct"] + position * (band["increment_max_pct"] - band["increment_min_pct"])
        reason = _ineligible_reason(ev, pol["eligibility"])
        rows.append({"employee_id": ev["employee_id"], "name": ev["employee"], "team": ev["team"],
                     "role": ev["role"], "score": ev["score"], "band": ev["band"],
                     "range_min_pct": band["increment_min_pct"], "range_max_pct": band["increment_max_pct"],
                     "eligible": reason is None, "reason": reason or "",
                     "pct_before_scaling": 0.0 if reason else pct})
    eligible = [r for r in rows if r["eligible"]]
    average_before = sum(r["pct_before_scaling"] for r in eligible) / len(eligible) if eligible else 0.0
    factor = budget / average_before if average_before > budget else 1.0
    for r in rows:
        r["recommended_pct"] = _floor1(r["pct_before_scaling"] * factor)
        r["pct_before_scaling"] = _floor1(r["pct_before_scaling"])
    rows.sort(key=lambda r: (-r["score"], r["name"]))
    for rank, r in enumerate(rows, 1):
        r["rank"] = rank
    eligible = [r for r in rows if r["eligible"]]
    return {
        "cycle": cycle, "budget_pct": budget,
        "average_before": round(average_before, 2),
        "average_after": round(sum(r["recommended_pct"] for r in eligible) / len(eligible), 2) if eligible else 0.0,
        "scaled": factor < 1.0, "scale_factor": round(factor, 3), "rows": rows,
        "top3": [{"name": r["name"], "employee_id": r["employee_id"], "score": r["score"],
                  "recommended_pct": r["recommended_pct"]} for r in eligible[:3]],
        "not_eligible": [{"name": r["name"], "employee_id": r["employee_id"], "reason": r["reason"]}
                         for r in rows if not r["eligible"]],
    }


@_safe
def increment_recommendations(team=None, cycle="2026-H2"):
    evs = _evidence_all(team, cycle)
    return _increments(evs, cycle) if evs else _no_evidence(team)


# ---------------- calibration ----------------

def _calibration(evs, cycle):
    pol = policy.load()
    order = {b["name"]: i for i, b in enumerate(pol["bands"])}  # 0 is the top band
    rating_bands = pol["calibration"]["rating_bands"]
    checks = []
    for ev in evs:
        rating = ev["last_rating"]
        if rating is None:
            continue  # no review (managers, new joiners): nothing to compare
        expected = next((name for name, (low, high) in rating_bands.items() if low <= rating <= high), None)
        if expected is None or expected == ev["band"]:
            continue
        above = order[ev["band"]] < order[expected]
        detail = (f"Evidence says {ev['band']} (score {ev['score']}) but the last rating was {rating}/5 "
                  f"({expected}). ")
        checks.append({"employee": ev["employee"], "employee_id": ev["employee_id"], "team": ev["team"],
                       "rule": "evidence above last rating" if above else "evidence below last rating",
                       "detail": detail + ("Don't carry the old rating forward." if above
                                           else "Discuss support first."),
                       "last_rating": rating, "evidence_band": ev["band"], "score": ev["score"]})
    counts = Counter(ev["band"] for ev in evs)
    distribution = [{"band": b["name"], "count": counts[b["name"]],
                     "share_pct": round(100 * counts[b["name"]] / len(evs), 1)} for b in pol["bands"]]
    top, limit = distribution[0], pol["top_band_max_share"]
    skew = top["share_pct"] / 100 > limit
    if skew:
        checks.append({"employee": None, "rule": "distribution skew",
                       "detail": f"{top['share_pct']}% of people are in '{top['band']}'; "
                                 f"the policy maximum is {round(limit * 100)}%."})
    teams = {}
    for ev in evs:
        teams.setdefault(ev["team"], []).append(ev["score"])
    return {"cycle": cycle, "headcount": len(evs), "distribution": distribution, "checks": checks,
            "skew": skew, "top_band_share_pct": top["share_pct"],
            "team_averages": [{"team": t, "average_score": round(sum(s) / len(s), 1), "headcount": len(s)}
                              for t, s in sorted(teams.items())]}


@_safe
def calibration(team=None, cycle="2026-H2"):
    evs = _evidence_all(team, cycle)
    return _calibration(evs, cycle) if evs else _no_evidence(team)


# ---------------- cycle status ----------------

def _latest_status(emp_id, cycle):
    """Newest of: approvals (waiting / decided) and decided records in appraisals.json."""
    events = [(a["created"], {"pending": "Waiting for HR", "approved": "Approved", "rejected": "Rejected"}[a["status"]])
              for a in store.list_approvals(None)
              if a["kind"] == "appraisal" and a["target_id"] == emp_id and a["change"].get("cycle") == cycle]
    events += [(r["time"], "Approved" if r["status"] == "approved" else "Rejected")
               for r in store.read_json("appraisals.json")
               if r["employee_id"] == emp_id and r.get("cycle") == cycle]
    return max(events)[1] if events else "Not started"


def _status(evs, cycle):
    rows = [{"employee_id": ev["employee_id"], "name": ev["employee"], "team": ev["team"],
             "status": _latest_status(ev["employee_id"], cycle), "band": ev["band"], "score": ev["score"],
             "last_rating": ev["last_rating"], "trend": ev["trend"]} for ev in evs]
    count = Counter(r["status"] for r in rows)
    pct = lambda n: round(100 * n / len(rows), 1)
    return {"cycle": cycle, "headcount": len(rows), "rows": rows,
            "approved": count["Approved"], "rejected": count["Rejected"],
            "waiting": count["Waiting for HR"], "not_started": count["Not started"],
            "approved_pct": pct(count["Approved"]),
            "completion_pct": pct(count["Approved"] + count["Rejected"])}


@_safe
def appraisal_cycle_status(team=None, cycle="2026-H2"):
    evs = _evidence_all(team, cycle)
    return _status(evs, cycle) if evs else _no_evidence(team)


# ---------------- HR report ----------------

@_safe
def people_report(team=None, cycle="2026-H2"):
    evs = _evidence_all(team, cycle)
    if not evs:
        return _no_evidence(team)
    inc, cal, cyc = _increments(evs, cycle), _calibration(evs, cycle), _status(evs, cycle)
    people = _people()
    attendance = [r["attendance_pct"] for r in people.team_overview(team).get("rows", [])
                  if isinstance(r.get("attendance_pct"), (int, float))]
    try:  # Member A's module; the report still works without it
        from tools import hr_reminders
        reminders = hr_reminders.hr_reminders(team).get("reminders", [])
    except Exception:
        reminders = None
    flags = people.get_attention_flags(team)["flags"]

    lines = [f"# People report: {team or 'All teams'} · {cycle} ({config.TODAY})", "", "## Headlines",
             f"- Headcount: {cyc['headcount']}",
             f"- Average attendance: {sum(attendance) / len(attendance):.1f}%" if attendance
             else "- Average attendance: not available",
             f"- Cycle completion: {cyc['completion_pct']}% decided "
             f"({cyc['approved']} approved, {cyc['rejected']} rejected, {cyc['waiting']} waiting for HR, "
             f"{cyc['not_started']} not started)",
             f"- Calibration checks: {len(cal['checks'])}", "",
             "## Appraisal cycle",
             *_table(["Name", "Team", "Status", "Band", "Score", "Last rating", "Trend"],
                     [(r["name"], r["team"], r["status"], r["band"], r["score"],
                       r["last_rating"] if r["last_rating"] is not None else "-", r["trend"]) for r in cyc["rows"]]),
             "", "## Increment recommendations",
             f"- Average {inc['average_after']}% against a budget of {inc['budget_pct']}%"
             + (f" (scaled by {inc['scale_factor']} to fit; before scaling {inc['average_before']}%)"
                if inc["scaled"] else " (within budget)"),
             *_table(["Rank", "Name", "Band", "Range", "Recommended"],
                     [(r["rank"], r["name"], r["band"], f"{r['range_min_pct']}-{r['range_max_pct']}%",
                       f"{r['recommended_pct']}%" if r["eligible"] else f"Not eligible: {r['reason']}")
                      for r in inc["rows"]]),
             "", "## Band distribution",
             *_table(["Band", "People", "Share"],
                     [(d["band"], d["count"], f"{d['share_pct']}%") for d in cal["distribution"]]),
             "", "## Calibration checks",
             *([f"- **{c['employee'] or 'Distribution'}**: {c['rule']}. {c['detail']}" for c in cal["checks"]]
               or ["- None."]),
             "", "## HR reminders",
             *(["- Not available yet."] if reminders is None else
               [f"- **{r['type']}**: {r['name']}, due {r['due']}. {r['detail']}" for r in reminders]
               or ["- None."]),
             "", "## Attention flags",
             *([f"- **{f['employee']}** [{f['rule']}]: {f['detail']}" for f in flags] or ["- None."]),
             "", "_Percentages only. Bands and ranges come from the policy file; HR approves every increment._"]
    return {"team": team or "All teams", "cycle": cycle,
            "filename": f"people_report_{(team or 'all').lower()}_{cycle}.md", "report": "\n".join(lines)}
