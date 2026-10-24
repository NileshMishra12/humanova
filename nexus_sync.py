"""Nexus sync (owner: Lead). Read-only: pulls what the Nexus user is allowed to see and writes it as the data files
HumaNova already reads (employees, attendance, leaves, goals, reviews, nexus_work, sows), so no tool changes.

Employee-keyed files are always rewritten together, so ids never mix with the old dummy data. The previous files
are copied to data/_backup_before_nexus the first time. A part the Nexus user may not read is skipped and listed
in the warnings; its file is then empty rather than stale.
"""
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

import config
import store
from nexus_client import SYNC_FILE, NexusApi, NexusError

FILES = ("employees.csv", "attendance.csv", "leaves.csv", "goals.csv", "reviews.csv", "nexus_work.csv", "sows.json")
BACKUP = "_backup_before_nexus"
WORK_WEEKS = 12
ATTENDANCE_DAYS = 90
HISTORY_LIMIT = 300  # issue-history calls per sync (one per recently touched bug)
TASK_TYPES = ("TASK", "SUBTASK", "STORY")
LEAVE_KINDS = (("casual", "casual"), ("sick", "sick"), ("earned", "earned"), ("privilege", "earned"),
               ("annual", "earned"))
COLUMNS = {
    "employees.csv": ["id", "name", "email", "role", "team", "manager_id", "join_date", "location", "skills",
                      "casual_balance", "sick_balance", "earned_balance"],
    "attendance.csv": ["employee_id", "date", "status"],
    "leaves.csv": ["id", "employee_id", "type", "start", "end", "status"],
    "goals.csv": ["id", "employee_id", "title", "target_date", "progress_pct", "status"],
    "reviews.csv": ["employee_id", "cycle", "rating", "notes", "reviewer_id"],
    "nexus_work.csv": ["employee_id", "week", "days_worked", "tasks_done", "bugs_fixed", "bugs_reopened",
                       "on_time_pct", "sow"],
}


def _day(value, midnight_fix=False):
    """YYYY-MM-DD from a date or an ISO datetime. Nexus 'date' columns can arrive as local midnight in UTC, so
    with midnight_fix an evening 18:30Z (India midnight) is read as the next day."""
    if not value:
        return ""
    text = str(value)
    if len(text) == 10:
        return text
    try:
        stamp = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:10]
    if stamp.tzinfo:
        stamp = stamp.astimezone(timezone.utc)
    if midnight_fix and (stamp.hour, stamp.minute) == (18, 30):
        stamp += timedelta(days=1)
    return stamp.date().isoformat()


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _monday(day):
    d = date.fromisoformat(day) if isinstance(day, str) else day
    return d - timedelta(days=d.weekday())


