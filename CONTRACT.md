# HumaNova Contract

Everyone sticks to this. If you need a change, ask the owner of the file.

## Rules

1. Every tool function returns a plain `dict`. It never raises. Bad input returns `{"error": "..."}`.
2. Data access goes only through `store.py`. No direct `open()`, `csv` or `json` file handling elsewhere.
3. Settings come from `config.py`. Check `llm.MOCK` to choose between Claude and your rule-based fallback.
4. A **role** is a dict `{label, team, scope, tools}` from `agent.ROLES`. `agent.allowed(role, tool_name)` says if the role may use a tool. UI modules hide whatever the role can't use.

## Member A: `tools/employee_tools.py`

| Function | Returns |
|---|---|
| `list_employees(team=None)` | `{"employees": [{id, name, role, team, manager_id, status_today}]}` |
| `get_employee(name_or_id)` | `{"employee": {id, name, email, role, team, manager_id, join_date, location, skills (list), leave_balance {casual, sick, earned} (ints), status_today}, "goals": [{id, employee_id, title, target_date, progress_pct (int), status, overdue (bool)}], "latest_review": {employee_id, cycle, rating (int), notes, reviewer_id} or None}` |
| `who_is_on_leave(start, end, team=None)` | `{"start", "end", "on_leave": [{name, employee_id, team, type, from, to}]}` |
| `get_attendance(name_or_id, days=30)` | `{employee, employee_id, days, present, wfh, leave, absent, attendance_pct}` |
| `get_attention_flags(team=None)` | `{"flags": [{employee, employee_id, rule, detail}]}`; `rule` is `"attendance"` \| `"goal_overdue"` \| `"low_rating"` |
| `team_overview(team=None)` | `{headcount, present_today, on_leave_today, flagged, rows: [{name, role, team, status_today, attendance_pct, goals_on_track, goals_overdue, latest_rating}]}` |
| `summarise_employee(name_or_id)` | `{"summary": str, "facts": dict, "source": "llm" \| "rules"}` |

## Member A: `tools/hr_reminders.py`

| Function | Returns |
|---|---|
| `hr_reminders(team=None)` | `{"reminders": [{type, name, due, detail}]}`; `type` is `probation` \| `wellbeing` \| `workload` \| `leave balance` \| `increment` |

## Member A: `ui_team.py`

- `render(role)` draws the Team tab.

## Member B: `tools/hiring_tools.py`

- `STAGES = ["Applied", "Shortlisted", "Interview", "Offer", "Hired", "Rejected"]`
- `extract_text(filename, data)` and `anonymise(text) -> str`

| Function | Returns |
|---|---|
| `list_openings()` | `{"openings": [{id, title, team, candidates}]}` |
| `create_opening(title, must_have, nice_to_have=None, min_years=0, jd_text="", team="")` | `{opening_id, title}` |
| `add_resume(opening_id, filename, data)` | `{candidate_id, name}` |
| `screen_candidates(opening_id)` | `{opening, opening_id, ranked: [{candidate_id, name, score, matched, missing, years_experience, reason, next_step, stage}]}` |
| `get_candidate(candidate_id)` | `{"candidate": {...}}` |
| `request_stage_change(candidate_id, stage, requested_by="HumaNova")` | `{approval_id, status: "pending", summary}` |
| `apply_stage_change(candidate_id, stage)` | `{candidate_id, stage}` (called by `store.decide_approval` on approval) |
| `draft_email(candidate_id, kind="invite")` | `{subject, body, status: "draft - not sent"}` |
| `interview_questions(candidate_id)` | `{candidate_id, questions: [5 strings]}` |

## Member B: UI

- `ui_hiring.py`: `render(role)`
- `ui_agents.py`: `render()`

## Lead (added later, so the shapes can be relied on)

| Function | Returns |
|---|---|
| `learning.screen_with_learning(opening_id)` | Same as `screen_candidates`, plus `learned_adjustment` and `learned_note` per candidate, and `"learning": {decisions, lessons, valued}` |
| `learning.reject_candidate(candidate_id, reason, by)` | Rejects a candidate and records the decision for learning |
| `learning.stats()` | `{decisions, feedback_up, feedback_down, corrections}` |
| `kg.stats()` | `{nodes, edges, by_type}` |
| `store.add_feedback(question, answer, rating, comment, by)` | `rating` is `"up"` or `"down"` |
