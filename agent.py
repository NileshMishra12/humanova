"""HumaNova agent: Claude tool-use loop, demo roles, and code-enforced scope."""
import importlib
import json
import re
from collections import Counter
from datetime import date, timedelta

import config
import llm
import store

MAX_ROUNDS = 6
MAX_HISTORY = 10
LAST_USAGE = {"input_tokens": 0, "output_tokens": 0, "mode": "offline"}  # updated in place by run_agent


# ---- tool definitions (what Claude sees) ----

def _tool(name, description, props=None, required=()):
    return {
        "name": name,
        "description": description,
        "input_schema": {"type": "object", "properties": props or {}, "required": list(required)},
    }


_TEAM = {"team": {"type": "string", "description": "Team name, e.g. Web or Mobile. Omit for all teams."}}
_TEAM_CYCLE = {**_TEAM, "cycle": {"type": "string", "description": "Appraisal cycle, e.g. 2026-H2 (default)."}}
_PERSON = {"name_or_id": {"type": "string", "description": "Employee name or id."}}
_CANDIDATE = {"candidate_id": {"type": "string", "description": "Candidate id."}}
_STAGES = ["Applied", "Shortlisted", "Interview", "Offer", "Hired", "Rejected"]

_DEFS = [
    # employee_tools
    _tool("list_employees", "List employees (id, name, role, team, manager, today's status). Use for 'who is in the team' questions.", _TEAM),
    _tool("get_employee", "Full profile of one person: details, skills, leave balance, goals and latest review. Use for any question about a specific employee.", _PERSON, ["name_or_id"]),
    _tool("who_is_on_leave", "Who is on leave between two dates. Use for 'who is out this week/next week' questions; work out the dates from the calendar in the system prompt.",
          {"start": {"type": "string", "description": "Start date, YYYY-MM-DD."},
           "end": {"type": "string", "description": "End date, YYYY-MM-DD."}, **_TEAM}, ["start", "end"]),
    _tool("get_attendance", "Attendance of one person over the last N days (present, WFH, leave, absent, attendance %).",
          {**_PERSON, "days": {"type": "integer", "description": "Look-back window in days, default 30."}}, ["name_or_id"]),
    _tool("get_attention_flags", "People who may need a supportive conversation: low attendance, overdue goals, low rating. Use for 'who needs attention' questions.", _TEAM),
    _tool("team_overview", "Headcount, who is in or on leave today, flags and a per-person table for a team. Use for team snapshots.", _TEAM),
    _tool("summarise_employee", "Short written summary of one person built from their facts. Use when asked to 'summarise' someone.", _PERSON, ["name_or_id"]),
    # hr_reminders
    _tool("hr_reminders", "Upcoming HR to-dos: probation reviews, wellbeing and workload check-ins, leave balances, increments due.", _TEAM),
    # hiring_tools
    _tool("list_openings", "List open positions with their candidate counts. Start here for any hiring question."),
    _tool("screen_candidates", "Rank the candidates of one opening against its requirements, with matched and missing skills and a suggested next step.",
          {"opening_id": {"type": "string", "description": "Opening id from list_openings."}}, ["opening_id"]),
    _tool("get_candidate", "Details of one candidate (anonymised profile, score, stage).", _CANDIDATE, ["candidate_id"]),
    _tool("request_stage_change", "Recommend moving a candidate to another stage. This does not move them: it creates an approval for a human to decide.",
          {**_CANDIDATE, "stage": {"type": "string", "enum": _STAGES}}, ["candidate_id", "stage"]),
    _tool("draft_email", "Draft an email to a candidate. Only a draft is produced; nothing is sent.",
          {**_CANDIDATE, "kind": {"type": "string", "description": "Kind of email, default 'invite'."}}, ["candidate_id"]),
    _tool("interview_questions", "Five tailored interview questions for a candidate.", _CANDIDATE, ["candidate_id"]),
    # lead tools (performance_tools / hr_tools / kg), skipped until they exist
    _tool("performance_trend", "Rating and goal trend of one person across review cycles.", _PERSON, ["name_or_id"]),
    _tool("team_performance", "Performance summary of a team: ratings, goals and delivery.", _TEAM),
    _tool("project_status", "Project status for a team from Nexus (who is on what, what is overdue). Report it as it is; do not re-judge it.", _TEAM),
    _tool("appraisal_evidence", "Evidence pack for one person's appraisal: goals, ratings, Nexus metrics, attendance. Use before recommending a rating.",
          {**_PERSON, "cycle": {"type": "string", "description": "Appraisal cycle, e.g. 2026-H1. Omit for the current one."}}, ["name_or_id"]),
    _tool("request_appraisal_approval", "Recommend an appraisal outcome for a person. This does not finalise it: it creates an approval for HR to decide.",
          {**_PERSON, "cycle": {"type": "string", "description": "Appraisal cycle, e.g. 2026-H2."},
           "requested_by": {"type": "string", "description": "Name of the person asking (the user)."}},
          ["name_or_id", "cycle", "requested_by"]),
    _tool("appraisal_report", "Downloadable markdown report of one person's appraisal evidence pack.",
          {**_PERSON, "cycle": {"type": "string", "description": "Appraisal cycle, e.g. 2026-H2."}}, ["name_or_id"]),
    _tool("team_report", "Downloadable markdown work report on a team for a manager.", _TEAM),
    _tool("appraisal_cycle_status", "Progress of the appraisal cycle per person (Not started, Waiting for HR, Approved, Rejected) with completion %. Use for 'how is the cycle going' questions.", _TEAM_CYCLE),
    _tool("calibration", "Fairness check: band distribution, people whose evidence band doesn't match their last rating, and distribution skew. Use for calibration, fairness or bias questions.", _TEAM_CYCLE),
    _tool("increment_recommendations", "Ranked increment % per person from the policy, fitted to the budget, with top performers and who is not eligible. Percentages only, never salary amounts; HR approves them. Use for increment, best performer or ranking questions.", _TEAM_CYCLE),
    _tool("people_report", "Full HR markdown report: cycle, increments, band distribution, calibration, reminders and flags. Use when HR asks for the people or HR report.", _TEAM_CYCLE),
    _tool("graph_search", "Search the knowledge graph of people, skills, projects and teams.",
          {"term": {"type": "string", "description": "A skill, person, team, project or opening to look up."}}, ["term"]),
]

