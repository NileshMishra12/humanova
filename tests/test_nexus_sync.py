"""Nexus sync tests against a fake Nexus server (shapes taken from the Nexus code). No credentials needed.
Run: python tests/test_nexus_sync.py
"""
import json
import os
import re
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA = tempfile.mkdtemp(prefix="humanova_nexus_")
os.environ.update(HR_DATA_DIR=DATA, HR_TODAY="2026-10-23", NEXUS_EMAIL="asha@test.local", NEXUS_PASSWORD="pw-secret-123")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import nexus_client  # noqa: E402
import nexus_sync  # noqa: E402
import store  # noqa: E402

failures = []


def check(name, ok):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        failures.append(name)


# ---- the fake Nexus ----

def emp(hr, user, code, name, team, title, manager=None, status="ACTIVE"):
    return {"id": hr, "userId": user, "employeeId": code, "status": status, "reportingManagerId": manager,
            "dateOfJoining": "2025-01-10T00:00:00.000Z", "workLocation": "Surat",
            "skills": [{"name": "Angular", "level": "SENIOR"}], "nationalId": "SECRET",
            "user": {"id": user, "name": name, "email": f"{code.lower()}@x.test"},
            "department": {"id": team, "name": team, "code": team[:3]}, "designation": {"id": title, "title": title}}


ASHA, RAVI = emp("hr1", "u1", "E001", "Asha Rao", "Web", "Engineering Manager"), \
    emp("hr2", "u2", "E002", "Ravi Kumar", "Web", "Developer", "hr1")
MEENA, OLD = emp("hr3", "u3", "E003", "Meena Iyer", "Web", "Developer", "hr1"), \
    emp("hr9", "u9", "E009", "Gone Person", "Web", "Developer", status="TERMINATED")
STATE = [{"id": "s_todo", "category": "PENDING", "isReopen": False}, {"id": "s_done", "category": "DONE", "isReopen": False},
         {"id": "s_reopen", "category": "IN_PROGRESS", "isReopen": True}]


def issue(i, kind, owner, state, done=None, due=None, updated="2026-10-01T00:00:00Z"):
    return {"id": i, "type": kind, "assigneeIds": [owner], "workflowStateId": state, "completedDate": done,
            "dueDate": due, "updatedAt": updated}


ROUTES = {
    "/projects": [{"id": "p1", "name": "Portal", "code": "PRJ-1", "status": "ACTIVE", "ownerId": "u1", "progress": 60,
                   "endDate": "2026-10-10T00:00:00.000Z", "isHidden": False},
                  {"id": "p2", "name": "Secret", "code": "PRJ-2", "isHidden": True}],
    "/projects/p1/members": [{"userId": "u2", "name": "Ravi Kumar", "role": "DEVELOPER", "teamName": "Web"},
                             {"userId": "u3", "name": "Meena Iyer", "role": "DEVELOPER", "teamName": "Web"}],
    "/hr/employees": [ASHA, RAVI, MEENA, OLD],
    "/hr/employees/me": ASHA,
    "/hr/employees/me/hierarchy-reportees": [RAVI],
    "/hr/attendance": [{"employeeId": "hr2", "date": f"2026-10-{d}", "status": "PRESENT"} for d in (19, 19, 20, 21, 22)]
                      + [{"employeeId": "hr2", "date": "2026-10-23", "status": "ON_LEAVE"},
                         {"employeeId": "hr2", "date": "2026-10-24", "status": "WEEK_OFF"}],
    "/hr/leaves": [{"id": "L1", "status": "APPROVED", "startDate": "2026-10-23T00:00:00.000Z",
                    "endDate": "2026-10-23T00:00:00.000Z", "employee": {"id": "u2", "employeeId": "E002"},
                    "leaveType": {"name": "Casual Leave"}}],
    "/hr/leaves/team": [{"id": "L1", "status": "APPROVED", "startDate": "2026-10-23T00:00:00.000Z",
                         "endDate": "2026-10-23T00:00:00.000Z", "employee": {"id": "u2", "employeeId": "E002"},
                         "leaveType": {"name": "Casual Leave"}}],
    "/hr/leaves/my": [],
    "/hr/leave-types/balances/*": [{"leaveType": {"name": "Casual Leave"}, "available": 7},
                                   {"leaveType": {"name": "Sick Leave"}, "totalBalance": 10, "usedBalance": 3},
                                   {"leaveType": {"name": "Earned Leave"}, "available": 12}],
    "/hr/performance/cycles": [{"id": "c1", "name": "2026-H1", "startDate": "2026-01-01", "endDate": "2026-06-30"}],
    "/hr/performance/goals/c1": [
        {"id": "g1", "employeeId": "hr2", "title": "Ship portal", "targetValue": "10", "achievedValue": "4", "status": "IN_PROGRESS"},
        {"id": "g2", "employeeId": "hr2", "title": "Docs", "targetValue": "1", "achievedValue": "1", "status": "COMPLETED"}],
    "/hr/performance/reviews/c1": [
        {"employeeId": "hr2", "reviewerId": "hr1", "overallRating": "4.0", "strengths": "Fast", "improvements": "Docs",
         "status": "FINALIZED"}, {"employeeId": "hr3", "overallRating": "2", "status": "DRAFT"}],
    "/workflows": STATE,
    "/issues/project/p1": [
        issue("i1", "TASK", "u2", "s_done", "2026-10-20T10:00:00Z", "2026-10-21T00:00:00Z"),
        issue("i2", "TASK", "u2", "s_done", "2026-10-22T10:00:00Z", "2026-10-21T00:00:00Z"),
        issue("i3", "BUG", "u2", "s_done", "2026-10-14T10:00:00Z", None),
        issue("i4", "BUG", "u3", "s_reopen", updated="2026-10-21T09:00:00Z"),
        issue("i5", "EPIC", "u2", "s_done", "2026-10-20T10:00:00Z", None)],
    "/issues/i4/history": [{"field": "status", "oldValue": "s_done", "newValue": "s_reopen", "createdAt": "2026-10-21T09:00:00Z"},
                           {"field": "status", "oldValue": "", "newValue": "s_todo", "createdAt": "2026-10-01T09:00:00Z"}],
}
SCENARIO = {"deny": set(), "expire_once": False}


