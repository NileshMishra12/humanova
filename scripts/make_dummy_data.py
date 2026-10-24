"""Generates the demo dataset: an export from Perk (HR system) and Nexus (work tracker).

Run from the project root:  python scripts/make_dummy_data.py

Deterministic (random.Random(42)) and dated relative to config.TODAY, so every run gives the same
numbers. Running it again resets data/ to this starting state, including the empty approvals,
audit log, decisions, feedback and appraisals files.

The data carries the planted demo story:
  - Rahul (E03): attendance 76.7% and Nexus output sliding week after week
  - Neha (E06): goal overdue, on-time delivery slipping
  - Arjun (E07): rated 2 in H1, but Nexus shows a clear recovery
"""
import random
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
import store  # noqa: E402

WORKING_DAYS = 30
NEXUS_WEEKS = 12
WFH_CHANCE = 0.25

# id, name, role, team, manager_id, join_date, location, skills, casual, sick, earned
EMPLOYEES = [
    ("E01", "Anita Desai", "Engineering Manager", "Web", "", "2019-06-03", "Surat",
     "Leadership;Angular;Planning", 8, 6, 14),
    ("E02", "Priya Shah", "Frontend Developer", "Web", "E01", "2023-04-10", "Surat",
     "Angular;TypeScript;RxJS", 2, 5, 9),
    ("E03", "Rahul Mehta", "Backend Developer", "Web", "E01", "2022-01-17", "Ahmedabad",
     "Node.js;PostgreSQL;Docker", 6, 1, 7),
    ("E04", "Sneha Kulkarni", "QA Engineer", "Web", "E01", "2024-02-05", "Surat",
     "Selenium;Cypress;API testing", 5, 3, 6),
    ("E05", "Vikram Rao", "Engineering Manager", "Mobile", "", "2018-09-10", "Pune",
     "Leadership;Flutter;Architecture", 7, 6, 15),
    ("E06", "Neha Iyer", "Mobile Developer", "Mobile", "E05", "2023-08-21", "Pune",
     "Flutter;Dart;Firebase", 6, 4, 8),
    ("E07", "Arjun Patel", "Mobile Developer", "Mobile", "E05", "2021-11-01", "Surat",
     "Kotlin;Android;REST", 4, 6, 11),
    ("E08", "Kavya Nair", "UI/UX Designer", "Mobile", "E05", "2026-05-04", "Bengaluru",
     "Figma;Design systems;Prototyping", 7, 6, 5),
]

# id, employee_id, type, start, end, status
LEAVES = [
    ("L1", "E02", "casual", "2026-10-22", "2026-10-27", "approved"),
    ("L2", "E04", "sick", "2026-10-26", "2026-10-28", "approved"),
    ("L3", "E08", "earned", "2026-10-06", "2026-10-07", "approved"),
    ("L4", "E06", "casual", "2026-11-03", "2026-11-04", "pending"),
]

# Fixed absent days, so attendance never moves (Rahul: 23 of 30 days = 76.7%).
ABSENT = {
    "E03": ["2026-09-14", "2026-09-17", "2026-09-23", "2026-09-24",
            "2026-10-12", "2026-10-15", "2026-10-19"],
    "E04": ["2026-09-29"],
    "E08": ["2026-10-05"],
}

# id, employee_id, title, target_date, progress_pct, status
GOALS = [
    ("G1", "E02", "Migrate dashboard module to Angular 18", "2026-11-15", 70, "in_progress"),
    ("G2", "E02", "Add unit tests to reach 70% coverage", "2026-12-15", 40, "in_progress"),
    ("G3", "E03", "Cut API p95 latency below 300 ms", "2026-11-30", 55, "in_progress"),
    ("G4", "E04", "Automate regression suite in Cypress", "2026-11-20", 65, "in_progress"),
    ("G5", "E06", "Release offline mode for field app", "2026-10-10", 60, "in_progress"),
    ("G6", "E07", "Migrate payments screen to Jetpack Compose", "2026-12-01", 30, "in_progress"),
    ("G7", "E08", "Publish mobile design system v1", "2026-11-25", 80, "in_progress"),
    ("G8", "E01", "Hire two frontend developers", "2026-12-31", 20, "in_progress"),
    ("G9", "E05", "Ship release 4.0 of the mobile app", "2026-09-30", 100, "done"),
]