_MODULES = ("tools.employee_tools", "tools.hr_reminders", "tools.hiring_tools",
            "tools.performance_tools", "tools.hr_tools", "kg")
IMPORT_ERRORS = {}


def _load_funcs():
    funcs = {}
    for modname in _MODULES:
        try:
            mod = importlib.import_module(modname)
        except Exception as e:  # module not written yet, or broken: app still starts
            IMPORT_ERRORS[modname] = f"{type(e).__name__}: {e}"
            continue
        for d in _DEFS:
            fn = getattr(mod, d["name"], None)
            if callable(fn):
                funcs.setdefault(d["name"], fn)
    return funcs


FUNCS = _load_funcs()
try:  # screening learns from recruiter decisions once learning.py is there
    import learning
    if "screen_candidates" in FUNCS:
        FUNCS["screen_candidates"] = learning.screen_with_learning
except Exception as e:
    learning = None
    IMPORT_ERRORS["learning"] = f"{type(e).__name__}: {e}"
TOOLS = [d for d in _DEFS if d["name"] in FUNCS]


# ---- roles ----

# Later prompts add their tools to these sets as they register them.
_EMPLOYEE = {"list_employees", "get_employee", "who_is_on_leave", "get_attendance", "get_attention_flags",
             "team_overview", "summarise_employee", "hr_reminders",
             "performance_trend", "team_performance", "project_status", "appraisal_evidence",
             "request_appraisal_approval", "appraisal_report", "team_report",
             "appraisal_cycle_status", "calibration", "increment_recommendations", "graph_search"}
_HIRING_READ = {"list_openings", "screen_candidates", "get_candidate"}
_HIRING_ALL = _HIRING_READ | {"request_stage_change", "draft_email", "interview_questions"}


def _role(label, team, scope, tools):
    return {"label": label, "team": team, "scope": scope, "tools": set(tools)}


def _manager(name, team):
    return _role(
        f"Manager - {name} ({team})", team,
        f"You are talking to {name}, manager of the {team} team. You may see only {team} team members "
        "and read-only hiring data (openings, screening, candidates).",
        _EMPLOYEE | _HIRING_READ)


ROLES = {r["label"]: r for r in (
    _manager("Anita Desai", "Web"),
    _manager("Vikram Rao", "Mobile"),
    _role("Recruiter - Talent Team", None,
          "You are talking to the Talent Team recruiter. You handle hiring only and have no access to employee data.",
          _HIRING_ALL | {"graph_search"}),
    _role("HR Admin", None,
          "You are talking to an HR Admin. You can see every team and use every tool.",
          _EMPLOYEE | _HIRING_ALL | {"people_report"}),
)}


def allowed(role, name):
    return name in role["tools"]


def can_decide(role, kind):
    """Who may approve or reject: hiring approvals by recruiter or HR Admin, everything else by HR Admin."""
    if kind == "candidate_stage":
        return role["label"] == "HR Admin" or role["label"].startswith("Recruiter")
    return role["label"] == "HR Admin"


# ---- scope enforcement (in code, not just the prompt) ----

# Later prompts append their tools to these tuples.
TEAM_TOOLS = ("list_employees", "who_is_on_leave", "get_attention_flags", "team_overview", "hr_reminders",
              "team_performance", "project_status", "team_report",
              "appraisal_cycle_status", "calibration", "increment_recommendations", "people_report")
PERSON_TOOLS = ("get_employee", "get_attendance", "summarise_employee", "performance_trend",
                "appraisal_evidence", "request_appraisal_approval", "appraisal_report")


def _scope(name, args, role):
    """Force the role's team into the team argument of team tools."""
    if role["team"] and name in TEAM_TOOLS:
        args["team"] = role["team"]
    return args


def _out_of_scope(name, args, role):
    """Error dict if the person asked about is in another team, else None."""
    if not role["team"] or name not in PERSON_TOOLS:
        return None
    who = args.get("name_or_id")
    if not who:
        return None  # the tool itself reports the missing argument
    get = FUNCS.get("get_employee")
    if get is None:
        return {"error": f"Can't check whether {who} is in your team ({role['team']}), so I can't look them up."}
    found = get(who)
    if not isinstance(found, dict) or "error" in found:
        return found if isinstance(found, dict) else {"error": f"Could not find {who}."}
    emp = found["employee"]
    if str(emp.get("team", "")).lower() != role["team"].lower():
        return {"error": f"{emp['name']} is not in your team ({role['team']}). "
                         f"You can only access {role['team']} team members."}
    return None