class Fake(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, code, body):
        raw = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/api/auth/login" and body == {"email": "asha@test.local", "password": "pw-secret-123"}:
            SCENARIO["logins"] = SCENARIO.get("logins", 0) + 1
            return self.send(200, {"user": {"id": "u1", "name": "Asha Rao", "permissions": ["hr.access"]},
                                   "token": "T1", "refreshToken": "R1"})
        self.send(401, {"statusCode": 401, "message": "Invalid email or password"})

    def do_GET(self):
        path = self.path.split("?")[0].removeprefix("/api")
        if self.headers.get("Authorization") != "Bearer T1":
            return self.send(401, {"statusCode": 401, "message": "Unauthorized"})
        if SCENARIO["expire_once"]:
            SCENARIO["expire_once"] = False
            return self.send(401, {"statusCode": 401, "message": "jwt expired"})
        if path in SCENARIO["deny"]:
            return self.send(403, {"statusCode": 403, "message": f"Access denied: Required permissions: x for {path}"})
        key = path if path in ROUTES else re.sub(r"/[^/]+$", "/*", path)
        if key in ROUTES:
            return self.send(200, ROUTES[key])
        self.send(404, {"statusCode": 404, "message": "Not found"})


server = ThreadingHTTPServer(("127.0.0.1", 0), Fake)
threading.Thread(target=server.serve_forever, daemon=True).start()
BASE = f"http://127.0.0.1:{server.server_address[1]}"


def api():
    return nexus_client.NexusApi(base_url=BASE, email="asha@test.local", password="pw-secret-123", timeout=10)


# ---- the client ----

try:
    nexus_client.NexusApi(base_url=BASE, email="asha@test.local", password="wrong").login()
    check("a wrong password is an error", False)
except nexus_client.NexusError as e:
    check("a wrong password is an error that says why", "Invalid email or password" in str(e))
SCENARIO["expire_once"] = True
check("an expired token logs in again and retries", api().get("/hr/employees/me")["employeeId"] == "E001")
SCENARIO["deny"] = {"/hr/employees"}
try:
    api().get("/hr/employees")
    check("a 403 is reported as 'can't read'", False)
except nexus_client.NexusDenied as e:
    check("a 403 is reported as 'can't read'", "can't read" in str(e))

# ---- a full sync ----

SCENARIO["deny"] = set()
store.write_csv("employees.csv", [{"id": "OLD1"}])
result = nexus_sync.sync(api())
check("sync succeeds", result["ok"] and result["tier"] == "all employees")
check("the old files were backed up once", (Path(DATA) / nexus_sync.BACKUP / "employees.csv").read_text().startswith("id"))
employees = {e["id"]: e for e in store.read_csv("employees.csv")}
check("terminated people are left out", set(employees) == {"E001", "E002", "E003"})
check("name, role, team and join date are mapped", (employees["E002"]["name"], employees["E002"]["role"], employees["E002"]["team"],
                                                     employees["E002"]["join_date"]) == ("Ravi Kumar", "Developer", "Web", "2025-01-10"))
check("manager is the business id", employees["E002"]["manager_id"] == "E001" and employees["E001"]["manager_id"] == "")
check("skills and leave balances are mapped", employees["E002"]["skills"] == "Angular" and
      (employees["E002"]["casual_balance"], employees["E002"]["sick_balance"], employees["E002"]["earned_balance"]) == ("7", "7", "12"))
