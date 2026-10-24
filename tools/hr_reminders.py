"""HR reminders: probation, wellbeing, workload, leave balance and increment nudges.

People, join dates and balances come from employee_tools; leave and SOWs are read through store.py.
Returns a plain dict and never raises.
"""
import functools
from datetime import date, timedelta

import config
import store
from tools import employee_tools

PROBATION_MONTHS = 6         # probation lasts this long from the join date
PROBATION_REVIEW_DAYS = 45   # a probation review is "due" when it ends within this many days
INCREMENT_MIN_MONTHS = 12    # service needed before the annual increment cycle
LEAVE_LOOKBACK_DAYS = 90     # wellbeing: look for approved leave in this many days
EARNED_UNUSED_MIN = 10       # wellbeing: earned days left at or above this
CASUAL_LOW_MAX = 2           # leave balance: casual days left at or below this

TYPE_ORDER = ["probation", "wellbeing", "workload", "leave balance", "increment"]


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            return {"error": f"{fn.__name__} failed: {e}"}
    return wrapper


def _to_date(value):
    try:
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _add_months(d, months):
    """Same day number `months` later; day 28 when that month is shorter."""
    year, month = divmod(d.month - 1 + months, 12)
    year, month = d.year + year, month + 1
    try:
        return date(year, month, d.day)
    except ValueError:
        return date(year, month, 28)


def _build(people):
    today = date.fromisoformat(config.TODAY)
    names = {p["id"]: p["name"] for p in people}
    window_start = today - timedelta(days=LEAVE_LOOKBACK_DAYS)
    had_leave = set()
    for l in store.read_csv("leaves.csv"):
        start, end = _to_date(l["start"]), _to_date(l["end"])
        if l["status"].strip().lower() == "approved" and start and end and end >= window_start and start <= today:
            had_leave.add(l["employee_id"])
    out = []

    def add(kind, name, due, detail):
        out.append({"type": kind, "name": name, "due": due.isoformat(), "detail": detail})

    for p in people:
        res = employee_tools.get_employee(p["id"])
        if "error" in res:
            continue
        e = res["employee"]
        joined = _to_date(e["join_date"])
        if joined:
            probation_end = _add_months(joined, PROBATION_MONTHS)
            if today < probation_end:
                if (probation_end - today).days <= PROBATION_REVIEW_DAYS:
                    detail = f"Probation review due {probation_end} (joined {joined})"
                else:
                    detail = f"In probation until {probation_end}"
                add("probation", e["name"], probation_end, detail)
            eligible = _add_months(joined, INCREMENT_MIN_MONTHS)
            if today < eligible:
                add("increment", e["name"], eligible, "Not yet eligible for the annual increment cycle")
        balance = e["leave_balance"]
        if balance["earned"] >= EARNED_UNUSED_MIN and e["id"] not in had_leave:
            add("wellbeing", e["name"], today, f"No leave in the last {LEAVE_LOOKBACK_DAYS} days and "
                f"{balance['earned']} earned days unused: suggest planning a break")
        if balance["casual"] <= CASUAL_LOW_MAX:
            days = "day" if balance["casual"] == 1 else "days"
            add("leave balance", e["name"], today, f"Only {balance['casual']} casual leave {days} left")

    for sow in store.read_json("sows.json"):
        due = _to_date(sow.get("due"))
        members = [names[m] for m in sow.get("members", []) if m in names]
        if due and due < today and str(sow.get("status", "")).strip().lower() != "done" and members:
            add("workload", ", ".join(members), due,
                f"{sow.get('id')} {sow.get('title')} is overdue (from Nexus): check workload and support")

    out.sort(key=lambda r: (TYPE_ORDER.index(r["type"]), r["due"], r["name"]))
    return out


@_safe
def hr_reminders(team=None):
    people = employee_tools.list_employees(team)
    if "error" in people:
        return people
    return {"reminders": _build(people["employees"])}
