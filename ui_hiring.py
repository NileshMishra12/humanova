"""The Hiring tab: openings, resume upload, anonymised screening and recruiter actions."""
import streamlit as st

import agent
from tools import hiring_tools

RESULTS = "hiring_results"


def _learning():
    """The lead's learning module, once it has landed."""
    try:
        import learning
        return learning
    except ImportError:
        return None


def _screen(opening_id):
    learning = _learning()
    if learning is not None:
        result = learning.screen_with_learning(opening_id)
    else:
        result = hiring_tools.screen_candidates(opening_id)
    st.session_state.setdefault(RESULTS, {})[opening_id] = result


def _skill_names(items):
    """Skill names from the learning block's lessons, whatever shape they come in."""
    if isinstance(items, dict):
        return [str(k) for k in items]
    names = []
    for item in items or []:
        if isinstance(item, dict):
            item = item.get("skill") or item.get("name") or ""
        if str(item).strip():
            names.append(str(item).strip())
    return names


def _upload(opening_id):
    files = st.file_uploader("Add resumes (.txt or .pdf)", type=["txt", "pdf"],
                             accept_multiple_files=True, key=f"hiring_upload_{opening_id}")
    if files and st.button(f"Add {len(files)} resume(s)", key=f"hiring_add_{opening_id}"):
        for f in files:
            result = hiring_tools.add_resume(opening_id, f.name, f.getvalue())
            if "error" in result:
                st.error(result["error"])
            else:
                st.success(f"Added {result['name']} ({result['candidate_id']}).")


def _not_a_fit(cand, role, opening_id):
    cid = cand["candidate_id"]
    with st.popover("Not a fit", width="stretch"):
        reason = st.text_area("Why?", key=f"hiring_why_{cid}")
        if st.button("Reject", key=f"hiring_reject_{cid}", type="primary"):
            learning = _learning()
            if learning is not None:
                result = learning.reject_candidate(cid, reason, role["label"])
            else:
                result = hiring_tools.apply_stage_change(cid, "Rejected")
            if isinstance(result, dict) and "error" in result:
                st.error(result["error"])
                return
            _screen(opening_id)
            st.rerun()


def _card(cand, role, opening_id, can_act):
    cid = cand["candidate_id"]
    with st.container(border=True):
        st.markdown(f"**{cand['name']}** · `{cid}` · {cand['stage']}")
        st.progress(cand["score"] / 100, text=f"{cand['score']}/100 · suggested: {cand['next_step']}")
        st.write(cand["reason"])
        if cand.get("learned_note"):
            st.caption(cand["learned_note"])
        st.markdown(f"✅ {', '.join(cand['matched']) or 'none'}  \n❌ {', '.join(cand['missing']) or 'none'}")
        if not can_act:
            return

        cols = st.columns(4)
        if cols[0].button("Request interview", key=f"hiring_req_{cid}", width="stretch"):
            result = hiring_tools.request_stage_change(cid, "Interview", role["label"])
            if "error" in result:
                st.error(result["error"])
            else:
                st.success(f"{result['summary']}: waiting for approval ({result['approval_id']}).")
        if cols[1].button("Draft invite", key=f"hiring_draft_{cid}", width="stretch"):
            st.session_state[f"hiring_email_{cid}"] = hiring_tools.draft_email(cid, "invite")
        if cols[2].button("Interview questions", key=f"hiring_qs_{cid}", width="stretch"):
            st.session_state[f"hiring_questions_{cid}"] = hiring_tools.interview_questions(cid)
        with cols[3]:
            _not_a_fit(cand, role, opening_id)

        email = st.session_state.get(f"hiring_email_{cid}")
        if email:
            if "error" in email:
                st.error(email["error"])
            else:
                st.text_area(f"Draft invite ({email['status']})",
                             f"Subject: {email['subject']}\n\n{email['body']}",
                             height=280, key=f"hiring_email_text_{cid}")
        questions = st.session_state.get(f"hiring_questions_{cid}")
        if questions:
            if "error" in questions:
                st.error(questions["error"])
            else:
                st.markdown("\n".join(f"{i}. {q}" for i, q in enumerate(questions["questions"], 1)))


def _new_opening():
    with st.expander("➕ New opening"):
        with st.form("hiring_new_opening"):
            title = st.text_input("Title")
            must = st.text_input("Must-have skills (comma separated)")
            nice = st.text_input("Nice-to-have skills (comma separated)")
            min_years = st.number_input("Minimum years", min_value=0, max_value=40, step=1)
            jd = st.text_area("Job description")
            if st.form_submit_button("Create opening"):
                result = hiring_tools.create_opening(title, must, nice, min_years, jd)
                if "error" in result:
                    st.error(result["error"])
                else:
                    st.rerun()


def render(role):
    if not agent.allowed(role, "screen_candidates"):
        st.info("Hiring isn't part of your role.")
        return
    can_act = agent.allowed(role, "request_stage_change")

    listing = hiring_tools.list_openings()
    if "error" in listing:
        st.error(listing["error"])
        return
    labels = {o["id"]: f"{o['title']} ({o['id']}) · {o['candidates']} candidates" for o in listing["openings"]}
    if labels:
        opening_id = st.selectbox("Opening", list(labels), format_func=labels.get, key="hiring_opening")

        if can_act:
            _upload(opening_id)

        if st.button("🔍 Run screening", type="primary", key="hiring_run"):
            _screen(opening_id)

        result = st.session_state.get(RESULTS, {}).get(opening_id)
        if result and "error" in result:
            st.error(result["error"])
        elif result:
            st.caption("🔒 Scored without name, contact details, gender or age. "
                       "Final decisions need human approval.")
            learned = result.get("learning") or {}
            if learned.get("decisions"):
                skills = ", ".join(_skill_names(learned.get("lessons")))
                st.info(f"🧠 Learned from {learned['decisions']} recruiter decision(s)"
                        + (f": {skills}" if skills else "."))
            for cand in result["ranked"]:
                _card(cand, role, opening_id, can_act)
    else:
        st.info("No openings yet.")

    if can_act:
        _new_opening()