REVIEW_CYCLE = "2026-H1"
# employee_id, rating, notes. The reviewer is the employee's manager; managers have no review.
REVIEWS = [
    ("E02", 4, "Delivers Angular work consistently and reviews teammates' pull requests with care. "
               "Next step is to own a larger module end to end."),
    ("E03", 3, "Solid backend work and a dependable owner of the billing services. "
               "Needs to raise blockers earlier and keep estimates realistic."),
    ("E04", 4, "Thorough test plans that caught several release blockers early. "
               "A good partner to developers when reproducing bugs."),
    ("E06", 3, "Good grasp of Flutter and Firebase and picks up new work quickly. "
               "Needs to tighten estimates and follow through on the offline mode milestones."),
    ("E07", 2, "Code quality concerns this cycle: several merge requests needed repeated rework and "
               "bugs were reopened. An improvement plan is in place with weekly check-ins with the manager."),
    ("E08", 4, "Settled in quickly after joining in May and shipped clear, well-documented design components. "
               "Works well with the mobile developers."),
]

SOW_OF = {"E01": "SOW-112", "E02": "SOW-112", "E04": "SOW-112", "E03": "SOW-115",
          "E05": "SOW-118", "E06": "SOW-118", "E07": "SOW-118", "E08": "SOW-121"}

SOWS = [
    {"id": "SOW-112", "title": "Client portal dashboard revamp", "team": "Web",
     "members": ["E02", "E03", "E04"], "due": "2026-11-30", "progress_pct": 65, "status": "on_track"},
    {"id": "SOW-115", "title": "Billing API v2", "team": "Web",
     "members": ["E03"], "due": "2026-11-15", "progress_pct": 40, "status": "at_risk"},
    {"id": "SOW-118", "title": "Field app offline mode", "team": "Mobile",
     "members": ["E06", "E07"], "due": "2026-10-10", "progress_pct": 60, "status": "overdue"},
    {"id": "SOW-121", "title": "Mobile design system", "team": "Mobile",
     "members": ["E08", "E07"], "due": "2026-11-25", "progress_pct": 80, "status": "on_track"},
]

EMPTY_FILES = ["approvals.json", "audit_log.json", "decisions.json", "feedback.json", "appraisals.json"]


def iso_days(start, end):
    d = date.fromisoformat(start)
    stop = date.fromisoformat(end)
    while d <= stop:
        yield d
        d += timedelta(days=1)


def last_working_days(today, n):
    days, d = [], today
    while len(days) < n:
        if d.weekday() < 5:
            days.append(d)
        d -= timedelta(days=1)
    return days[::-1]


def approved_leave_days(emp_id):
    return {d for _id, e, _t, start, end, status in LEAVES
            if e == emp_id and status == "approved" for d in iso_days(start, end)}


def absent_days(emp_id):
    return {date.fromisoformat(d) for d in ABSENT.get(emp_id, [])}


def pct(x):
    x = round(x, 1)
    return int(x) if x == int(x) else x


def employee_rows():
    rows = []
    for id_, name, role, team, manager_id, join_date, location, skills, casual, sick, earned in EMPLOYEES:
        first, last = name.lower().split()
        rows.append({"id": id_, "name": name, "email": f"{first}.{last}@company.test", "role": role,
                     "team": team, "manager_id": manager_id, "join_date": join_date,
                     "location": location, "skills": skills, "casual_balance": casual,
                     "sick_balance": sick, "earned_balance": earned})
    return rows


def leave_rows():
    return [dict(zip(("id", "employee_id", "type", "start", "end", "status"), row)) for row in LEAVES]


def attendance_rows(rng, today):
    rows = []
    for emp in EMPLOYEES:
        id_ = emp[0]
        on_leave, absent = approved_leave_days(id_), absent_days(id_)
        for d in last_working_days(today, WORKING_DAYS):
            if d in absent:
                status = "absent"
            elif d in on_leave:
                status = "leave"
            else:
                status = "wfh" if rng.random() < WFH_CHANCE else "present"
            rows.append({"employee_id": id_, "date": d.isoformat(), "status": status})
    return rows


