"""The "Appraisals & HR" tab (owner: Lead). Every number comes from the tools, every rule from the policy file."""
import pandas as pd
import streamlit as st

import agent
import nexus_client
import policy
from tools import performance_tools

REMINDER_ICONS = {"probation": "⏳", "wellbeing": "💚", "workload": "📊", "leave balance": "🏖️", "increment": "📈"}
SHOW = 15


def _use(role, name, **args):
    return agent.use_tool(role, name, **args)


def _team_picker(role, column):
    """Managers are fixed to their team; HR picks one (or all)."""
    if role["team"]:
        column.caption(f"Team: {role['team']}")
        return role["team"]
    teams = sorted({e["team"] for e in _use(role, "list_employees").get("employees", [])})
    choice = column.selectbox("Team", ["All teams", *teams], key="appr-team")
    return None if choice == "All teams" else choice


def _show_report(title, res, key):
    if res is None:
        return
    if "error" in res:
        st.error(res["error"])
        return
    with st.expander(f"📄 {title}", expanded=True):
        st.markdown(res["report"])
        st.download_button("⬇️ Download", res["report"], file_name=res["filename"], mime="text/markdown",
                           key=f"download-{key}")


def _show_pack(role, res, inc):
    if "error" in res:
        st.error(res["error"])
        return
    ev = res["evidence"]
    with st.container(border=True):
        st.markdown(f"##### {ev['employee']} · {ev['cycle']}")
        top = st.columns(3)
        top[0].metric("Suggested band", ev["band"])
        top[1].metric("Score", f"{ev['score']}/100")
        top[2].metric("Nexus trend", ev["trend"])
        rng = ev["increment_range"]
        row = next((r for r in inc.get("rows", []) if r["employee_id"] == ev["employee_id"]), None)
        if row is None:
            st.markdown(f"**Increment range:** {rng['min_pct']}-{rng['max_pct']}%")
        elif row["eligible"]:
            st.markdown(f"**Recommended increment:** {row['recommended_pct']}% "
                        f"(range {rng['min_pct']}-{rng['max_pct']}%)")
        else:
            st.markdown(f"**Not eligible for an increment:** {row['reason']}")
        if ev.get("action"):
            st.warning(f"Policy action: {ev['action']}")

        st.markdown("**Score breakdown**")
        st.dataframe([{"Component": performance_tools.LABELS[k], "Points": v, "Max": ev["max_points"][k]}
                      for k, v in ev["breakdown"].items()], width="stretch", hide_index=True)
        st.markdown("**Evidence**")
        st.dataframe([{"Source": e["source"], "Fact": e["fact"]} for e in ev["evidence"]],
                     width="stretch", hide_index=True)
        st.markdown(ev["narrative"])
        st.caption(ev["increment_note"])

        send, download = st.columns([1, 1])
        if send.button("Send to HR for approval", key=f"send-{ev['employee_id']}"):
            sent = _use(role, "request_appraisal_approval", name_or_id=ev["employee_id"], cycle=ev["cycle"],
                        requested_by=role["label"])
            if "error" in sent:
                st.warning(sent["error"])
            else:
                st.success(f"Sent to HR ({sent['approval_id']}). HR decides it in the Approvals tab.")
        download.download_button("⬇️ Download appraisal report", res["report"], file_name=res["filename"],
                                 mime="text/markdown", key=f"download-pack-{ev['employee_id']}")


def _policy_expander():
    pol = policy.load()
    with st.expander("📜 Policy rules in use"):
        st.caption(pol["name"])
        st.markdown("**Bands**")
        st.dataframe([{"Band": b["name"], "Minimum score": b["min_score"],
                       "Increment": f"{b['increment_min_pct']}-{b['increment_max_pct']}%",
                       "Action": b.get("action", "")} for b in pol["bands"]], width="stretch", hide_index=True)
        st.markdown("**Rubric weights (points)**")
        st.dataframe([{"Component": performance_tools.LABELS[k], "Points": v}
                      for k, v in pol["rubric_weights"].items()], width="stretch", hide_index=True)
        el = pol["eligibility"]
        st.markdown(
            f"- Increment budget: **{pol['increment_budget_pct']}%** average\n"
            f"- Eligibility: at least {el['min_service_months']} months of service"
            f"{', not in probation (' + str(el['probation_months']) + ' months)' if el['exclude_probation'] else ''}\n"
            f"- Top band share limit: {round(pol['top_band_max_share'] * 100)}%\n"
            f"- Trend adjustment: ±{pol['trend_adjustment_points']} points")
        for note in pol["notes"]:
            st.caption(f"• {note}")
        st.info("HR can replace `policies/appraisal_policy.json` with the company policy. No code changes needed.")


def _nexus_expander(role, team):
    with st.expander("📥 Input from Nexus (read-only)"):
        if not agent.allowed(role, "team_performance"):
            st.caption("Not available for your role.")
            return
        table = performance_tools.work_table(team)
        if "error" in table:
            st.caption(table["error"])
            return
        st.dataframe([{"Name": r["name"], "Role": r["role"], "Team": r["team"],
                       "Tasks/day worked": "-" if r["tasks_per_day"] is None else f"{r['tasks_per_day']:.2f}",
                       "On-time %": "-" if r["on_time_pct"] is None else f"{r['on_time_pct']:.0f}",
                       "Trend": r["trend"] or "no data"} for r in table["rows"]], width="stretch", hide_index=True)
        if table["series"]:
            st.caption("Tasks per day worked, for people whose trend changed")
            st.line_chart(pd.DataFrame(table["series"]).sort_index())
        capacity = agent.use_tool(role, "team_capacity", **({"team": team} if team else {}))
        if "error" not in capacity:
            st.markdown("**Capacity (from Nexus projects and recent output)**")
            st.dataframe([{"Name": p["name"], "Team": p["team"], "Status": p["status"],
                           "Active projects": p["active_projects"], "Overdue": p["overdue_projects"],
                           "Output vs target": "-" if p["utilisation_pct"] is None else f"{p['utilisation_pct']}%"}
                          for p in capacity["people"]], width="stretch", hide_index=True)
            st.caption(capacity["rule"])
        source = nexus_client.source()
        st.caption(f"Source: {source['name']} ({source['mode']}: {source['detail']}). "
                   "HumaNova only reads these numbers from Nexus. It never changes or re-measures them.")