def use_tool(role, name, **args):
    """Run a tool for a role from the UI: permission, team scope and audit, same as the agent."""
    return _run(name, args, role, []) or {"error": f"{name} isn't available yet."}


def call_tool(name, args, user):
    """Run a tool and audit it. Never raises; failures come back as {"error": ...}."""
    args = dict(args or {})
    try:
        if name not in FUNCS:
            raise LookupError(f"Tool '{name}' is not available")
        result = FUNCS[name](**args)
        if not isinstance(result, dict):
            raise TypeError(f"{name} returned {type(result).__name__}, expected a dict")
    except Exception as e:
        result = {"error": f"{name} failed: {e}"}
    store.audit(user, name, args, "error" if "error" in result else "ok")
    return result


# ---- prompt ----

def system_prompt(role):
    today = date.fromisoformat(config.TODAY)
    monday = today - timedelta(days=today.weekday())

    def week(start):
        return f"{start.isoformat()} to {(start + timedelta(days=4)).isoformat()}"

    notes = learning.corrections() if learning else []
    corrections = ("\n\nCorrections from users (follow them):\n" + "\n".join(f"- {n}" for n in notes)) if notes else ""
    return f"""You are HumaNova, the company's HR agent.
Today is {today.strftime('%A')} {today.isoformat()}.
This week (Mon-Fri): {week(monday)}. Next week (Mon-Fri): {week(monday + timedelta(days=7))}.
{role['scope']}

Rules:
- Use tools for every fact. Never invent names, numbers or dates. If a tool returns an error, say so plainly.
- Stage changes and appraisals are recommendations: they go to Approvals, a human decides. Never say they are done.
- Emails are drafts only. Nothing is sent.
- Attention flags are prompts for a supportive conversation, not judgements about a person.
- Increments are a % from the policy and HR approves them. Never give salary amounts.
- Work metrics come from Nexus. Report them as they are; don't re-judge them.
- If a request is outside your role's access, say so briefly.
- Answer in short bullets with the numbers.{corrections}"""


# ---- running the agent ----

# ---- offline agent: keyword routing to the same tools, no AI ----

SHOW = 10  # rows listed per answer; the rest becomes "...and N more", so big datasets stay readable
NOT_YET = "That part isn't available yet: it needs a tool that hasn't been added."


def _has(q, *terms):
    """True if any term starts at a word boundary in the lower-cased question."""
    return any(re.search(r"\b" + re.escape(t), q) for t in terms)


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _run(name, args, role, trace):
    """Run a tool the way the live loop does. None when the tool doesn't exist yet."""
    if name not in FUNCS:
        return None
    if not allowed(role, name):
        return {"error": f"Your role ({role['label']}) can't do that. Try something within your access.",
                "denied": True}
    want = args.get("team")
    if role["team"] and name in TEAM_TOOLS and want and want.lower() != role["team"].lower():
        return {"error": f"You can only see the {role['team']} team.", "denied": True}
    args = _scope(name, dict(args), role)
    trace.append({"tool": name, "args": args})
    return _out_of_scope(name, args, role) or call_tool(name, args, role["label"])


# -- formatting: short markdown bullets with the numbers --

def _inline(item):
    if not isinstance(item, dict):
        return str(item)
    first = next((k for k in ("name", "employee", "title") if k in item), None)
    head = [str(item[first])] if first else []
    rest = [f"{k.replace('_', ' ')} {v}" for k, v in item.items()
            if k != first and isinstance(v, (str, int, float, bool)) and v != ""]
    return " · ".join((head + rest)[:7])


def _list(items, fmt):
    lines = [f"- {fmt(i)}" for i in items[:SHOW]]
    if len(items) > SHOW:
        lines.append(f"- …and {len(items) - SHOW} more")
    return lines


def _bullets(data, indent=""):
    """Generic view of any tool result (used where the shape isn't special-cased)."""
    lines = []
    for key, val in data.items():
        label = key.replace("_", " ")
        if isinstance(val, dict):
            lines.append(f"{indent}- **{label}**:")
            lines += _bullets(val, indent + "  ")
        elif isinstance(val, list):
            lines.append(f"{indent}- **{label}** ({len(val)})" + (":" if val else ""))
            lines += [f"{indent}  - {_inline(v)}" for v in val[:SHOW]]
            if len(val) > SHOW:
                lines.append(f"{indent}  - …and {len(val) - SHOW} more")
        elif isinstance(val, str) and "\n" in val:
            lines.append(val)  # a markdown report
        elif val not in (None, ""):
            lines.append(f"{indent}- **{label}**: {val}")
    return lines


def _section(title, res, body=_bullets):
    if res is None:
        return None
    lines = [f"**{title}**"]
    lines += [f"- ⚠️ {res['error']}"] if "error" in res else body(res)
    return "\n".join(lines)


def _show(title, res, body=_bullets):
    return _section(title, res, body) or NOT_YET


def _leave_lines(res):
    rows = sorted(res.get("on_leave", []), key=lambda r: str(r.get("from", "")))
    if not rows:
        return ["- Nobody is on leave in this period."]
    teams = Counter(r.get("team", "?") for r in rows)
    lines = [f"- **{len(rows)}** on leave ({', '.join(f'{t} {n}' for t, n in teams.most_common())})"]
    return lines + _list(rows, lambda r: f"{r.get('name')} ({r.get('team')}): {r.get('type')}, "
                                         f"{r.get('from')} → {r.get('to')}")