check("sensitive fields are never written", "SECRET" not in (Path(DATA) / "employees.csv").read_text())
att = [(a["date"], a["status"]) for a in store.read_csv("attendance.csv") if a["employee_id"] == "E002"]
check("attendance collapses sessions and skips weekends", att == [("2026-10-19", "present"), ("2026-10-20", "present"),
                                                                  ("2026-10-21", "present"), ("2026-10-22", "present"),
                                                                  ("2026-10-23", "leave")])
leaves = store.read_csv("leaves.csv")
check("leaves use business ids and lower-case status", leaves[0]["employee_id"] == "E002" and leaves[0]["status"] == "approved"
      and leaves[0]["start"] == "2026-10-23" and leaves[0]["type"] == "casual")
goals = {g["title"]: g for g in store.read_csv("goals.csv")}
check("goal progress comes from achieved over target", goals["Ship portal"]["progress_pct"] == "40" and goals["Docs"]["status"] == "done"
      and goals["Ship portal"]["target_date"] == "2026-06-30")
reviews = store.read_csv("reviews.csv")
check("only finalized reviews are kept", len(reviews) == 1 and reviews[0]["rating"] == "4" and reviews[0]["reviewer_id"] == "E001")

work = {(r["employee_id"], r["week"]): r for r in store.read_csv("nexus_work.csv")}
this_week, last_week = work[("E002", "2026-10-19")], work[("E002", "2026-10-12")]
check("tasks done, late tasks and on-time % are counted", (this_week["tasks_done"], this_week["bugs_fixed"], this_week["on_time_pct"]) == ("2", "0", "50"))
check("days worked come from attendance (leave day not counted)", this_week["days_worked"] == "4" and last_week["days_worked"] == "0")
check("bugs fixed are counted in their own week", last_week["bugs_fixed"] == "1" and last_week["on_time_pct"] == "")
check("epics are ignored", this_week["tasks_done"] == "2")
check("reopens come from status history", work[("E003", "2026-10-19")]["bugs_reopened"] == "1")
check("no attendance means 5 days", work[("E003", "2026-10-19")]["days_worked"] == "5")
check("every contributor gets 12 weekly rows", len([k for k in work if k[0] == "E002"]) == 12 and ("E001", "2026-10-19") not in work)
check("the project row is mapped", store.read_json("sows.json") == [
    {"code": "PRJ-1", "title": "Portal", "team": "Web", "members": ["E002", "E003"], "due": "2026-10-10",
     "progress_pct": 60, "status": "overdue"}])
marker = (Path(DATA) / "nexus_sync.json").read_text()
check("the sync marker holds no token or password", '"T1"' not in marker and '"R1"' not in marker and "pw-secret" not in marker)
check("the tools now report a live source", nexus_client.source()["live"] and nexus_client.work_rows("E002")[0]["employee_id"] == "E002")
check("the tools read the projects back", nexus_client.projects()[0]["code"] == "PRJ-1")
nexus_client.forget_sync()
check("forgetting the sync goes back to dummy", not nexus_client.source()["live"])

# ---- a user who can't read everything ----

SCENARIO["deny"] = {"/hr/employees", "/hr/attendance", "/hr/leaves", "/hr/performance/reviews/c1"}
result = nexus_sync.sync(api())
check("limited sync still succeeds", result["ok"] and result["tier"] == "your own record and your reportees")
employees = {e["id"]: e for e in store.read_csv("employees.csv")}
check("it falls back to you, your reportees and project members", {"E001", "E002"} <= set(employees) and any(i.startswith("N-") for i in employees))
check("it says what it could not read", any("employees" in w and "can't read" in w for w in result["warnings"])
      and any("attendance" in w for w in result["warnings"]) and any("reviews of" in w for w in result["warnings"]))
check("unreadable parts become empty files, not stale ones", store.read_csv("attendance.csv") == [] and store.read_csv("reviews.csv") == [])
check("team leave is used when view-all is refused", store.read_csv("leaves.csv")[0]["employee_id"] == "E002")
check("work still comes through without attendance", any(r["tasks_done"] == "2" for r in store.read_csv("nexus_work.csv")))

# ---- nothing readable ----

SCENARIO["deny"] = {"/projects", "/hr/employees", "/hr/employees/me", "/hr/employees/me/hierarchy-reportees"}
before = (Path(DATA) / "employees.csv").read_text()
result = nexus_sync.sync(api())
check("with nothing readable no file is touched", not result["ok"] and (Path(DATA) / "employees.csv").read_text() == before)

server.shutdown()
print(f"\n{'ALL PASSED' if not failures else str(len(failures)) + ' FAILED'}")
sys.exit(1 if failures else 0)
