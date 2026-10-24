"""Learning layer (owner: Lead). HumaNova improves from what people decide, without retraining a model.

1. Recruiter decisions: a job skill named in a reject reason becomes a lesson for that opening, and one named
   in an advance reason becomes a valued skill. Screening then adjusts scores and says why.
2. Chat feedback: thumbs-down comments become corrections added to the agent's system prompt.
Everything is plain data (decisions.json, feedback.json), so it can be read, audited and reset.
"""
import store
from kg import mentions

PENALTY = 10      # per lesson skill the candidate is missing
MAX_PENALTY = 20
BONUS = 5         # per valued skill the candidate has


def _opening(opening_id):
    key = (opening_id or "").strip().lower()
    return next((o for o in store.read_json("openings.json") if key in (o["id"].lower(), o["title"].lower())), None)


def _next_step(score):
    return "interview" if score >= 75 else "hold" if score >= 50 else "reject"  # same thresholds as screening


def lessons_for(opening_id):
    """{'decisions': n, 'lessons': [skills recruiters rejected for], 'valued': [skills they advanced for]}"""
    job = _opening(opening_id)
    out = {"decisions": 0, "lessons": [], "valued": []}
    if not job:
        return out
    skills = job["must_have"] + job["nice_to_have"]
    for d in store.list_decisions(job["id"]):
        out["decisions"] += 1
        bucket = out["lessons"] if d["decision"] == "rejected" else out["valued"]
        bucket += [s for s in skills if mentions(s, d["reason"]) and s not in bucket]
    return out


def screen_with_learning(opening_id):
    """screen_candidates plus the adjustments learned from earlier recruiter decisions."""
    try:
        from tools import hiring_tools
    except ImportError:
        return {"error": "Hiring tools aren't available yet"}
    res = hiring_tools.screen_candidates(opening_id)
    if "error" in res:
        return res
    learned = lessons_for(res["opening_id"])
    lessons, valued = {s.lower(): s for s in learned["lessons"]}, {s.lower(): s for s in learned["valued"]}
    for c in res["ranked"]:
        missing = {s.lower() for s in c.get("missing", [])}
        matched = {s.lower() for s in c.get("matched", [])}
        lost = [lessons[k] for k in lessons if k in missing]
        gained = [valued[k] for k in valued if k in matched]
        penalty = min(PENALTY * len(lost), MAX_PENALTY)
        bonus = BONUS * len(gained)
        c["base_score"], c["learned_adjustment"], c["learned_note"] = c["score"], bonus - penalty, ""
        if not lost and not gained:
            continue
        c["score"] = max(0, min(100, c["score"] + bonus - penalty))
        c["next_step"] = _next_step(c["score"])
        notes = []
        if lost:
            notes.append(f"-{penalty}: missing {', '.join(lost)} "
                         f"(recruiters rejected for {'it' if len(lost) == 1 else 'them'})")
        if gained:
            notes.append(f"+{bonus}: has {', '.join(gained)} "
                         f"(recruiters advanced for {'it' if len(gained) == 1 else 'them'})")
        c["learned_note"] = "; ".join(notes)
    res["ranked"].sort(key=lambda c: -c["score"])
    res["learning"] = learned
    return res


def reject_candidate(candidate_id, reason, by):
    """A recruiter's own decision: it needs no approval. The reason is what HumaNova learns from."""
    try:
        from tools import hiring_tools
    except ImportError:
        return {"error": "Hiring tools aren't available yet"}
    found = hiring_tools.get_candidate(candidate_id)
    if "error" in found:
        return found
    cand = found["candidate"]
    hiring_tools.apply_stage_change(cand["id"], "Rejected")
    decision = store.record_decision(cand["id"], cand["opening_id"], "rejected", reason, by)
    return {"candidate_id": cand["id"], "stage": "Rejected", "decision": decision}


def corrections(limit=5):
    """The latest thumbs-down comments, phrased as rules for the agent's system prompt."""
    notes = [f for f in store.list_feedback("down") if f["comment"].strip()]
    return [f'When asked something like "{f["question"][:80]}": {f["comment"].strip()}' for f in notes[-limit:]]


def stats():
    feedback = store.list_feedback()
    return {"decisions": len(store.list_decisions()),
            "feedback_up": sum(1 for f in feedback if f["rating"] == "up"),
            "feedback_down": sum(1 for f in feedback if f["rating"] == "down"),
            "corrections": len(corrections(limit=10 ** 6))}