def goal_rows():
    return [dict(zip(("id", "employee_id", "title", "target_date", "progress_pct", "status"), row))
            for row in GOALS]


def review_rows():
    manager = {e[0]: e[4] for e in EMPLOYEES}
    return [{"employee_id": id_, "cycle": REVIEW_CYCLE, "rating": rating, "notes": notes,
             "reviewer_id": manager[id_]} for id_, rating, notes in REVIEWS]


def week_pattern(emp_id, i, rng):
    """(tasks per day, bugs fixed, bugs reopened, on-time %) for week index i (0 = oldest)."""
    u, r = rng.uniform, rng.randint
    if emp_id in ("E01", "E05"):
        return u(0.4, 0.6), r(0, 1), 0, r(92, 98)
    if emp_id == "E02":
        return u(1.7, 2.0), r(3, 5), r(0, 1), r(92, 97)
    if emp_id == "E03":
        if i <= 5:
            return u(1.5, 1.7), r(3, 4), r(0, 1), r(88, 93)
        return 1.6 - 0.12 * (i - 5), r(1, 3), r(2, 3), 88 - 4 * (i - 5)
    if emp_id == "E06":
        return u(1.2, 1.4), r(2, 3), r(0, 1), 88 - 2.4 * i
    if emp_id == "E07":
        if i <= 4:
            return u(0.8, 0.95), r(1, 2), r(2, 3), r(58, 64)
        return (min(0.95 + 0.11 * (i - 4), 1.7), r(3, 4), 1 if rng.random() < 0.2 else 0,
                min(66 + 4 * (i - 4), 92))
    return u(1.3, 1.5), r(2, 4), r(0, 1), r(90, 96)  # E04, E08


def nexus_rows(rng, today):
    this_monday = today - timedelta(days=today.weekday())
    weeks = [this_monday - timedelta(weeks=NEXUS_WEEKS - 1 - i) for i in range(NEXUS_WEEKS)]
    rows = []
    for emp in EMPLOYEES:
        id_ = emp[0]
        off = approved_leave_days(id_)
        if id_ == "E03":  # Nexus mirrors Rahul's absences; that is the story
            off |= absent_days(id_)
        for i, monday in enumerate(weeks):
            week_days = [monday + timedelta(days=k) for k in range(5)]
            days_worked = sum(1 for d in week_days if d <= today and d not in off)
            per_day, fixed, reopened, on_time = week_pattern(id_, i, rng)
            if days_worked == 0:
                fixed = reopened = 0
            rows.append({"employee_id": id_, "week": monday.isoformat(), "days_worked": days_worked,
                         "tasks_done": round(per_day * days_worked), "bugs_fixed": fixed,
                         "bugs_reopened": reopened, "on_time_pct": pct(on_time), "sow": SOW_OF[id_]})
    return rows


def main():
    today = date.fromisoformat(config.TODAY)
    rng = random.Random(42)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)

    tables = {
        "employees.csv": (employee_rows(), ["id", "name", "email", "role", "team", "manager_id", "join_date",
                                            "location", "skills", "casual_balance", "sick_balance",
                                            "earned_balance"]),
        "leaves.csv": (leave_rows(), ["id", "employee_id", "type", "start", "end", "status"]),
        "attendance.csv": (attendance_rows(rng, today), ["employee_id", "date", "status"]),
        "goals.csv": (goal_rows(), ["id", "employee_id", "title", "target_date", "progress_pct", "status"]),
        "reviews.csv": (review_rows(), ["employee_id", "cycle", "rating", "notes", "reviewer_id"]),
        "nexus_work.csv": (nexus_rows(rng, today), ["employee_id", "week", "days_worked", "tasks_done",
                                                    "bugs_fixed", "bugs_reopened", "on_time_pct", "sow"]),
    }
    for name, (rows, fields) in tables.items():
        store.write_csv(name, rows, fields)
    store.write_json("sows.json", SOWS)
    for name in EMPTY_FILES:
        store.write_json(name, [])

    counts = ", ".join(f"{len(rows)} {name.split('.')[0]}" for name, (rows, _) in tables.items())
    print(f"Wrote {counts}, {len(SOWS)} sows and {len(EMPTY_FILES)} empty files to {config.DATA_DIR} "
          f"(as of {config.TODAY})")


if __name__ == "__main__":
    main()
