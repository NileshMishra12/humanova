"""Regression tests: safe file writes and the 'is this a CV?' check on uploads.
Run: python tests/test_storage_and_uploads.py
"""
import json
import os
import sys
import tempfile
import threading
from pathlib import Path

DATA = tempfile.mkdtemp(prefix="humanova_storage_")
os.environ["HR_DATA_DIR"] = DATA
os.environ["HR_MOCK"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import store  # noqa: E402
from tools import hiring_tools  # noqa: E402

failures = []


def check(name, ok):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        failures.append(name)


# ---- storage ----

path = Path(DATA) / "audit_log.json"
good = [{"n": i} for i in range(99)]
path.write_text(json.dumps(good, indent=2) + "\n    }\n]", encoding="utf-8")  # a write that was cut short and overlapped
check("a file with leftovers after the data is read", store.read_json("audit_log.json") == good)
check("and is repaired on disk", json.loads(path.read_text(encoding="utf-8")) == good)

path.write_text("not json at all", encoding="utf-8")
try:
    store.read_json("audit_log.json")
    check("a hopeless file raises a clear error", False)
except ValueError as e:
    check("a hopeless file raises a clear error", "audit_log.json" in str(e))
path.unlink()

errors = []


def worker(n):
    try:
        for i in range(40):
            store.audit(f"user{n}", "tool", {"i": i}, "ok")
            store.record_decision(f"C{n}", "OP1", "rejected", f"reason {i}", f"user{n}")
    except Exception as e:  # noqa: BLE001
        errors.append(e)


threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
[t.start() for t in threads]
[t.join() for t in threads]
check("8 threads writing at once raise no errors", not errors)
log = json.loads(path.read_text(encoding="utf-8"))
check("the audit log stays valid JSON and keeps its cap", isinstance(log, list) and len(log) <= store.AUDIT_LIMIT)
decisions = store.read_json("decisions.json")
check("no decision is lost when threads write together", len(decisions) == 8 * 40)
check("decision ids are unique", len({d["id"] for d in decisions}) == len(decisions))
check("no temporary files are left behind", not list(Path(DATA).glob("*.tmp")))

store.write_csv("x.csv", [{"a": "1", "b": "two"}])
check("csv round-trips", store.read_csv("x.csv") == [{"a": "1", "b": "two"}])
store.write_csv("empty.csv", [], ["a", "b"])
check("an empty csv keeps its header", (Path(DATA) / "empty.csv").read_text(encoding="utf-8").startswith("a,b"))

# ---- is this a CV? ----

CV = ("Priya Nair\npriya@mail.com | +91 98765 43210\nSenior QA Engineer - 6 years experience\n"
      "Skills: Selenium, Python, API testing, CI/CD\nExperience:\n- 2020-present: QA Engineer, Acme\n"
      "Education: B.Tech Computer Science, 2018\n")
FUND_REPORT = ("ASHISH SHAH (AMFI REGISTERED MUTUAL FUND DISTRIBUTER) Email: a@yahoo.com Phone: 9820652260 "
               "Eligible for Redemption Family Head Name Client Name Fund Name Folio No. Total Units Market Value "
               "Mirae Asset Midcap Direct-G 79987606054 18,304.46 7,59,433.87 0.00 0.00 18,304.46 7,59,433 "
               "HDFC Focused Direct-G 41375789/41 3,904.41 10,15,930.87 0.00 0.00 3,904.41 10,15,930 Total 71,81,303.61")
INVOICE = "INVOICE No 4521. Total amount due 12,400.00. Balance 3,000.00. Statement of account for March 2026. Receipt of payment. " * 2
check("a normal CV is accepted", hiring_tools.looks_like_resume(CV)[0])
check("the sample resumes are accepted", all(hiring_tools.looks_like_resume(f.read_text(encoding="utf-8"))[0]
                                             for f in (Path(__file__).resolve().parent.parent / "data" / "resumes" / "OP1").glob("*.txt")))
check("a mutual-fund report is refused", not hiring_tools.looks_like_resume(FUND_REPORT)[0])
check("an invoice is refused", not hiring_tools.looks_like_resume(INVOICE)[0])
check("an empty file is refused", not hiring_tools.looks_like_resume("")[0])

hiring_tools.create_opening("QA Engineer", ["Selenium", "Python"], team="QA")
opening = store.read_json("openings.json")[-1]["id"]
bad = hiring_tools.add_resume(opening, "report.txt", FUND_REPORT)
check("uploading a non-CV returns an error saying why", "error" in bad and "doesn't read like a CV" in bad["error"])
check("a refused upload is not saved as a candidate", not store.read_json("candidates.json") and
      not (Path(DATA) / "resumes" / opening / "report.txt").exists())
good_up = hiring_tools.add_resume(opening, "priya.txt", CV)
check("uploading a real CV still works", "candidate_id" in good_up)
tiny = hiring_tools.add_resume(opening, "tiny.txt", "Sam Roy\nsam@mail.test\nSelenium, Python, 3 years")
check("a very short CV that names the job's skills still works", "candidate_id" in tiny)
check("the same short text without those skills is refused",
      "error" in hiring_tools.add_resume(opening, "tiny2.txt", "Sam Roy\nsam@mail.test\nGardening, 3 years"))

print(f"\n{'ALL PASSED' if not failures else str(len(failures)) + ' FAILED'}")
sys.exit(1 if failures else 0)