def _flag_lines(res):
    flags = res.get("flags", [])
    if not flags:
        return ["- Nobody is flagged right now."]
    rules = Counter(f.get("rule") for f in flags)
    lines = [f"- **{len(flags)}** flags ({', '.join(f'{r} {n}' for r, n in rules.most_common())})"]
    lines += _list(flags, lambda f: f"{f.get('employee')} [{f.get('rule')}]: {f.get('detail')}")
    return lines + ["- These are prompts for a supportive conversation, not judgements."]


def _checkin_lines(res):
    flags = res.get("flags", [])
    if not flags:
        return ["- No check-ins needed."]
    return [f"- **{len(flags)}** supportive 1:1s to book"] + _list(
        flags, lambda f: f"{f.get('employee')}: {f.get('detail')} ({f.get('rule')})")


def _reminder_lines(res):
    items = res.get("reminders", [])
    if not items:
        return ["- No reminders due."]
    by_type = {}
    for r in items:
        by_type.setdefault(r.get("type", "other"), []).append(r)
    lines = [f"- **{len(items)}** reminders ({', '.join(f'{t} {len(v)}' for t, v in by_type.items())})"]
    for kind, rows in by_type.items():
        rows = sorted(rows, key=lambda r: str(r.get("due", "")))
        lines.append(f"- **{kind}**")
        lines += [f"  - {r.get('name')}: {r.get('detail')} (due {r.get('due')})" for r in rows[:5]]
        if len(rows) > 5:
            lines.append(f"  - …and {len(rows) - 5} more")
    return lines


def _candidate_lines(res):
    ranked = res.get("ranked", [])
    if not ranked:
        return ["- No candidates yet."]
    steps = Counter(c.get("next_step") for c in ranked)
    lines = [f"- **{len(ranked)}** candidates ({', '.join(f'{s} {n}' for s, n in steps.most_common())})"]
    return lines + _list(ranked, lambda c: (
        f"{c.get('name')} ({c.get('candidate_id')}): score {c.get('score')}, {c.get('years_experience')} yrs, "
        f"matched {', '.join(map(str, c.get('matched') or [])) or 'none'}, "
        f"missing {', '.join(map(str, c.get('missing') or [])) or 'none'} → {c.get('next_step')}"
        + (f" [{c['learned_note']}]" if c.get("learned_note") else "")))


def _interview_lines(res):
    picks = [c for c in res.get("ranked", []) if str(c.get("next_step")).lower() == "interview"]
    if not picks:
        return ["- Nobody is ready for interview yet."]
    return [f"- **{len(picks)}** interviews to schedule"] + _list(
        picks, lambda c: f"{c.get('name')} ({c.get('candidate_id')}): score {c.get('score')}")


def _overview_lines(res):
    lines = [f"- Headcount **{res.get('headcount')}** · present today {res.get('present_today')} · "
             f"on leave {res.get('on_leave_today')} · flagged {res.get('flagged')}"]
    rows = res.get("rows", [])
    low = sorted((r for r in rows if _num(r.get("attendance_pct")) is not None),
                 key=lambda r: _num(r["attendance_pct"]))[:5]
    if low:
        lines.append("- Lowest attendance: " + "; ".join(f"{r.get('name')} {r['attendance_pct']}%" for r in low))
    over = sorted((r for r in rows if (_num(r.get("goals_overdue")) or 0) > 0),
                  key=lambda r: -_num(r["goals_overdue"]))[:5]
    if over:
        lines.append("- Most overdue goals: " + "; ".join(f"{r.get('name')} {r['goals_overdue']}" for r in over))
    rated = sorted((r for r in rows if _num(r.get("latest_rating")) is not None),
                   key=lambda r: _num(r["latest_rating"]))[:5]
    if rated:
        lines.append("- Lowest latest rating: " + "; ".join(f"{r.get('name')} {r['latest_rating']}" for r in rated))
    return lines


def _employee_lines(res):
    e, goals, review = res.get("employee", {}), res.get("goals", []), res.get("latest_review")
    bal = e.get("leave_balance") or {}
    lines = [
        f"- **{e.get('name')}** ({e.get('id')}), {e.get('role')}, {e.get('team')} team · "
        f"{e.get('location')} · joined {e.get('join_date')}",
        f"- Today: {e.get('status_today')} · skills: {', '.join(map(str, e.get('skills') or [])) or 'none'}",
        f"- Leave balance: casual {bal.get('casual')}, sick {bal.get('sick')}, earned {bal.get('earned')}",
        f"- Goals: {len(goals)} total, {sum(1 for g in goals if g.get('overdue'))} overdue",
    ]
    lines += [f"  - {g.get('title')}: {g.get('progress_pct')}% ({g.get('status')})"
              + (" OVERDUE" if g.get("overdue") else "") for g in goals[:SHOW]]
    lines.append(f"- Latest review: rating {review.get('rating')} ({review.get('cycle')}): {review.get('notes')}"
                 if review else "- No review on file.")
    return lines


def _attendance_lines(res):
    return [f"- {res.get('employee')}, last {res.get('days')} days: present {res.get('present')}, "
            f"WFH {res.get('wfh')}, leave {res.get('leave')}, absent {res.get('absent')} "
            f"→ **{res.get('attendance_pct')}%**"]


def _summary_lines(res):
    return [str(res.get("summary", "")), f"- Source: {res.get('source')}"]


def _email_lines(res):
    return [f"- **Subject:** {res.get('subject')}", "", str(res.get("body", "")), "", f"- {res.get('status')}"]


def _questions_lines(res):
    return [f"{i}. {q}" for i, q in enumerate(res.get("questions", []), 1)]


