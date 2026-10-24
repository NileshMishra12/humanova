"""Reads HR's appraisal policy. All rules live in policies/appraisal_policy.json, never in code."""
import json

import config

POLICY_FILE = config.ROOT / "policies" / "appraisal_policy.json"


def load():
    policy = json.loads(POLICY_FILE.read_text(encoding="utf-8"))
    policy["bands"] = sorted(policy["bands"], key=lambda b: b["min_score"], reverse=True)
    return policy


def band_for(score):
    """The highest band whose minimum score the score reaches."""
    bands = load()["bands"]
    return next((b for b in bands if score >= b["min_score"]), bands[-1])