class _Sync:
    def __init__(self, api):
        self.api, self.today = api, date.fromisoformat(config.TODAY)
        self.warnings, self.denied = [], set()
        self.people, self.by_user, self.by_hr = [], {}, {}
        self.projects, self.members = [], {}
        self.present_days, self.leave_days, self.attended = {}, {}, set()
        self.working_on = defaultdict(set)  # project id -> user ids with work touched in the window

    def attempt(self, label, fn, default=None):
        """Run one read. A refusal or error becomes a warning and the default, never a crash."""
        if label in self.denied:
            return default
        try:
            return fn()
        except NexusError as e:
            self.warnings.append(f"{label}: {e}")
            if "can't read" in str(e):
                self.denied.add(label)
        except Exception as e:  # an unexpected shape must not stop the whole sync
            self.warnings.append(f"{label}: unexpected data ({type(e).__name__}: {e})")
        return default

    # ---- people ----

    def load_projects(self):
        found = self.attempt("projects", lambda: self.api.get("/projects"), []) or []
        self.projects = [p for p in found if not p.get("isHidden")]
        for p in self.projects:
            self.members[p["id"]] = self.attempt(
                f"members of {p.get('name')}", lambda p=p: self.api.get(f"/projects/{p['id']}/members"), []) or []

    def _add_person(self, person):
        self.people.append(person)
        self.by_user[person["user_id"]] = person
        if person["hr_id"]:
            self.by_hr[person["hr_id"]] = person

    def load_people(self):
        everyone = self.attempt("employees", lambda: self.api.get("/hr/employees"))
        tier = "all employees"
        if everyone is None:
            tier = "your own record and your reportees"
            me = self.attempt("own employee record", lambda: self.api.get("/hr/employees/me"))
            reportees = self.attempt("reportees", lambda: self.api.get("/hr/employees/me/hierarchy-reportees"), [])
            everyone = ([me] if isinstance(me, dict) else []) + list(reportees or [])
        for e in everyone:
            if e.get("status") == "TERMINATED" or not e.get("userId") or e["userId"] in self.by_user:
                continue
            user, dept, desig = e.get("user") or {}, e.get("department") or {}, e.get("designation") or {}
            skills = e.get("skills") or []
            self._add_person({
                "id": e.get("employeeId") or e["id"], "user_id": e["userId"], "hr_id": e["id"],
                "name": user.get("name") or (self.api.user.get("name") if e["userId"] == self.api.user.get("id") else "")
                or e.get("employeeId") or "Unknown",
                "email": user.get("email") or "", "role": desig.get("title") or "", "team": dept.get("name") or "",
                "manager_hr": e.get("reportingManagerId"), "join_date": _day(e.get("dateOfJoining"), True),
                "location": e.get("workLocation") or e.get("city") or "",
                "skills": [s.get("name") if isinstance(s, dict) else str(s) for s in skills]})
        # anyone on a visible project who has no HR record yet (for example a non-admin login)
        for p in self.projects:
            for m in self.members[p["id"]]:
                if m.get("userId") and m["userId"] not in self.by_user:
                    self._add_person({"id": f"N-{m['userId'][:6]}", "user_id": m["userId"], "hr_id": None,
                                      "name": m.get("name") or "Unknown", "email": m.get("email") or "",
                                      "role": "Team member" if str(m.get("role") or "MEMBER").upper() == "MEMBER"
                                      else str(m["role"]).replace("_", " ").title(),
                                      "team": m.get("teamName") or "",
                                      "manager_hr": None, "join_date": "", "location": "",
                                      "skills": m.get("skills") or []})
        for person in self.people:
            person["team"] = person["team"] or "Unassigned"
        return tier

    def balances(self):
        totals = {}
        for person in self.people:
            if not person["hr_id"]:
                continue
            items = self.attempt("leave balances", lambda person=person: self.api.get(
                f"/hr/leave-types/balances/{person['hr_id']}"), []) or []
            kinds = Counter()
            for item in items:
                name = str((item.get("leaveType") or {}).get("name") or item.get("leaveType") or "").lower()
                left = _num(item.get("available"))
                if left is None:
                    left = (_num(item.get("totalBalance")) or 0) - (_num(item.get("usedBalance")) or 0)
                for key, kind in LEAVE_KINDS:
                    if key in name:
                        kinds[kind] += left
                        break
            totals[person["id"]] = kinds
        return totals

    # ---- attendance and leave ----

    def load_attendance(self):
        start = (self.today - timedelta(days=ATTENDANCE_DAYS)).isoformat()
        rows = self.attempt("attendance", lambda: self.api.get(
            "/hr/attendance", startDate=start, endDate=self.today.isoformat()))
        if rows is None:
            return []
        days = defaultdict(set)
        for r in rows:
            person = self.by_hr.get(r.get("employeeId"))
            if person and r.get("status") not in ("HOLIDAY", "WEEK_OFF"):
                days[(person["id"], _day(r.get("date")))].add(r.get("status"))
        out, self.present_days = [], defaultdict(int)
        self.attended = {pid for pid, _ in days}
        for (pid, day), statuses in sorted(days.items()):
            if statuses & {"PRESENT", "HALF_DAY"}:
                status = "present"
                self.present_days[(pid, _monday(day))] += 1
            else:
                status = "leave" if "ON_LEAVE" in statuses else "absent"
            out.append({"employee_id": pid, "date": day, "status": status})
        return out

    def load_leaves(self):
        rows = self.attempt("leaves", lambda: self.api.get("/hr/leaves"))
        if rows is None:  # no view-all: your reportees plus yourself
            rows = (self.attempt("team leaves", lambda: self.api.get("/hr/leaves/team"), []) or []) + \
                   (self.attempt("own leaves", lambda: self.api.get("/hr/leaves/my"), []) or [])
        out, self.leave_days = [], defaultdict(int)
        for r in rows:
            person = self.by_user.get((r.get("employee") or {}).get("id"))
            if not person:
                continue
            start, end = _day(r.get("startDate"), True), _day(r.get("endDate"), True)
            kind = str((r.get("leaveType") or {}).get("name") or "leave").lower().split()[0]
            status = str(r.get("status") or "").lower()
            out.append({"id": r.get("id"), "employee_id": person["id"], "type": kind, "start": start, "end": end,
                        "status": status})
            if status == "approved" and start and end:
                day = date.fromisoformat(start)
                while day <= date.fromisoformat(end):
                    if day.weekday() < 5:
                        self.leave_days[(person["id"], _monday(day))] += 1
                    day += timedelta(days=1)
        return out

    # ---- goals and reviews ----

    def load_performance(self):
        cycles = self.attempt("performance cycles", lambda: self.api.get("/hr/performance/cycles"), []) or []
        cycles = sorted(cycles, key=lambda c: str(c.get("startDate")))[-4:]
        goals, reviews = [], []
        for c in cycles:
            label, end = c.get("name") or c["id"], _day(c.get("endDate"), True)
            for g in self.attempt(f"goals of {label}", lambda c=c: self.api.get(
                    f"/hr/performance/goals/{c['id']}"), []) or []:
                person = self.by_hr.get(g.get("employeeId"))
                if not person:
                    continue
                target, achieved = _num(g.get("targetValue")), _num(g.get("achievedValue"))
                text = str(g.get("status") or "").lower()
                done = "complet" in text or "achiev" in text
                progress = 100 if done else min(100, round(100 * achieved / target)) if target and achieved else 0
                goals.append({"id": g.get("id"), "employee_id": person["id"], "title": g.get("title") or "",
                              "target_date": end, "progress_pct": progress,
                              "status": "done" if done else "in_progress"})
            for r in self.attempt(f"reviews of {label}", lambda c=c: self.api.get(
                    f"/hr/performance/reviews/{c['id']}"), []) or []:
                person, rating = self.by_hr.get(r.get("employeeId")), _num(r.get("overallRating"))
                if person and rating is not None and r.get("status") in ("FINALIZED", "ACCEPTED"):
                    reviewer = self.by_hr.get(r.get("reviewerId"))
                    notes = " ".join(x for x in (r.get("strengths"), r.get("improvements")) if x)
                    reviews.append({"employee_id": person["id"], "cycle": label, "rating": round(rating),
                                    "notes": notes, "reviewer_id": reviewer["id"] if reviewer else ""})
        return goals, reviews

    # ---- Nexus work and projects ----

    def weeks(self):
        monday = _monday(self.today)
        return [monday - timedelta(weeks=i) for i in reversed(range(WORK_WEEKS))]

    def load_work(self):
        weeks = self.weeks()
        first, last = weeks[0], weeks[-1] + timedelta(days=7)
        stats = defaultdict(lambda: {"tasks": 0, "bugs": 0, "reopened": 0, "due": 0, "on_time": 0, "sow": Counter()})
        contributors, history_calls = set(), 0
        for p in self.projects:
            code = p.get("code") or p.get("displayId") or p.get("name")
            states = {s["id"]: s for s in self.attempt(
                f"workflow of {p.get('name')}", lambda p=p: self.api.get("/workflows", projectId=p["id"]), []) or []}
            reopen_ids = {sid for sid, s in states.items() if s.get("isReopen")}
            issues = self.attempt(f"issues of {p.get('name')}", lambda p=p: self.api.get(
                f"/issues/project/{p['id']}"), []) or []
            if issues and states and not reopen_ids:
                self.warnings.append(f"{p.get('name')}: no workflow state is marked 'reopen', so reopened bugs show as 0")
            for issue in issues:
                owners = [self.by_user[u]["id"] for u in issue.get("assigneeIds") or [] if u in self.by_user]
                contributors.update(owners)
                if _day(issue.get("updatedAt")) >= first.isoformat():  # real involvement, not just being on the list
                    self.working_on[p["id"]].update(u for u in issue.get("assigneeIds") or [] if u in self.by_user)
                kind = issue.get("type")
                done_day = _day(issue.get("completedDate"))
                if states.get(issue.get("workflowStateId"), {}).get("category") == "DONE" and done_day \
                        and first <= _monday(done_day) < last:
                    week = _monday(done_day)
                    for pid in owners:
                        s = stats[(pid, week)]
                        if kind in TASK_TYPES:
                            s["tasks"] += 1
                        elif kind == "BUG":
                            s["bugs"] += 1
                        else:
                            continue
                        s["sow"][code] += 1
                        if issue.get("dueDate"):
                            s["due"] += 1
                            s["on_time"] += done_day <= _day(issue["dueDate"])
                if kind == "BUG" and reopen_ids and owners and _day(issue.get("updatedAt")) >= first.isoformat():
                    if history_calls >= HISTORY_LIMIT:
                        continue
                    history_calls += 1
                    events = self.attempt("issue history", lambda i=issue: self.api.get(
                        f"/issues/{i['id']}/history"), []) or []
                    for ev in events:
                        when = _day(ev.get("createdAt"))
                        if ev.get("field") == "status" and ev.get("newValue") in reopen_ids and ev.get("oldValue") \
                                and when and first <= _monday(when) < last:
                            for pid in owners:
                                stats[(pid, _monday(when))]["reopened"] += 1
        if history_calls >= HISTORY_LIMIT:
            self.warnings.append(f"issue history: read only {HISTORY_LIMIT} bugs, so very old reopens may be missing")

        rows = []
        for person in self.people:
            if person["id"] not in contributors:
                continue
            for week in weeks:
                s = stats[(person["id"], week)]
                if person["id"] in self.attended:
                    days = self.present_days.get((person["id"], week), 0)
                else:  # no attendance for this person: a full week minus approved leave
                    days = 5 - self.leave_days.get((person["id"], week), 0)
                rows.append({"employee_id": person["id"], "week": week.isoformat(), "days_worked": days,
                             "tasks_done": s["tasks"], "bugs_fixed": s["bugs"], "bugs_reopened": s["reopened"],
                             "on_time_pct": round(100 * s["on_time"] / s["due"]) if s["due"] else "",
                             "sow": s["sow"].most_common(1)[0][0] if s["sow"] else ""})
        missing = [p["name"] for p in self.people if p["id"] in contributors and p["id"] not in self.attended]
        if missing:
            self.warnings.append(f"days worked: no attendance for {len(missing)} contributor(s) (for example "
                                 f"{missing[0]}), so their weeks count 5 days minus approved leave")
        return rows

    def project_rows(self):
        out = []
        for p in self.projects:
            # members who actually have work on it; Nexus project lists often include everyone
            active = self.working_on.get(p["id"])
            people = [x for x in self.people if x["user_id"] in active] if active else                 [self.by_user[m["userId"]] for m in self.members[p["id"]] if m.get("userId") in self.by_user]
            owner = self.by_user.get(p.get("ownerId"))
            teams = Counter(x["team"] for x in people)
            due, progress = _day(p.get("endDate"), True), _num(p.get("progress")) or 0
            finished = progress >= 100 or str(p.get("status")).upper() in ("COMPLETED", "DONE", "CLOSED")
            out.append({"id": p.get("code") or p.get("displayId") or p.get("name"), "title": p.get("name"),
                        "team": (owner or {}).get("team") or (teams.most_common(1)[0][0] if teams else ""),
                        "members": [x["id"] for x in people], "due": due, "progress_pct": round(progress),
                        "status": "done" if finished else "overdue" if due and due < self.today.isoformat()
                        else "on_track"})
        return out