def _evidence_lines(res):
    rng = res.get("increment_range", {})
    lines = [f"- **{res.get('band')}**, score **{res.get('score')}/100** · increment range "
             f"{rng.get('min_pct')}-{rng.get('max_pct')}% · trend {res.get('trend')}"]
    if res.get("action"):
        lines.append(f"- Action: {res['action']}")
    lines.append("- Breakdown: " + ", ".join(f"{k.replace('_', ' ')} {v}/{res['max_points'][k]}"
                                             for k, v in res.get("breakdown", {}).items()))
    lines += ["", "**Evidence**"] + [f"- [{e['source']}] {e['fact']}" for e in res.get("evidence", [])]
    return lines + ["", str(res.get("narrative", "")), "", f"_{res.get('increment_note')}_"]


def _increment_lines(res):
    scaled = f" · scaled to fit (before {res.get('average_before')}%)" if res.get("scaled") else " · within budget"
    lines = [f"- Average **{res.get('average_after')}%** against a budget of {res.get('budget_pct')}%{scaled}"]
    top = res.get("top3", [])
    if top:
        lines.append("- Top performers: " + "; ".join(
            f"{t['name']} (score {t['score']}, {t['recommended_pct']}%)" for t in top))
    rows = [r for r in res.get("rows", []) if r.get("eligible")]
    lines += _list(rows, lambda r: f"#{r['rank']} {r['name']}: score {r['score']}, {r['band']}, "
                                   f"range {r['range_min_pct']}-{r['range_max_pct']}% → **{r['recommended_pct']}%**")
    out = res.get("not_eligible", [])
    if out:
        lines.append(f"- **{len(out)}** not eligible:")
        lines += [f"  - {o['name']}: {o['reason']}" for o in out[:SHOW]]
    return lines + ["- Percentages only; HR approves every increment."]


def _calibration_lines(res):
    dist = ", ".join(f"{d['band']} {d['count']} ({d['share_pct']}%)" for d in res.get("distribution", []))
    lines = [f"- Band distribution: {dist}"]
    checks = res.get("checks", [])
    lines.append(f"- **{len(checks)}** calibration check(s)" + (":" if checks else "."))
    lines += [f"  - {c.get('employee') or 'Distribution'} [{c['rule']}]: {c['detail']}" for c in checks[:SHOW]]
    if len(checks) > SHOW:
        lines.append(f"  - …and {len(checks) - SHOW} more")
    teams = ", ".join(f"{t['team']} {t['average_score']}" for t in res.get("team_averages", []))
    return lines + ([f"- Average score by team: {teams}"] if teams else [])


def _cycle_lines(res):
    lines = [f"- Cycle {res.get('cycle')}: **{res.get('completion_pct')}%** decided · approved "
             f"{res.get('approved')} · rejected {res.get('rejected')} · waiting for HR {res.get('waiting')} "
             f"· not started {res.get('not_started')}"]
    rows = sorted(res.get("rows", []), key=lambda r: (r["status"] == "Not started", r["status"]))
    return lines + _list(rows, lambda r: f"{r['name']}: {r['status']} ({r['band']}, score {r['score']}, "
                                         f"last rating {r.get('last_rating') or '-'}, trend {r.get('trend')})")


def _trend_lines(res):
    rows = res.get("weeks", [])
    tasks = ", ".join("-" if r.get("tasks_per_day") is None else str(r["tasks_per_day"]) for r in rows)
    on_time = ", ".join("-" if r.get("on_time_pct") is None else f"{r['on_time_pct']:.0f}" for r in rows)
    return [f"- **{res.get('trend')}**: {res.get('summary')}",
            f"- Tasks per day worked, by week: {tasks}", f"- On-time %, by week: {on_time}"]


def _graph_lines(res):
    if res.get("kind") == "skill":
        lines = [f"- Skill: **{res.get('skill')}**"]
        sections = [("employees", "Employees who know it", lambda x: x["name"]),
                    ("candidates", "Candidates with it", lambda x: x["name"]),
                    ("openings", "Openings", lambda x: f"{x['opening']} ({x['relation']})"),
                    ("decisions", "Recruiter decisions citing it", lambda x: x["detail"]),
                    ("projects", "Projects", lambda x: f"{x['project']}: {', '.join(x['people_who_know_it'])}")]
        for key, label, fmt in sections:
            items = res.get(key, [])
            lines.append(f"- **{label}** ({len(items)})" + (":" if items else ""))
            lines += [f"  - {fmt(i)}" for i in items[:SHOW]]
            if len(items) > SHOW:
                lines.append(f"  - …and {len(items) - SHOW} more")
        return lines
    lines = []
    for m in res.get("matches", []):
        lines.append(f"- **{m['label']}** ({m['type']}): {m['detail']}")
        lines += [f"  - {c['relation']} {'→' if c['direction'] == 'out' else '←'} {c['label']} ({c['type']})"
                  for c in m["connections"][:SHOW]]
    return lines or _bullets(res)


def _report_lines(res):
    return [res["report"]] if "report" in res else _bullets(res)


def _stage_lines(res):
    return [f"- {res.get('summary')}",
            f"- Approval **{res.get('approval_id')}** is {res.get('status')}: a person decides in the Approvals tab."]


# -- lookups --

def _employees():
    """Everyone, unscoped: used only to spot names and teams in the question (tools still enforce scope)."""
    fn = FUNCS.get("list_employees")
    try:
        res = fn() if fn else {}
    except Exception:
        return []
    return res.get("employees", []) if isinstance(res, dict) else []


