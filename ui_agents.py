"""The Agents tab: a connections map and one card per agent, like the Agents page in Jaarvis."""
import math
from html import escape

import streamlit as st

import config
import store

DOT = {"live": "#3fb950", "offline": "#d29922", "planned": "#8b949e"}
BADGE = {"ready": "🟢 ready", "offline": "🟡 offline", "planned": "⚪ planned"}
NO_LLM = "no AI model: rules"

# (icon, name, what it does, model, audit-log tool names). model: "main", "fast" or a fixed label.
LIVE_AGENTS = [
    ("🧭", "Orchestrator", "Reads each chat question, picks the tools and writes the answer.", "main", ("chat",)),
    ("👥", "Employee Tracker", "Employees, attendance, leave, goals and attention flags.", "fast",
     ("list_employees", "get_employee", "who_is_on_leave", "get_attendance", "get_attention_flags",
      "team_overview", "summarise_employee")),
    ("📈", "Nexus reader", "Reads work metrics from Nexus as input to HR.", NO_LLM,
     ("performance_trend", "team_performance", "project_status")),
    ("⚖️", "Calibration auditor", "Compares ratings across teams and suggests increments.", "main",
     ("calibration", "increment_recommendations")),
    ("⏰", "HR reminders", "Probation, wellbeing, workload, leave balance and increment nudges.", NO_LLM,
     ("hr_reminders",)),
    ("📝", "Appraisal auditor", "Builds evidence packs and recommends appraisal outcomes.", "main",
     ("appraisal_evidence", "appraisal_cycle_status", "request_appraisal_approval")),
    ("📄", "Report writer", "Writes team and company people reports.", "main", ("team_report", "people_report")),
    ("🎯", "Hiring Screener", "Ranks anonymised resumes, drafts emails and interview questions.", "fast",
     ("list_openings", "screen_candidates", "get_candidate", "request_stage_change", "draft_email",
      "interview_questions")),
    ("🕸️", "Brain", "Knowledge graph of people, skills, projects and teams.", "no AI model: a graph",
     ("graph_search",)),
    ("🔁", "Learning loop", "Learns from recruiter decisions and answer feedback.", NO_LLM,
     ("reject_candidate", "add_feedback")),
    ("☀️", "Daily digest", "A morning summary of what needs attention today.", "main", ("daily_digest",)),
]

PLANNED_AGENTS = [
    ("🔎", "Sourcing agent", "Finds matching profiles for open roles."),
    ("✉️", "Outreach agent", "Writes outreach messages; the recruiter approves before sending."),
    ("📥", "CV intake (MCP)", "Collects CVs from mailboxes and job portals into openings."),
    ("📅", "Interview scheduler", "Books interview slots across calendars."),
    ("🔄", "Nexus sync", "Keeps work metrics in sync with Nexus automatically."),
    ("🏁", "Goal coach", "Helps employees break goals into weekly steps."),
    ("📚", "Policy Q&A", "Answers policy questions with the source clause."),
    ("🤝", "Onboarding agent", "Guides new joiners through their first weeks."),
]


def _plural(n, word):
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _stats(module_name):
    """stats() from the lead's kg / learning module, or None if it isn't there yet."""
    try:
        return __import__(module_name).stats()
    except Exception:
        return None


def _employee_count():
    try:
        from tools import employee_tools
        return len(employee_tools.list_employees().get("employees", []))
    except Exception:
        return 0


def _connections():
    kg, learning = _stats("kg"), _stats("learning")
    if config.MOCK:
        claude = ("Claude", "offline mode: rules answer", "offline")
    else:
        claude = ("Claude", f"live · {config.MODEL} · {config.FAST_MODEL}", "live")
    return [
        claude,
        ("HR data", f"dummy data · {_plural(_employee_count(), 'employee')} · Perk export format", "live"),
        ("Knowledge graph", f"{_plural(kg['nodes'], 'node')} · {_plural(kg['edges'], 'link')}", "live")
        if kg else ("Knowledge graph", "planned", "planned"),
        ("Learning memory", f"{_plural(learning['decisions'], 'decision')} · {learning['feedback_up']} up / "
                            f"{learning['feedback_down']} down · {_plural(learning['corrections'], 'correction')}",
         "live") if learning else ("Learning memory", "planned", "planned"),
        ("Nexus", "phase 2: read work metrics (input to HR)", "planned"),
        ("Perk HRMS", "phase 2: sync", "planned"),
        ("PostgreSQL", "phase 2", "planned"),
        ("Naukri / LinkedIn", "phase 3: sourcing", "planned"),
        ("Email / Teams", "phase 2", "planned"),
    ]


