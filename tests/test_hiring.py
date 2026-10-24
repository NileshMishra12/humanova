"""Quick checks for the hiring tools. Run: python tests/test_hiring.py

Uses a temp data folder and offline mode, so it never touches the real data or spends tokens.
"""
import os
import runpy
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.environ["HR_DATA_DIR"] = tempfile.mkdtemp(prefix="humanova_hiring_")
os.environ["HR_MOCK"] = "1"
sys.path.insert(0, str(ROOT))

runpy.run_path(str(ROOT / "scripts" / "make_hiring_data.py"), run_name="__main__")

import config  # noqa: E402
import store  # noqa: E402
from tools import hiring_tools as h  # noqa: E402

results = []


def check(label, ok):
    results.append(bool(ok))
    print(f"{'PASS' if ok else 'FAIL'}  {label}")


rohan = (config.DATA_DIR / "resumes" / "OP1" / "01_rohan_verma.txt").read_text(encoding="utf-8")
anon = h.anonymise(rohan)
for gone in ("Rohan", "Verma", "@", "+91 98250 11111", "Male", "Age 29"):
    check(f"anonymise removes {gone!r}", gone not in anon)
for kept in ("Angular", "5 years"):
    check(f"anonymise keeps {kept!r}", kept in anon)

openings = h.list_openings().get("openings", [])
check("list_openings shows 5 candidates", len(openings) == 1 and openings[0]["candidates"] == 5)

ranked = h.screen_candidates("OP1").get("ranked", [])
names = [c["name"] for c in ranked]
check("Rohan ranks first", names[:1] == ["Rohan Verma"])
check("Deepak ranks last", names[-1:] == ["Deepak Shah"])
check("Meera > Sameer > Isha",
      all(n in names for n in ("Meera Joshi", "Sameer Khan", "Isha Gupta"))
      and names.index("Meera Joshi") < names.index("Sameer Khan") < names.index("Isha Gupta"))
check("next_step is interview for the first", bool(ranked) and ranked[0]["next_step"] == "interview")
check("next_step is reject for the last", bool(ranked) and ranked[-1]["next_step"] == "reject")

request = h.request_stage_change("C1", "Interview")
check("stage stays Applied after the request",
      request.get("status") == "pending" and h.get_candidate("C1")["candidate"]["stage"] == "Applied")
if "approval_id" in request:
    store.decide_approval(request["approval_id"], True, "test")
check("stage becomes Interview once approved", h.get_candidate("C1")["candidate"]["stage"] == "Interview")

email = h.draft_email("C2", "invite")
check("draft_email has the first name", "Meera" in email.get("body", ""))
check("draft_email is 'not sent'", "not sent" in email.get("status", ""))

questions = h.interview_questions("C2").get("questions", [])
check("interview_questions gives 5 different questions", len(questions) == 5 and len(set(questions)) == 5)

added = h.add_resume("OP1", "06_neha_patel.txt", b"Neha Patel\nneha.patel@mail.test\nAngular, TypeScript, 4 years")
check("add_resume registers a 6th candidate",
      added.get("candidate_id") == "C6" and h.list_openings()["openings"][0]["candidates"] == 6)

check("a bad opening id gives an error", "error" in h.screen_candidates("OP99"))

print(f"\n{sum(results)}/{len(results)} passed")
sys.exit(0 if all(results) else 1)