def sync(api=None):
    """Pull from Nexus and rewrite the data files. Returns {'ok', 'counts', 'warnings', 'time', 'tier'}."""
    api = api or NexusApi()
    api.login()
    run = _Sync(api)
    run.load_projects()
    tier = run.load_people()
    if not run.people:
        return {"ok": False, "warnings": run.warnings + ["Nothing readable: this Nexus user can see no employees or "
                                                          "projects, so no files were changed."]}
    balances = run.balances()
    attendance, leaves = run.load_attendance(), run.load_leaves()
    goals, reviews = run.load_performance()
    work, projects = run.load_work(), run.project_rows()
    by_id = {p["hr_id"]: p["id"] for p in run.people if p["hr_id"]}

    employees = [{"id": p["id"], "name": p["name"], "email": p["email"], "role": p["role"], "team": p["team"],
                  "manager_id": by_id.get(p["manager_hr"], ""), "join_date": p["join_date"],
                  "location": p["location"], "skills": ";".join(s for s in p["skills"] if s),
                  **{f"{k}_balance": round(balances.get(p["id"], {}).get(k, 0))
                     for k in ("casual", "sick", "earned")}} for p in run.people]

    store.backup_files(FILES, BACKUP)
    for name, rows in (("employees.csv", employees), ("attendance.csv", attendance), ("leaves.csv", leaves),
                       ("goals.csv", goals), ("reviews.csv", reviews), ("nexus_work.csv", work)):
        store.write_csv(name, rows, COLUMNS[name])
    store.write_json("sows.json", projects)

    counts = {"employees": len(employees), "attendance days": len(attendance), "leaves": len(leaves),
              "goals": len(goals), "reviews": len(reviews), "work rows": len(work), "projects": len(projects)}
    time = datetime.now().isoformat(timespec="seconds")
    store.write_json(SYNC_FILE, {"time": time, "base_url": api.base_url, "as": api.user.get("name"),
                                 "tier": tier, "counts": counts, "warnings": run.warnings})
    return {"ok": True, "time": time, "tier": tier, "counts": counts, "warnings": run.warnings}
