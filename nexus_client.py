"""Nexus connector (owner: Lead). Everything HumaNova reads from Nexus goes through this module.

Today it reads a dummy export shaped like Nexus (data/nexus_work.csv, data/sows.json). To go live, change the
two functions below to call the Nexus API; nothing else in HumaNova needs to change. HumaNova only reads
Nexus numbers: it never writes to Nexus or re-measures its work.
"""
import store


def source():
    """Where the numbers come from, for labels in the UI and reports."""
    return {"name": "Nexus", "mode": "dummy export", "detail": "data/nexus_work.csv and data/sows.json",
            "live": False}


def work_rows(employee_id=None):
    """Weekly work rows: employee_id, week, days_worked, tasks_done, bugs_fixed, bugs_reopened,
    on_time_pct, sow. All employees, or just one."""
    return [r for r in store.read_csv("nexus_work.csv") if employee_id is None or r["employee_id"] == employee_id]


def projects():
    """SOW projects: code, title, team, members (employee ids), due, progress_pct, status."""
    return store.read_json("sows.json")
