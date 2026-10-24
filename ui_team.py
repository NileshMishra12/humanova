"""Team tab: headline numbers, who may need a supportive conversation, the team table and 1:1 prep."""
import streamlit as st

import agent
from tools import employee_tools

COLUMNS = {"name": "Name", "role": "Role", "team": "Team", "status_today": "Today",
           "attendance_pct": "Attendance %", "goals_on_track": "Goals on track",
           "goals_overdue": "Goals overdue", "latest_rating": "Latest rating"}


def _attention(team):
    st.subheader("Needs attention")
    res = employee_tools.get_attention_flags(team)
    if "error" in res:
        st.error(res["error"])
        return
    for flag in res["flags"]:
        st.warning(f"**{flag['employee']}** - {flag['detail']}")
    if not res["flags"]:
        st.success("Nobody is flagged right now.")
    st.caption("Flags are rule-based prompts for a supportive conversation, not judgements.")


def _prep(role, names):
    st.subheader("1:1 prep")
    name = st.selectbox("1:1 prep for", names, key="team-prep-person")
    summaries = st.session_state.setdefault("team_summaries", {})
    key = (role["label"], name)
    if st.button("Generate 1:1 summary", key="team-prep-button", disabled=not name):
        with st.spinner("Preparing the summary..."):
            summaries[key] = agent.call_tool("summarise_employee", {"name_or_id": name}, role["label"])
    res = summaries.get(key)
    if not res:
        return
    if "error" in res:
        st.error(res["error"])
        return
    st.markdown(res["summary"])
    st.caption("Written by Claude from the facts on file." if res["source"] == "llm"
               else "Built from the facts on file (offline mode).")


def render(role):
    if not agent.allowed(role, "team_overview"):
        st.info("Your role doesn't include employee data, so there is no team view here.")
        return
    team = role["team"]  # None means everyone
    overview = employee_tools.team_overview(team)
    if "error" in overview:
        st.error(overview["error"])
        return

    tiles = st.columns(4)
    tiles[0].metric("Headcount", overview["headcount"])
    tiles[1].metric("Present today", overview["present_today"])
    tiles[2].metric("On leave today", overview["on_leave_today"])
    tiles[3].metric("Need attention", overview["flagged"])

    if agent.allowed(role, "get_attention_flags"):
        _attention(team)

    st.subheader(f"{team} team" if team else "Everyone")
    st.dataframe([{label: row[key] for key, label in COLUMNS.items()} for row in overview["rows"]],
                 hide_index=True, width="stretch")

    if agent.allowed(role, "summarise_employee"):
        _prep(role, [row["name"] for row in overview["rows"]])
