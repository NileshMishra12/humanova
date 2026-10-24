"""HumaNova Streamlit app.
Run: streamlit run app.py --server.address 0.0.0.0 --server.port 8788
"""
import importlib
import json
import runpy
from datetime import date

import streamlit as st

import agent
import config
import llm
import nexus_client
import nexus_sync
import store
from nexus_client import NexusError

st.set_page_config(page_title="HumaNova", page_icon="🌟", layout="wide")

EXAMPLES = ["Plan my Monday", "Who performed best? Increments", "Calibration check",
            "Appraisal evidence for Arjun", "Any HR reminders?"]
RECRUITER_EXAMPLES = ["Shortlist the candidates", "Who can interview for RxJS?",
                      "Draft an invite for the top candidate", "Move C1 to interview"]
TABS = ["💬 Chat", "👥 Team", "⚖️ Appraisals & HR", "🧑‍💼 Hiring", "✅ Approvals",
        "☀️ Daily digest", "🧠 Knowledge graph", "🤖 Agents", "📜 Audit log"]


# ---- sidebar ----

def reset_demo_data():
    notes = []
    for name in ("make_dummy_data.py", "make_hiring_data.py"):
        path = config.ROOT / "scripts" / name
        if not path.exists():
            notes.append(f"{name} isn't there yet, skipped.")
            continue
        try:
            runpy.run_path(str(path), run_name="__main__")
        except Exception as e:
            notes.append(f"{name} failed: {type(e).__name__}: {e}")
    nexus_client.forget_sync()  # the dummy data is back, so the files no longer come from Nexus
    st.session_state.clear()
    st.session_state["flash"] = " ".join(notes) or "Demo data reset."
    st.rerun()


def sync_from_nexus():
    try:
        with st.spinner("Reading from Nexus…"):
            result = nexus_sync.sync()
    except NexusError as e:
        st.session_state["flash"] = f"Nexus sync failed: {e}"
        st.rerun()
    if not result["ok"]:
        st.session_state["flash"] = " ".join(result["warnings"])
    else:
        counts = ", ".join(f"{n} {name}" for name, n in result["counts"].items())
        warnings = "".join(f"\n- {w}" for w in result["warnings"])
        st.session_state["flash"] = f"Synced from Nexus ({result['tier']}): {counts}.{warnings}"
    st.session_state.pop("digests", None)
    st.rerun()


def sidebar():
    st.sidebar.title("🌟 HumaNova")
    st.sidebar.caption("AI agent platform for HR · demo on dummy data")
    role = agent.ROLES[st.sidebar.selectbox("Signed in as (demo)", list(agent.ROLES))]
    st.sidebar.write("Mode: " + ("🟡 Offline" if llm.MOCK else f"🟢 Live · {config.MODEL}"))
    st.sidebar.write("📅 " + date.fromisoformat(config.TODAY).strftime("%A %d %b %Y"))
    st.sidebar.write("🧠 Learned so far: 0")
    source = nexus_client.source()
    st.sidebar.write("Nexus: " + ("🟢 live sync" if source["live"] else "🟡 dummy export"))
    if source["live"]:
        st.sidebar.caption(source["detail"])
    if config.NEXUS_LIVE and st.sidebar.button("Sync from Nexus", width="stretch"):
        sync_from_nexus()
    if st.sidebar.button("Reset demo data", width="stretch"):
        reset_demo_data()
    flash = st.session_state.pop("flash", None)
    if flash:
        st.sidebar.info(flash)
    st.sidebar.caption("Data today is dummy files shaped like a Perk export. "
                       "Phase 2 is Postgres + Perk sync.")
    return role


# ---- teammate and later modules ----

def render_module(module_name, owner, *args):
    try:
        mod = importlib.import_module(module_name)
    except ModuleNotFoundError as e:
        if e.name != module_name:
            st.error(f"`{module_name}` failed to import: {e}")
        else:
            st.info(f"`{module_name}` isn't ready yet: {owner} is still building it.")
        return
    except Exception as e:
        st.error(f"`{module_name}` failed to import: {type(e).__name__}: {e}")
        return
    try:
        mod.render(*args)
    except Exception as e:
        st.error(f"`{module_name}` hit an error: {type(e).__name__}: {e}")


# ---- chat ----

def save_feedback(role, msgs, i, rating, comment=""):
    add_feedback = getattr(store, "add_feedback", None)  # added later
    if add_feedback is None:
        return False
    question = msgs[i - 1]["content"] if i > 0 else ""
    try:
        add_feedback(question, msgs[i]["content"], rating, comment, role["label"])
    except Exception as e:
        st.warning(f"Couldn't save feedback: {e}")
        return False
    return True