def _wrap(text, width=34):
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    return lines + [line] if line else lines


def _connections_html():
    w, h, cx, cy = 920, 560, 460, 265
    rx, ry, bw, bh = 330, 200, 224, 64
    lines, boxes = [], []
    nodes = _connections()
    for i, (name, detail, status) in enumerate(nodes):
        angle = -math.pi / 2 + 2 * math.pi * i / len(nodes)
        x, y = cx + rx * math.cos(angle), cy + ry * math.sin(angle)
        left, top = x - bw / 2, y - bh / 2
        lines.append(f'<line x1="{cx}" y1="{cy}" x2="{x:.0f}" y2="{y:.0f}" stroke="#3d444d" '
                     f'stroke-width="1.5" stroke-dasharray="6 5"/>')
        detail_svg = "".join(
            f'<text x="{left + 14:.0f}" y="{top + 41 + 14 * j:.0f}" fill="#9da7b3" font-size="11.5">'
            f'{escape(part)}</text>'
            for j, part in enumerate(_wrap(detail)[:2])
        )
        boxes.append(
            f'<rect x="{left:.0f}" y="{top:.0f}" width="{bw}" height="{bh}" rx="10" fill="#1b2028" stroke="#30363d"/>'
            f'<circle cx="{left + bw - 16:.0f}" cy="{top + 18:.0f}" r="5" fill="{DOT[status]}"/>'
            f'<text x="{left + 14:.0f}" y="{top + 23:.0f}" fill="#e6edf3" font-size="14" font-weight="600">'
            f'{escape(name)}</text>{detail_svg}'
        )
    hub = (
        f'<circle cx="{cx}" cy="{cy}" r="80" fill="#1f6feb" fill-opacity="0.15" stroke="#58a6ff" stroke-width="2"/>'
        f'<text x="{cx}" y="{cy - 2}" fill="#e6edf3" font-size="20" font-weight="700" text-anchor="middle">'
        f'HumaNova</text>'
        f'<text x="{cx}" y="{cy + 20}" fill="#9da7b3" font-size="12" text-anchor="middle">HR agent platform</text>'
    )
    legend = "".join(
        f'<circle cx="{24 + 80 * i}" cy="{h - 22}" r="5" fill="{DOT[s]}"/>'
        f'<text x="{34 + 80 * i}" y="{h - 18}" fill="#9da7b3" font-size="12">{s}</text>'
        for i, s in enumerate(DOT)
    )
    note = (f'<text x="{w - 20}" y="{h - 18}" fill="#8b949e" font-size="12" text-anchor="end">'
            f'Planned connections are on the roadmap and not active yet.</text>')
    # width and height 100%: the drawing scales to fit the iframe, so nothing is cut off on wide screens.
    svg = (f'<svg viewBox="0 0 {w} {h}" width="100%" height="100%" xmlns="http://www.w3.org/2000/svg" '
           f'font-family="Segoe UI, Helvetica, Arial, sans-serif">'
           f'{"".join(lines)}{hub}{"".join(boxes)}{legend}{note}</svg>')
    return ('<html style="height:100%"><body style="margin:0;height:100%;overflow:hidden;'
            f'background:#111418;border-radius:12px">{svg}</body></html>')


def _run_counts():
    counts = {}
    for entry in store.read_json("audit_log.json"):
        counts[entry.get("tool")] = counts.get(entry.get("tool"), 0) + 1
    return counts


def _model(model):
    if model not in ("main", "fast"):
        return model
    if config.MOCK:
        return "offline: rules"
    return config.MODEL if model == "main" else config.FAST_MODEL


def _cards():
    counts = _run_counts()
    cards = []
    for icon, name, what, model, tools in LIVE_AGENTS:
        status = "offline" if model in ("main", "fast") and config.MOCK else "ready"
        runs = sum(counts.get(t, 0) for t in tools)
        cards.append((icon, name, status, what, f"{_plural(runs, 'run')} · {_model(model)}"))
    for icon, name, what in PLANNED_AGENTS:
        cards.append((icon, name, "planned", what, "on the roadmap"))
    return cards


def render():
    st.subheader("Connections")
    html, height = _connections_html(), 580
    if hasattr(st, "iframe"):
        st.iframe(html, height=height)
    else:
        import streamlit.components.v1 as components
        components.html(html, height=height)

    st.subheader("Agents")
    cards = _cards()
    for start in range(0, len(cards), 3):
        for col, (icon, name, status, what, footer) in zip(st.columns(3), cards[start:start + 3]):
            with col, st.container(border=True):
                st.markdown(f"**{icon} {name}** · {BADGE[status]}")
                st.write(what)
                st.caption(footer)
