"""Nexus connector (owner: Lead). Everything HumaNova knows about Nexus goes through this module.

Two halves:
- Reading what the tools use: work_rows(), projects(), source(). They read the data files, which hold either a
  dummy export or the result of the last sync, so the app keeps working if Nexus or the wifi is down.
- NexusApi: a small read-only HTTP client (GET only, plus the login) used by nexus_sync.py to refresh those files.

HumaNova never writes to Nexus.
"""
import json
import urllib.error
import urllib.parse
import urllib.request

import config
import store

SYNC_FILE = "nexus_sync.json"


class NexusError(Exception):
    """Nexus could not be reached or answered with an error."""


class NexusDenied(NexusError):
    """The Nexus user is not allowed to read this (HTTP 403)."""


# ---------------- what the tools read ----------------

def _sync_info():
    info = store.read_json(SYNC_FILE)
    return info if isinstance(info, dict) else {}


def source():
    """Where the numbers come from, for labels in the UI and reports."""
    info = _sync_info()
    if info.get("time"):
        return {"name": "Nexus", "mode": "live sync", "detail": f"last synced {info['time']} from {info['base_url']}",
                "live": True}
    return {"name": "Nexus", "mode": "dummy export", "detail": "data/nexus_work.csv and data/sows.json",
            "live": False}


def forget_sync():
    """Call after the data files were replaced by something else (for example the dummy demo data)."""
    store.write_json(SYNC_FILE, {})


def work_rows(employee_id=None):
    """Weekly work rows: employee_id, week, days_worked, tasks_done, bugs_fixed, bugs_reopened,
    on_time_pct, sow. All employees, or just one."""
    return [r for r in store.read_csv("nexus_work.csv") if employee_id is None or r["employee_id"] == employee_id]


def projects():
    """Projects (SOWs): code, title, team, members (employee ids), due, progress_pct, status."""
    return store.read_json("sows.json")


# ---------------- read-only HTTP client ----------------

class NexusApi:
    """Logs in once, reuses the token, logs in again if it expires. GET only."""

    def __init__(self, base_url=None, email=None, password=None, timeout=60):
        self.base_url = (base_url or config.NEXUS_URL).rstrip("/")
        self._email = email or config.NEXUS_EMAIL
        self._password = password or config.NEXUS_PASSWORD
        self.timeout, self.token, self.user = timeout, None, {}

    @property
    def permissions(self):
        return set(self.user.get("permissions", []))

    def _request(self, method, path, params=None, body=None, auth=True):
        url = f"{self.base_url}/api{path}"
        if params:
            url += "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Accept": "application/json", **({"Content-Type": "application/json"} if data else {})}
        if auth and self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data, headers, method=method),
                                        timeout=self.timeout) as res:
                raw = res.read().decode("utf-8")
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", errors="ignore")[:200]
            if e.code == 403:
                raise NexusDenied(f"Nexus says this user can't read {path}: {detail}") from e
            raise NexusError(f"Nexus answered {e.code} for {path}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise NexusError(f"Could not reach Nexus at {self.base_url}: {e}") from e
        return json.loads(raw) if raw else None

    def login(self):
        if not (self._email and self._password):
            raise NexusError("No Nexus credentials: set NEXUS_EMAIL and NEXUS_PASSWORD in .env")
        try:
            res = self._request("POST", "/auth/login", body={"email": self._email, "password": self._password},
                                auth=False)
        except NexusDenied as e:  # a refused login is not a permissions problem
            raise NexusError(str(e)) from e
        self.token, self.user = res["token"], res.get("user", {})
        return self.user

    def get(self, path, **params):
        if not self.token:
            self.login()
        try:
            return self._request("GET", path, params)
        except NexusError as e:
            if "answered 401" not in str(e):
                raise
        self.login()  # token expired (18-hour limit): log in once more
        return self._request("GET", path, params)