def show_message(role, msgs, i):
    m = msgs[i]
    label = role["label"]
    with st.chat_message(m["role"]):
        st.markdown(m["content"])
        if m["role"] != "assistant":
            return
        usage, trace = m.get("usage", {}), m.get("trace", [])
        tokens_in, tokens_out = usage.get("input_tokens", 0), usage.get("output_tokens", 0)
        mode = "🟡 offline" if usage.get("mode") == "offline" else "🟢 live"
        st.caption(f"{mode} · {tokens_in + tokens_out} tokens ({tokens_in} in / {tokens_out} out) "
                   f"· {len(trace)} tool call(s)")
        if m.get("feedback"):
            st.caption("Thanks, feedback noted.")
        else:
            up_col, down_col, _ = st.columns([1, 1, 8])
            if up_col.button("👍", key=f"up-{label}-{i}"):
                save_feedback(role, msgs, i, "up")
                m["feedback"] = "up"
                st.rerun()
            with down_col.popover("👎"):
                comment = st.text_area("What was wrong?", key=f"why-{label}-{i}")
                if st.button("Send", key=f"down-{label}-{i}"):
                    save_feedback(role, msgs, i, "down", comment)
                    m["feedback"] = "down"
                    st.rerun()
        if trace:
            with st.expander("🔧 Tools used"):
                st.json(trace)


def chat_tab(role):
    msgs = st.session_state.setdefault("chats", {}).setdefault(role["label"], [])
    examples = RECRUITER_EXAMPLES if role["label"].startswith("Recruiter") else EXAMPLES
    clicked = None
    for col, text in zip(st.columns(len(examples)), examples):
        if col.button(text, width="stretch", key=f"ex-{role['label']}-{text}"):
            clicked = text
    for i in range(len(msgs)):
        show_message(role, msgs, i)
    typed = st.chat_input("Ask HumaNova about your team, hiring or appraisals…")
    question = clicked or typed
    if question:
        msgs.append({"role": "user", "content": question})
        with st.spinner("Thinking…"):
            answer, trace = agent.run_agent([{"role": m["role"], "content": m["content"]} for m in msgs], role)
        msgs.append({"role": "assistant", "content": answer, "trace": trace, "usage": dict(agent.LAST_USAGE)})
        st.rerun()  # so the input sits below the new answer


# ---- approvals, digest, audit ----

def decide(approval_id, approve, role, reason):
    try:
        store.decide_approval(approval_id, approve, role["label"], reason)
    except Exception as e:
        st.error(f"Couldn't decide {approval_id}: {type(e).__name__}: {e}")
        return
    st.rerun()


def approvals_tab(role):
    if not (agent.can_decide(role, "appraisal") or agent.can_decide(role, "candidate_stage")):
        st.info("Your role can't decide approvals. Appraisal recommendations go to HR Admin.")
        return
    pending = [a for a in store.list_approvals() if agent.can_decide(role, a["kind"])]
    if not pending:
        st.success("Nothing is waiting for you.")
    for a in pending:
        with st.container(border=True):
            st.markdown(f"**{a['summary']}**")
            st.caption(f"{a['kind'].replace('_', ' ')} · requested by {a['requested_by']} · "
                       f"{a.get('created', '')} · {a['id']}")
            with st.expander("Details"):
                st.json(a["change"])
            reason = st.text_input("Reason (optional)", key=f"reason-{a['id']}")
            approve_col, reject_col, _ = st.columns([1, 1, 6])
            if approve_col.button("✅ Approve", key=f"approve-{a['id']}"):
                decide(a["id"], True, role, reason)
            if reject_col.button("❌ Reject", key=f"reject-{a['id']}"):
                decide(a["id"], False, role, reason)


def digest_tab(role):
    digests = st.session_state.setdefault("digests", {})
    if st.button("☀️ Generate daily digest", key="digest-button"):
        with st.spinner("Building the digest…"):
            digests[role["label"]] = agent.daily_digest(role)
    if role["label"] in digests:
        st.markdown(digests[role["label"]])
    else:
        st.caption("Click the button to build today's digest for your role.")


def audit_tab():
    entries = store.read_json("audit_log.json")[-200:][::-1]
    if not entries:
        st.info("No activity yet.")
        return
    st.dataframe(
        [{"time": e.get("time"), "user": e.get("user"), "tool": e.get("tool"), "status": e.get("status"),
          "inputs": json.dumps(e.get("inputs"), default=str)} for e in entries],
        width="stretch", hide_index=True)


# ---- page ----

role = sidebar()
tabs = st.tabs(TABS)
with tabs[0]:
    chat_tab(role)
with tabs[1]:
    render_module("ui_team", "Member A", role)
with tabs[2]:
    render_module("ui_appraisals", "the Lead", role)
with tabs[3]:
    render_module("ui_hiring", "Member B", role)
with tabs[4]:
    approvals_tab(role)
with tabs[5]:
    digest_tab(role)
with tabs[6]:
    render_module("ui_graph", "the Lead")
with tabs[7]:
    render_module("ui_agents", "Member B")
with tabs[8]:
    audit_tab()
