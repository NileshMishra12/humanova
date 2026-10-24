"""Knowledge graph (owner: Lead), built from our own data: who works where, who knows what, which
projects and openings need which skills, and what recruiters decided and why.

Nodes: team, employee, skill, sow, opening, candidate, decision.
Edges: works in, reports to, knows, works on, for team, requires, nice to have, applied for, has, cites
(and "about", from a decision to its candidate).
"""
import re

import store

CACHE_SKIP = ("knowledge_graph.json", "audit_log.json", "approvals.json", "appraisals.json", "feedback.json")
_cache = {}


def norm(text):
    """Loose key so 'Node.js', 'nodejs' and 'node js' are one skill."""
    return re.sub(r"[^a-z0-9+#]", "", str(text).lower())


def mentions(skill, text):
    """Whole-word, case-insensitive match of a skill in free text."""
    return re.search(rf"(?i)(?<![\w.]){re.escape(skill)}(?![\w])", text or "") is not None


def _resume_text(candidate):
    data = store.read_bytes("resumes", candidate["opening_id"], candidate["file"]) if candidate.get("file") else None
    if data is None:
        return ""
    if candidate["file"].lower().endswith(".pdf"):
        try:
            from tools import hiring_tools
            return hiring_tools.extract_text(candidate["file"], data)
        except Exception:
            return ""
    return data.decode("utf-8", errors="ignore")


def build_graph():
    """Build the graph from the data files, save data/knowledge_graph.json, return {nodes, edges}."""
    try:  # auto-intake: registers resumes dropped into a folder since the last screening
        from tools import hiring_tools
        hiring_tools.list_openings()
    except Exception:
        pass

    nodes, edges = {}, []

    def node(node_id, kind, label, title):
        nodes.setdefault(node_id, {"id": node_id, "type": kind, "label": label, "title": title})
        return node_id

    def edge(source, target, kind):
        edges.append({"source": source, "target": target, "type": kind})

    def team(name):
        return node(f"team:{name}", "team", name, f"Team {name}") if name else None

    def skill(name):
        return node(f"skill:{norm(name)}", "skill", name, f"Skill: {name}")

    employees = store.read_csv("employees.csv")
    for e in employees:
        eid = node(f"emp:{e['id']}", "employee", e["name"], f"{e['name']}, {e['role']} ({e['team']})")
        edge(eid, team(e["team"]), "works in")
        for s in filter(None, (x.strip() for x in e.get("skills", "").split(";"))):
            edge(eid, skill(s), "knows")
    for e in employees:
        if e.get("manager_id"):
            edge(f"emp:{e['id']}", f"emp:{e['manager_id']}", "reports to")

    for p in store.read_json("sows.json"):
        pid = node(f"sow:{p['code']}", "sow", f"{p['code']} {p['title']}",
                   f"{p['code']} {p['title']}: due {p['due']}, {p.get('progress_pct')}% done, {p.get('status')}")
        if p.get("team"):
            edge(pid, team(p["team"]), "for team")
        for m in p.get("members", []):
            edge(f"emp:{m}", pid, "works on")

    openings = store.read_json("openings.json")
    for o in openings:
        oid = node(f"opening:{o['id']}", "opening", o["title"], f"Opening {o['id']}: {o['title']} ({o.get('team', '')})")
        if o.get("team"):
            edge(oid, team(o["team"]), "for team")
        for s in o.get("must_have", []):
            edge(oid, skill(s), "requires")
        for s in o.get("nice_to_have", []):
            edge(oid, skill(s), "nice to have")

    skills = [(n["id"], n["label"]) for n in nodes.values() if n["type"] == "skill"]
    for c in store.read_json("candidates.json"):
        cid = node(f"cand:{c['id']}", "candidate", c["name"],
                   f"{c['name']} ({c['id']}): stage {c.get('stage')}" + (f", score {c['score']}" if "score" in c else ""))
        edge(cid, f"opening:{c['opening_id']}", "applied for")
        text = _resume_text(c)
        for skill_id, label in skills:
            if mentions(label, text):
                edge(cid, skill_id, "has")

    for d in store.list_decisions():
        did = node(f"decision:{d['id']}", "decision", f"{d['decision']}: {d['reason'][:28] or 'no reason'}",
                   f"{d['decision'].title()} {d['candidate_id']} by {d['by']}: {d['reason'] or 'no reason given'}")
        edge(did, f"cand:{d['candidate_id']}", "about")
        for skill_id, label in skills:
            if mentions(label, d["reason"]):
                edge(did, skill_id, "cites")

    graph = {"nodes": list(nodes.values()),
             "edges": [e for e in edges if e["source"] in nodes and e["target"] in nodes]}
    store.write_json("knowledge_graph.json", graph)
    return graph