def render(role):
    if not agent.allowed(role, "increment_recommendations"):
        st.info("This tab is for managers and HR. Recruiters work in the Hiring tab.")
        return

    if st.session_state.get("appr-role") != role["label"]:  # packs and reports belong to one role
        for key in ("appr-pack", "appr-people", "appr-team-report"):
            st.session_state.pop(key, None)
        st.session_state["appr-role"] = role["label"]

    controls = st.columns([1, 1, 2])
    cycle = controls[0].text_input("Appraisal cycle", value="2026-H2", key="appr-cycle").strip() or "2026-H2"
    team = _team_picker(role, controls[1])
    scope = {"team": team} if team else {}

    status = _use(role, "appraisal_cycle_status", cycle=cycle, **scope)
    cal = _use(role, "calibration", cycle=cycle, **scope)
    inc = _use(role, "increment_recommendations", cycle=cycle, **scope)
    for res in (status, cal, inc):
        if "error" in res:
            st.error(res["error"])
            return
    reminders = _use(role, "hr_reminders", **scope)
    reminders = None if "error" in reminders else reminders.get("reminders", [])

    metrics = st.columns(4)
    metrics[0].metric("Cycle approved", f"{status['approved_pct']}%")
    metrics[1].metric("Waiting for HR", status["waiting"])
    metrics[2].metric("Calibration checks", len(cal["checks"]))
    metrics[3].metric("HR reminders", "n/a" if reminders is None else len(reminders))

    st.markdown("#### Appraisal cycle")
    st.dataframe([{"Name": r["name"], "Team": r["team"], "Status": r["status"], "Band": r["band"],
                   "Score": r["score"], "Last rating": "-" if r["last_rating"] is None else str(r["last_rating"]),
                   "Trend": r["trend"]} for r in status["rows"]], width="stretch", hide_index=True)

    left, right = st.columns(2)
    with left:
        st.markdown("#### Band distribution")
        st.bar_chart(pd.DataFrame(cal["distribution"]), x="band", y="count", horizontal=True, sort=False)
    with right:
        st.markdown("#### Calibration checks")
        if not cal["checks"]:
            st.success("No calibration issues.")
        for c in cal["checks"]:
            st.warning(f"**{c.get('employee') or 'Distribution skew'}** · {c['rule']}\n\n{c['detail']}")

    st.markdown("#### Increment recommendations")
    top = st.columns(3)
    top[0].metric("Average increment", f"{inc['average_after']}%")
    top[0].caption(f"budget {inc['budget_pct']}%" + (" · scaled to fit" if inc["scaled"] else ""))
    top[1].metric("Top performers", ", ".join(t["name"].split()[0] for t in inc["top3"]) or "-")
    top[2].metric("Not eligible", len(inc["not_eligible"]))
    st.dataframe([{"Rank": r["rank"], "Name": r["name"], "Team": r["team"], "Score": r["score"],
                   "Band": r["band"], "Range": f"{r['range_min_pct']}-{r['range_max_pct']}%",
                   "Recommended": f"{r['recommended_pct']}%" if r["eligible"] else "Not eligible",
                   "Note": r["reason"]} for r in inc["rows"]], width="stretch", hide_index=True)
    _policy_expander()

    st.markdown("#### HR reminders")
    if reminders is None:
        st.caption("HR reminders aren't available yet.")
    elif not reminders:
        st.success("No reminders due.")
    for r in (reminders or [])[:SHOW]:
        st.info(f"**{r['name']}** · due {r['due']}\n\n{r['detail']}", icon=REMINDER_ICONS.get(r["type"], "🔔"))
    if reminders and len(reminders) > SHOW:
        st.caption(f"…and {len(reminders) - SHOW} more")

    st.markdown("#### Evidence pack")
    people = _use(role, "list_employees", **scope).get("employees", [])
    labels = {f"{e['name']} ({e['role']}, {e['id']})": e["id"] for e in people}
    choice = st.selectbox("Employee", list(labels), key="appr-employee")
    build, people_report, team_report = st.columns(3)
    if choice and build.button("⚖️ Build evidence pack"):
        with st.spinner("Building the evidence pack…"):
            st.session_state["appr-pack"] = _use(role, "appraisal_report", name_or_id=labels[choice], cycle=cycle)
    if agent.allowed(role, "people_report") and people_report.button("👥 People report (HR)"):
        with st.spinner("Building the people report…"):
            st.session_state["appr-people"] = _use(role, "people_report", cycle=cycle, **scope)
    if team_report.button("📝 Team work report"):
        st.session_state["appr-team-report"] = _use(role, "team_report", **scope)

    if st.session_state.get("appr-pack"):
        _show_pack(role, st.session_state["appr-pack"], inc)
    _show_report("People report", st.session_state.get("appr-people"), "people")
    _show_report("Team work report", st.session_state.get("appr-team-report"), "team")

    _nexus_expander(role, team)
