"""Quick checks for Member A's work against the planted demo story. Plain script, no pytest needed.

Run from anywhere:  python tests/test_employee.py
Builds fresh dummy data in a temp folder (your real data/ is never touched), then prints PASS or FAIL per check.
"""
import contextlib
import io
import os
import runpy
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TMP = tempfile.TemporaryDirectory()
os.environ["HR_DATA_DIR"] = TMP.name   # must be set before config is imported
os.environ["HR_MOCK"] = "1"            # offline: no Claude calls
os.environ["HR_TODAY"] = "2026-10-23"  # the planted story is dated for this Friday
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
from tools import employee_tools, hr_reminders  # noqa: E402

CHECKS = []


def check(label):
    def register(fn):
        CHECKS.append((label, fn))
        return fn
    return register


def week(start):
    return start.isoformat(), (start + timedelta(days=4)).isoformat()


def names_on_leave(start, end):
    return sorted(p["name"] for p in employee_tools.who_is_on_leave(start, end)["on_leave"])


@check("Web team has 4 people")
def _():
    names = [e["name"] for e in employee_tools.list_employees("Web")["employees"]]
    return len(names) == 4, names


@check("Priya is on leave today")
def _():
    status = employee_tools.get_employee("Priya")["employee"]["status_today"]
    return status == "leave", status


@check("Priya is the only one on leave this week")
def _():
    today = date.fromisoformat(config.TODAY)
    names = names_on_leave(*week(today - timedelta(days=today.weekday())))
    return names == ["Priya Shah"], names


@check("Next week it's Priya and Sneha")
def _():
    today = date.fromisoformat(config.TODAY)
    names = names_on_leave(*week(today - timedelta(days=today.weekday() - 7)))
    return names == ["Priya Shah", "Sneha Kulkarni"], names


@check("Rahul has 30 days, 7 absent, under 85%")
def _():
    a = employee_tools.get_attendance("Rahul")
    return (a["days"], a["absent"]) == (30, 7) and a["attendance_pct"] < 85, a


@check("Rahul's attendance is 76.7%")
def _():
    pct = employee_tools.get_attendance("Rahul")["attendance_pct"]
    return pct == 76.7, pct


@check("Flags are exactly Rahul attendance, Neha goal_overdue, Arjun low_rating")
def _():
    flags = sorted((f["employee"], f["rule"]) for f in employee_tools.get_attention_flags()["flags"])
    want = [("Arjun Patel", "low_rating"), ("Neha Iyer", "goal_overdue"), ("Rahul Mehta", "attendance")]
    return flags == want, flags


@check("Team overview for everyone is 8 / 7 / 1 / 3")
def _():
    o = employee_tools.team_overview()
    got = (o["headcount"], o["present_today"], o["on_leave_today"], o["flagged"])
    return got == (8, 7, 1, 3), got


@check('"neha" finds Neha Iyer')
def _():
    name = employee_tools.get_employee("neha").get("employee", {}).get("name")
    return name == "Neha Iyer", name


@check("An unknown name returns an error")
def _():
    res = employee_tools.get_employee("Nobody Here")
    return "error" in res and "employee" not in res, res


@check("Kavya has a probation reminder")
def _():
    reminders = hr_reminders.hr_reminders()["reminders"]
    hits = [r for r in reminders if r["type"] == "probation" and r["name"] == "Kavya Nair"]
    return len(hits) == 1, hits


def main():
    if Path(config.DATA_DIR) != Path(TMP.name):
        print(f"FAIL  setup: refusing to run, config.DATA_DIR is {config.DATA_DIR}, not the temp folder")
        return 1
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            runpy.run_path(str(ROOT / "scripts" / "make_dummy_data.py"), run_name="__main__")
    except Exception as e:
        print(f"FAIL  setup: make_dummy_data.py crashed: {type(e).__name__}: {e}")
        return 1

    passed = 0
    for label, fn in CHECKS:
        try:
            ok, detail = fn()
        except Exception as e:
            ok, detail = False, f"{type(e).__name__}: {e}"
        passed += bool(ok)
        print(f"{'PASS' if ok else 'FAIL'}  {label}" + ("" if ok else f"\n      got: {detail}"))
    print(f"\n{passed} of {len(CHECKS)} checks passed" + ("" if passed == len(CHECKS) else " - FAILED"))
    return 0 if passed == len(CHECKS) else 1


if __name__ == "__main__":
    code = main()
    TMP.cleanup()
    sys.exit(code)