def _find_people(q, emps):
    """Employees named in the question: full name or id first, else first name."""
    named = [e for e in emps if e.get("name") and (
        e["name"].lower() in q or re.search(r"\b" + re.escape(str(e.get("id")).lower()) + r"\b", q))]
    if named:
        return named
    return [e for e in emps if e.get("name")
            and re.search(r"\b" + re.escape(e["name"].split()[0].lower()) + r"\b", q)]


def _which(people):
    return "**Which one?** " + ", ".join(f"{p['name']} ({p['id']})" for p in people[:5])


def _team_arg(q, emps):
    for team in sorted({e.get("team") for e in emps if e.get("team")}, key=len, reverse=True):
        if re.search(r"\b" + re.escape(team.lower()) + r"\b", q):
            return {"team": team}
    return {}


def _leave_range(q):
    today = date.fromisoformat(config.TODAY)
    if _has(q, "today"):
        return today, today, "today"
    monday = today - timedelta(days=today.weekday())
    label = "this week"
    if "next week" in q:
        monday += timedelta(days=7)
        label = "next week"
    return monday, monday + timedelta(days=4), label


def _opening_id(q):
    m = re.search(r"\bop\d+\b", q)
    if m:
        return m.group(0).upper()
    fn = FUNCS.get("list_openings")
    try:
        openings = fn().get("openings", []) if fn else []
    except Exception:
        openings = []
    for o in openings:
        if str(o.get("title", "")).lower() in q:
            return o["id"]
    return "OP1"


def _skill_in(question):
    m = re.search(r"(?:knows?|interview|expert(?:s)?(?:\s+(?:in|on|for|at))?)\s+(?:(?:for|a|an|the)\s+)*"
                  r"([\w.+#/-]+)", question, re.I)
    return m.group(1) if m else re.findall(r"[\w.+#/-]+", question)[-1]


def _stage(q):
    m = re.search(r"\bto\s+(\w+)", q)
    word = m.group(1) if m else ""
    for s in _STAGES:
        low = s.lower()
        if word and (word == low or (len(word) >= 4 and low.startswith(word))):
            return s
    if _has(q, "reject"):
        return "Rejected"
    return next((s for s in _STAGES if re.search(r"\b" + s.lower() + r"\b", q)), None)


def _find_row(res, emp):
    keys = {str(emp.get("id")).lower(), str(emp.get("name")).lower()}
    for val in res.values():
        if isinstance(val, list):
            for row in val:
                if isinstance(row, dict) and any(
                        str(v).lower() in keys for v in row.values() if isinstance(v, (str, int))):
                    return row
    return None


# -- routes --

def _interviewer(run, screen):
    """Skill to find an interviewer for, with its graph result: a required skill that many candidates are
    missing and that someone here knows (so it names an actual interviewer)."""
    missing = Counter(s for c in screen.get("ranked", []) for s in c.get("missing") or [])
    fallback = None
    for skill, _ in missing.most_common(6):
        found = run("graph_search", term=skill)
        if found is None or "error" in found or not found.get("employees"):
            continue
        if any(o.get("relation") == "requires" and o.get("opening") == screen.get("opening")
               for o in found.get("openings", [])):
            return skill, found
        fallback = fallback or (skill, found)
    return fallback or ("RxJS", run("graph_search", term="RxJS"))


def _plan(q, run, team):
    today = date.fromisoformat(config.TODAY)
    monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    start, end = monday.isoformat(), (monday + timedelta(days=4)).isoformat()
    leave = run("who_is_on_leave", start=start, end=end, **team)
    flags = run("get_attention_flags", **team)
    screen = run("screen_candidates", opening_id=_opening_id(q))
    skill, graph = _interviewer(run, screen or {})

    parts = [
        _section(f"Who is out next week ({start} to {end})", leave, _leave_lines),
        _section("Check-ins to book", flags, _checkin_lines),
        _section("Interviews to schedule", screen, _interview_lines),
        _section(f"Suggested interviewer (who knows {skill})", graph, _graph_lines),
    ]
    results = [leave, flags, screen, graph]
    parts = [p for p, r in zip(parts, results) if p and not (r or {}).get("denied")]
    if parts:
        return "**Plan for next week**\n\n" + "\n\n".join(parts)
    denied = next((r for r in results if r and r.get("denied")), None)
    return denied["error"] if denied else NOT_YET


def _candidate_action(q, run, cand):
    if cand:
        cid = cand.group(0).upper()
    else:
        found = run("screen_candidates", opening_id=_opening_id(q))
        if found is None or "error" in found:
            return _show("Top candidate", found)
        if not found.get("ranked"):
            return "- No candidates to pick from yet."
        cid = found["ranked"][0]["candidate_id"]
    if _has(q, "move") or (_has(q, "reject") and not _has(q, "email")):
        stage = _stage(q)
        if not stage:
            return f"**Which stage?** {', '.join(_STAGES)}"
        return _show(f"Stage change for {cid}", run("request_stage_change", candidate_id=cid, stage=stage),
                     _stage_lines)
    if _has(q, "email", "invite"):
        kind = "reject" if _has(q, "reject") else "invite"
        return _show(f"Draft {kind} email for {cid}", run("draft_email", candidate_id=cid, kind=kind),
                     _email_lines)
    return _show(f"Interview questions for {cid}", run("interview_questions", candidate_id=cid),
                 _questions_lines)


