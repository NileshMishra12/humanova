"""The "Knowledge graph" tab (owner: Lead): numbers, a search box, and the graph drawn with vis-network."""
import json

import streamlit as st

import config

VIS_FILE = config.ROOT / "assets" / "vis-network.min.js"
VIS_CDN = "https://unpkg.com/vis-network@9.1.9/standalone/umd/vis-network.min.js"
COLORS = {"team": "#f59e0b", "employee": "#60a5fa", "skill": "#34d399", "sow": "#a78bfa",
          "opening": "#f472b6", "candidate": "#fb923c", "decision": "#f87171"}
SIZES = {"team": 26, "employee": 16, "skill": 12, "sow": 20, "opening": 22, "candidate": 14, "decision": 12}
LEGEND = {"team": "Team", "employee": "Employee", "skill": "Skill", "sow": "Project (SOW)",
          "opening": "Opening", "candidate": "Candidate", "decision": "Recruiter decision"}


def _script_json(data):
    """JSON that is safe inside a <script> tag: a typed reason like '</script>' can't end the script."""
    return (json.dumps(data).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
            .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


@st.cache_data
def _vis_script():
    """The vis-network library inline (works with no internet), else from a CDN."""
    if VIS_FILE.exists():
        return "<script>" + VIS_FILE.read_text(encoding="utf-8").replace("</script", "<\\/script") + "</script>"
    return f'<script src="{VIS_CDN}"></script>'


def _page(graph):
    data = {"nodes": graph["nodes"], "edges": graph["edges"], "colors": COLORS, "sizes": SIZES}
    present = {n["type"] for n in graph["nodes"]}
    legend = "".join(f'<span><i style="background:{COLORS[t]}"></i>{label}</span>'
                     for t, label in LEGEND.items() if t in present)
    return f"""<!doctype html><html><head><meta charset="utf-8"><style>
html,body{{margin:0;height:100%;background:#0f172a;font-family:system-ui,sans-serif}}
#g{{position:absolute;inset:0}}
#legend{{position:absolute;left:10px;bottom:10px;display:flex;gap:14px;flex-wrap:wrap;color:#cbd5e1;
font-size:12px;background:rgba(15,23,42,.8);padding:6px 10px;border-radius:6px}}
#legend i{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:5px}}
</style></head><body><div id="g"></div><div id="legend">{legend}</div>
{_vis_script()}
<script>
const DATA = {_script_json(data)};
const nodes = new vis.DataSet(DATA.nodes.map(n => ({{
  id: n.id, label: n.label, title: n.type + ": " + n.title, shape: "dot",
  color: DATA.colors[n.type], size: DATA.sizes[n.type] || 14,
  font: {{color: "#e2e8f0", size: 12, strokeWidth: 3, strokeColor: "#0f172a"}}
}})));
const edges = new vis.DataSet(DATA.edges.map((e, i) => ({{
  id: i, from: e.source, to: e.target, title: e.type, arrows: "to", color: {{color: "#64748b", opacity: 0.6}}
}})));
const network = new vis.Network(document.getElementById("g"), {{nodes, edges}}, {{
  physics: {{solver: "forceAtlas2Based", forceAtlas2Based: {{gravitationalConstant: -60, springLength: 90}},
            stabilization: {{iterations: 250}}}},
  interaction: {{hover: true, tooltipDelay: 100}}, edges: {{smooth: false, width: 1}}
}});
network.once("stabilizationIterationsDone", () => {{
  network.setOptions({{physics: false}});
  network.fit({{animation: {{duration: 400}}}});
}});
window.addEventListener("load", () => setTimeout(() => network.fit(), 300));
window.addEventListener("resize", () => network.fit());
</script></body></html>"""


def render():
    try:
        import kg
    except Exception as e:
        st.info(f"The knowledge graph isn't ready: {type(e).__name__}: {e}")
        return

    if st.button("🔄 Rebuild graph", key="graph-rebuild"):
        kg.get_graph(refresh=True)
    stats = kg.stats()
    by_type = stats["by_type"]
    metrics = st.columns(5)
    metrics[0].metric("Nodes", stats["nodes"])
    metrics[1].metric("Links", stats["edges"])
    metrics[2].metric("People", by_type.get("employee", 0))
    metrics[3].metric("Candidates", by_type.get("candidate", 0))
    metrics[4].metric("Decisions learned", by_type.get("decision", 0))

    term = st.text_input("Search the graph", placeholder="A skill (RxJS), a person (Priya), a project (SOW-112)…",
                         key="graph-search").strip()
    if term:
        st.json(kg.graph_search(term))

    page = _page(kg.get_graph())
    if hasattr(st, "iframe"):
        st.iframe(page, height=640)
    else:
        import streamlit.components.v1 as components
        components.html(page, height=640)
    st.caption("Hover a node or link for details. Built from our own data: employees, skills, projects, "
               "openings, candidates and recruiter decisions.")
