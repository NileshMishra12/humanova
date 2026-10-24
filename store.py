"""All file reads/writes live here, so moving to Postgres later touches one module."""
import csv
import json
import uuid
from datetime import datetime

import config

AUDIT_LIMIT = 500


def _path(name):
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return config.DATA_DIR / name


def read_csv(name):
    path = _path(name)
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_csv(name, rows, fieldnames=None):
    if fieldnames is None:
        fieldnames = list(rows[0].keys()) if rows else []
    with _path(name).open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def read_json(name):
    path = _path(name)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(name, data):
    _path(name).write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def read_bytes(*parts):
    """A file under the data folder (e.g. a resume), or None if it isn't there."""
    path = config.DATA_DIR.joinpath(*parts)
    return path.read_bytes() if path.is_file() else None


def backup_files(names, folder):
    """Copy data files into data/<folder> once, so the first overwrite (for example by a Nexus sync) can be undone."""
    target = config.DATA_DIR / folder
    if target.exists():
        return False
    target.mkdir(parents=True)
    for name in names:
        if (config.DATA_DIR / name).is_file():
            (target / name).write_bytes((config.DATA_DIR / name).read_bytes())
    return True


def data_stamp(skip=()):
    """Changes whenever a data file changes (used to cache slow results). skip lists file names to ignore."""
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    return tuple(sorted((p.name, p.stat().st_mtime_ns) for p in config.DATA_DIR.iterdir()
                        if p.is_file() and p.name not in skip))


def _now():
    return datetime.now().isoformat(timespec="seconds")


def audit(user, tool, inputs, status):
    log = read_json("audit_log.json")
    log.append({"time": _now(), "user": user, "tool": tool, "inputs": inputs, "status": status})
    write_json("audit_log.json", log[-AUDIT_LIMIT:])


# ---- approvals ----

def create_approval(kind, target_id, change, requested_by, summary):
    approvals = read_json("approvals.json")
    approval = {
        "id": "AP-" + uuid.uuid4().hex[:6].upper(),
        "kind": kind,
        "target_id": target_id,
        "change": change,
        "requested_by": requested_by,
        "summary": summary,
        "status": "pending",
        "created": _now(),
    }
    approvals.append(approval)
    write_json("approvals.json", approvals)
    audit(requested_by, "create_approval", {"id": approval["id"], "kind": kind}, "pending")
    return approval


def list_approvals(status="pending"):
    approvals = read_json("approvals.json")
    return [a for a in approvals if status is None or a["status"] == status]


def decide_approval(approval_id, approve, by, reason=""):
    approvals = read_json("approvals.json")
    approval = next((a for a in approvals if a["id"] == approval_id), None)
    if approval is None:
        raise KeyError(f"No approval {approval_id}")
    if approval["status"] != "pending":
        raise ValueError(f"{approval_id} is already {approval['status']}")

    approval["status"] = "approved" if approve else "rejected"
    approval["decided_by"] = by
    approval["reason"] = reason
    approval["decided"] = _now()

    if approve and approval["kind"] == "candidate_stage":
        from tools import hiring_tools  # teammate's module; imported lazily
        hiring_tools.apply_stage_change(approval["target_id"], approval["change"]["stage"])

    if approval["kind"] == "appraisal":
        records = read_json("appraisals.json")
        records.append({
            "employee_id": approval["target_id"],
            **approval["change"],
            "status": approval["status"],
            "decided_by": by,
            "comment": reason,
            "time": approval["decided"],
        })
        write_json("appraisals.json", records)

    write_json("approvals.json", approvals)

    if approval["kind"] == "candidate_stage":  # a human decision on a candidate: HumaNova learns from it
        stage = approval["change"]["stage"]
        advance = approve != (stage == "Rejected")  # approving a move forward, or refusing a rejection
        candidate = next((c for c in read_json("candidates.json") if c["id"] == approval["target_id"]), None)
        if candidate:
            record_decision(candidate["id"], candidate["opening_id"], "advanced" if advance else "rejected",
                            reason, by, source="approval")

    audit(by, "decide_approval", {"id": approval_id, "approve": approve}, approval["status"])
    return approval


# ---- recruiter decisions and chat feedback (what HumaNova learns from) ----

def record_decision(candidate_id, opening_id, decision, reason, by, source="manual"):
    """decision is 'advanced' or 'rejected'."""
    if decision not in ("advanced", "rejected"):
        raise ValueError("decision must be 'advanced' or 'rejected'")
    decisions = read_json("decisions.json")
    record = {"id": f"D{len(decisions) + 1}", "time": _now(), "candidate_id": candidate_id,
              "opening_id": opening_id, "decision": decision, "reason": reason or "", "by": by, "source": source}
    decisions.append(record)
    write_json("decisions.json", decisions)
    audit(by, "record_decision", {"candidate_id": candidate_id, "decision": decision}, "ok")
    return record


def list_decisions(opening_id=None):
    return [d for d in read_json("decisions.json") if opening_id is None or d["opening_id"] == opening_id]


def add_feedback(question, answer, rating, comment, by):
    """rating is 'up' or 'down'."""
    if rating not in ("up", "down"):
        raise ValueError("rating must be 'up' or 'down'")
    feedback = read_json("feedback.json")
    record = {"id": f"F{len(feedback) + 1}", "time": _now(), "question": question, "answer": answer,
              "rating": rating, "comment": comment or "", "by": by}
    feedback.append(record)
    write_json("feedback.json", feedback)
    return record


def list_feedback(rating=None):
    return [f for f in read_json("feedback.json") if rating is None or f["rating"] == rating]