def _help(role):
    topics = []
    if allowed(role, "get_employee"):
        topics += ["who is on leave this week", "who needs attention", "a team overview",
                   "a person's summary, attendance or profile", "HR reminders"]
    if allowed(role, "screen_candidates"):
        topics += ["screen candidates", "an invite email or interview questions for the top candidate"]
    topics = topics or ["nothing is enabled for this role yet"]
    return ("**Here's what I can help with**\n" + "\n".join(f"- {t}" for t in topics)
            + "\n- (Offline mode: I match keywords, so short, direct questions work best.)")


def _answer(question, role, trace):
    q = question.lower()
    emps = _employees()
    people = _find_people(q, emps)
    team = _team_arg(q, emps)

    def run(name, **args):
        return _run(name, args, role, trace)

    if _has(q, "plan") and _has(q, "monday", "week", "day"):
        return _plan(q, run, team)
    if _has(q, "calibrat", "fair", "bias"):
        return _show("Calibration check", run("calibration", **team), _calibration_lines)
    if _has(q, "reminder", "probation", "wellbeing", "due"):
        return _show("HR reminders", run("hr_reminders", **team), _reminder_lines)
    if _has(q, "cycle", "appraisal status", "completion"):
        return _show("Appraisal cycle status", run("appraisal_cycle_status", **team), _cycle_lines)
    if _has(q, "people report", "hr report"):
        return _show("People report", run("people_report", **team), _report_lines)
    if _has(q, "report"):
        return _show("Team report", run("team_report", **team), _report_lines)
    if _has(q, "increment", "best perform", "top perform", "ranking") and not people:
        return _show("Increment recommendations", run("increment_recommendations", **team), _increment_lines)
    if people and (_has(q, "appraisal", "evidence", "increment", "approval") or (_has(q, "send") and _has(q, "hr"))):
        if len(people) > 1:
            return _which(people)
        emp = people[0]
        if _has(q, "send", "approval"):
            m = re.search(r"20\d\d-h[12]", q)
            return _show(f"Send {emp['name']} to HR for approval",
                         run("request_appraisal_approval", name_or_id=emp["id"],
                             cycle=m.group(0).upper() if m else "2026-H2", requested_by=role["label"]),
                         lambda r: _bullets(r) + ["- Waiting in Approvals: HR decides."])
        evidence = run("appraisal_evidence", name_or_id=emp["id"])
        text = _show(f"Appraisal evidence: {emp['name']}", evidence, _evidence_lines)
        if evidence and "error" not in evidence:
            # company-wide, so the budget scaling matches the HR tab
            inc = run("increment_recommendations")
            row = _find_row(inc, emp) if inc and "error" not in inc else None
            if row:
                text += f"\n- **Recommended increment**: {_inline(row)}"
        return text
    if _has(q, "sow", "project"):
        return _show("Project status (Nexus)", run("project_status", **team))
    if _has(q, "improv", "declin", "trend", "performance"):
        if len(people) > 1:
            return _which(people)
        if people:
            return _show(f"Performance trend: {people[0]['name']}",
                         run("performance_trend", name_or_id=people[0]["id"]), _trend_lines)
        return _show("Team performance", run("team_performance", **team))
    if _has(q, "who knows", "who can interview", "expert"):
        skill = _skill_in(question)
        return _show(f"Who knows {skill}", run("graph_search", term=skill), _graph_lines)
    if _has(q, "leave") and _has(q, "who"):
        start, end, label = _leave_range(q)
        return _show(f"Who is on leave {label} ({start} to {end})",
                     run("who_is_on_leave", start=start.isoformat(), end=end.isoformat(), **team), _leave_lines)
    if _has(q, "attention", "flag", "concern"):
        return _show("Who may need a supportive conversation", run("get_attention_flags", **team), _flag_lines)

    cand = re.search(r"\bc\d+\b", q)
    if (cand or _has(q, "top candidate", "best candidate")) and _has(
            q, "move", "email", "invite", "question", "reject"):
        return _candidate_action(q, run, cand)
    if cand:
        return _show(f"Candidate {cand.group(0).upper()}", run("get_candidate", candidate_id=cand.group(0).upper()))
    if _has(q, "screen", "shortlist", "candidate"):
        opening = _opening_id(q)
        return _show(f"Candidates for {opening}", run("screen_candidates", opening_id=opening), _candidate_lines)

    if people:
        if len(people) > 1:
            return _which(people)
        emp = people[0]
        if _has(q, "1:1", "summar", "prep"):
            return _show(f"Summary: {emp['name']}", run("summarise_employee", name_or_id=emp["id"]), _summary_lines)
        if _has(q, "attendance"):
            m = re.search(r"(\d+)\s*days", q)
            days = {"days": int(m.group(1))} if m else {}
            return _show(f"Attendance: {emp['name']}", run("get_attendance", name_or_id=emp["id"], **days),
                         _attendance_lines)
        return _show(emp["name"], run("get_employee", name_or_id=emp["id"]), _employee_lines)
    if _has(q, "team", "overview", "headcount"):
        return _show("Team overview", run("team_overview", **team), _overview_lines)
    return _help(role)


def mock_agent(question, role):
    """Offline agent: keyword routing to the same tools. Returns (answer, trace) like run_agent."""
    LAST_USAGE.update(input_tokens=0, output_tokens=0, mode="offline")
    trace = []
    try:
        answer = _answer(question, role, trace)
    except Exception as e:  # the offline path must never take the app down
        answer = f"Offline mode hit a problem: {type(e).__name__}: {e}"
    return answer, trace