def get_graph(refresh=False):
    """The graph, rebuilt only when a source data file changed."""
    stamp = store.data_stamp(skip=CACHE_SKIP)
    if refresh or _cache.get("stamp") != stamp:
        _cache.update(stamp=stamp, graph=build_graph())
    return _cache["graph"]


def stats():
    g = get_graph()
    by_type = {}
    for n in g["nodes"]:
        by_type[n["type"]] = by_type.get(n["type"], 0) + 1
    return {"nodes": len(g["nodes"]), "edges": len(g["edges"]), "by_type": by_type}


def _connections(g, node_id, by_id):
    out = []
    for e in g["edges"]:
        if e["source"] == node_id:
            other, direction = by_id[e["target"]], "out"
        elif e["target"] == node_id:
            other, direction = by_id[e["source"]], "in"
        else:
            continue
        out.append({"relation": e["type"], "direction": direction, "type": other["type"], "label": other["label"]})
    return sorted(out, key=lambda c: (c["relation"], c["label"]))


def _search_skill(g, skill_node, by_id):
    sid, out = skill_node["id"], {"employees": [], "candidates": [], "openings": [], "decisions": [], "projects": []}
    knowers = set()
    for e in g["edges"]:
        if e["target"] != sid:
            continue
        src = by_id[e["source"]]
        if e["type"] == "knows":
            knowers.add(src["id"])
            out["employees"].append({"name": src["label"], "title": src["title"]})
        elif e["type"] == "has":
            out["candidates"].append({"name": src["label"], "title": src["title"]})
        elif e["type"] in ("requires", "nice to have"):
            out["openings"].append({"opening": src["label"], "relation": e["type"]})
        elif e["type"] == "cites":
            out["decisions"].append({"decision": src["label"], "detail": src["title"]})
    for p in (n for n in g["nodes"] if n["type"] == "sow"):
        members = [by_id[e["source"]]["label"] for e in g["edges"]
                   if e["type"] == "works on" and e["target"] == p["id"] and e["source"] in knowers]
        if members:
            out["projects"].append({"project": p["label"], "people_who_know_it": members})
    return out


def graph_search(term):
    """For a skill: who knows it, candidates with it, openings that need it, decisions citing it, projects.
    For anything else (a person, team, project, opening, candidate): its connections."""
    term = (term or "").strip()
    if not term:
        return {"error": "Give a skill, person, team, project or opening to look up"}
    g = get_graph()
    by_id = {n["id"]: n for n in g["nodes"]}
    skill_node = by_id.get(f"skill:{norm(term)}")
    if skill_node:
        return {"term": term, "kind": "skill", "skill": skill_node["label"], **_search_skill(g, skill_node, by_id)}

    words = re.findall(r"\w+", term.lower())
    exact = [n for n in g["nodes"] if n["type"] != "skill" and (
        n["id"].split(":", 1)[1].lower() == term.lower() or norm(n["label"]) == norm(term))]
    loose = [n for n in g["nodes"] if n["type"] != "skill" and words
             and all(w in re.findall(r"\w+", n["label"].lower()) for w in words)]
    matches = (exact or loose)[:5]
    if not matches:
        return {"error": f"Nothing in the graph matches '{term}'"}
    return {"term": term, "kind": "node",
            "matches": [{"type": n["type"], "label": n["label"], "detail": n["title"],
                         "connections": _connections(g, n["id"], by_id)} for n in matches]}