def daily_digest(role):
    """Short markdown digest built from the same tools; in live mode Claude writes it from those facts."""
    today = date.fromisoformat(config.TODAY)
    monday = today - timedelta(days=today.weekday()) + timedelta(days=7)
    start, end = monday.isoformat(), (monday + timedelta(days=4)).isoformat()
    trace = []

    def run(name, **args):
        return _run(name, args, role, trace)

    parts = []

    def add(title, res, body=_bullets):
        if res is not None and not res.get("denied"):  # skip tools that are missing or off-limits for this role
            parts.append(_section(title, res, body))

    add("On leave today", run("who_is_on_leave", start=today.isoformat(), end=today.isoformat()), _leave_lines)
    add(f"On leave next week ({start} to {end})", run("who_is_on_leave", start=start, end=end), _leave_lines)
    add("Attention flags", run("get_attention_flags"), _flag_lines)
    add("HR reminders", run("hr_reminders"), _reminder_lines)

    listing = run("list_openings")
    openings = (listing or {}).get("openings") or [{"id": "OP1"}]
    lines = []
    for o in openings[:5]:
        res = run("screen_candidates", opening_id=o["id"])
        if res is None or res.get("denied") or "error" in res:
            continue
        picks = [c for c in res.get("ranked", []) if str(c.get("next_step")).lower() == "interview"]
        if picks:
            more = f" …and {len(picks) - 3} more" if len(picks) > 3 else ""
            lines.append(f"- **{o.get('title', o['id'])}** ({o['id']}): "
                         + ", ".join(f"{c.get('name')} ({c.get('score')})" for c in picks[:3]) + more)
    if lines:
        parts.append("**Top interview candidates**\n" + "\n".join(lines))

    if can_decide(role, "appraisal") or can_decide(role, "candidate_stage"):
        pending = [a for a in store.list_approvals() if can_decide(role, a["kind"])]
        body = _list(pending, lambda a: f"{a['summary']} (requested by {a['requested_by']})") or ["- Nothing waiting."]
        parts.append(f"**Pending approvals ({len(pending)})**\n" + "\n".join(body))

    heading = f"## ☀️ Daily digest · {today.strftime('%A %d %b %Y')}"
    if not parts:
        return f"{heading}\n\nNothing to report for your role."
    facts = "\n\n".join(parts)
    if not llm.MOCK:
        try:
            return llm.complete_text(
                f"Write today's HR daily digest as short markdown sections, starting with the heading "
                f"'{heading}'. Use only these facts, keep every name and number, and keep the tone supportive.\n\n"
                f"FACTS:\n{facts}",
                system=system_prompt(role), max_tokens=900, model=config.MODEL)
        except llm.LLMError:
            pass
    return f"{heading}\n\n{facts}"


def _last_user_text(history):
    for m in reversed(history):
        if m.get("role") == "user" and isinstance(m.get("content"), str):
            return m["content"]
    return ""


def _log_chat(role, history, trace, status):
    store.audit(role["label"], "chat",
                {"question": _last_user_text(history)[:300], "tools": [t["tool"] for t in trace]}, status)


def run_agent(history, role):
    question = _last_user_text(history)
    LAST_USAGE.update(input_tokens=0, output_tokens=0, mode="live")
    if llm.MOCK:
        answer, trace = mock_agent(question, role)
        _log_chat(role, history, trace, "mock")
        return answer, trace

    messages = [{"role": m["role"], "content": m["content"]} for m in history[-MAX_HISTORY:]]
    while messages and messages[0]["role"] != "user":
        messages.pop(0)  # a trimmed window must start with the user
    tools = [t for t in TOOLS if allowed(role, t["name"])]
    system = system_prompt(role)
    trace = []

    try:
        for round_no in range(MAX_ROUNDS):
            kwargs = {"tools": tools} if tools else {}
            if tools and round_no == MAX_ROUNDS - 1:
                kwargs["tool_choice"] = {"type": "none"}  # last round: must answer
            reply = llm.client().messages.create(
                model=config.MODEL, max_tokens=1200, system=system, messages=messages, **kwargs)
            LAST_USAGE["input_tokens"] += reply.usage.input_tokens
            LAST_USAGE["output_tokens"] += reply.usage.output_tokens

            if reply.stop_reason != "tool_use":
                answer = "".join(b.text for b in reply.content if b.type == "text").strip()
                _log_chat(role, history, trace, "ok")
                return answer or "(No answer returned.)", trace

            messages.append({"role": "assistant", "content": reply.content})
            results = []
            for block in reply.content:
                if block.type != "tool_use":
                    continue
                trace.append({"tool": block.name, "args": block.input})
                args = _scope(block.name, dict(block.input), role)
                if not allowed(role, block.name):
                    result = {"error": f"Your role ({role['label']}) can't use {block.name}."}
                else:
                    result = _out_of_scope(block.name, args, role) or call_tool(block.name, args, role["label"])
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": json.dumps(result, default=str),
                                **({"is_error": True} if "error" in result else {})})
            messages.append({"role": "user", "content": results})
        raise RuntimeError("tool loop did not finish")
    except Exception as e:
        answer, offline_trace = mock_agent(question, role)
        _log_chat(role, history, trace + offline_trace, f"fallback: {type(e).__name__}")
        return (f"Claude was unavailable ({type(e).__name__}), so this is an offline answer.\n\n{answer}",
                trace + offline_trace)
