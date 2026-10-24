"""Agent tests with a fake Claude: no API key, internet or teammate files needed.
Run: python tests/test_agent.py
"""
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

os.environ["HR_DATA_DIR"] = tempfile.mkdtemp(prefix="humanova_agent_")
os.environ["ANTHROPIC_API_KEY"] = "test-key"
os.environ.pop("HR_MOCK", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import llm  # noqa: E402
import store  # noqa: E402

failures = []


def check(name, ok):
    print(("PASS  " if ok else "FAIL  ") + name)
    if not ok:
        failures.append(name)


# ---- a fake Claude and fake tools ----

def text(t):
    return SimpleNamespace(type="text", text=t)


def tool_use(i, name, args):
    return SimpleNamespace(type="tool_use", id=f"toolu_{i}", name=name, input=args)


def reply(*blocks, stop="end_turn", tokens_in=100, tokens_out=20):
    return SimpleNamespace(stop_reason=stop, content=list(blocks),
                           usage=SimpleNamespace(input_tokens=tokens_in, output_tokens=tokens_out))


class FakeClaude:
    def __init__(self, script):
        self.script, self.calls = list(script), []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        return item


def use(script):
    fake = FakeClaude(script)
    llm._client = fake
    return fake


seen = {}


def fake_list_employees(team=None):
    seen["list_team"] = team
    people = [{"id": "E1", "name": "Priya Shah", "role": "Dev", "team": team or "Web",
               "manager_id": "E0", "status_today": "present"}]
    if team is None:
        people += [{"id": "E2", "name": "Sam", "role": "Dev", "team": "CS Team", "manager_id": "E0", "status_today": "present"},
                   {"id": "E3", "name": "Lee", "role": "Dev", "team": "Backend Team", "manager_id": "E0", "status_today": "present"}]
    return {"employees": people}


def fake_team_overview(team=None):
    seen["overview_team"] = team
    return {"headcount": 1, "rows": []}


def fake_get_employee(name_or_id):
    team = "Mobile" if name_or_id in ("E7", "Arjun") else "Web"
    return {"employee": {"id": name_or_id, "name": "Arjun Patel" if team == "Mobile" else "Priya Shah",
                         "team": team}, "goals": [], "latest_review": None}


def fake_get_attendance(name_or_id, days=30):
    seen["attendance_called"] = name_or_id
    return {"employee": name_or_id, "attendance_pct": 99}


def fake_huge():
    return {"rows": ["x" * 100] * 500}


def fake_boom():
    raise RuntimeError("disk on fire")


original_funcs, original_tools = dict(agent.FUNCS), list(agent.TOOLS)
agent.FUNCS.update(team_overview=fake_team_overview, list_employees=fake_list_employees, get_employee=fake_get_employee,
                   get_attendance=fake_get_attendance, huge=fake_huge, boom=fake_boom)
agent.TOOLS[:] = [d for d in agent._DEFS if d["name"] in agent.FUNCS]
anita = agent.ROLES["Manager - Anita Desai (Web)"]
recruiter = agent.ROLES["Recruiter - Talent Team"]
admin = agent.ROLES["HR Admin"]
ask = lambda q: [{"role": "user", "content": q}]  # noqa: E731

# ---- the tool-use loop ----

fake = use([reply(tool_use(1, "list_employees", {}), stop="tool_use", tokens_in=120, tokens_out=30),
            reply(text("Priya Shah is in Web."), tokens_in=200, tokens_out=40)])
answer, trace = agent.run_agent(ask("Who is in my team?"), anita)
check("loop returns Claude's final answer", answer == "Priya Shah is in Web.")
check("trace lists the tool Claude called", [t["tool"] for t in trace] == ["list_employees"])
check("token usage adds up over rounds", (agent.LAST_USAGE["input_tokens"], agent.LAST_USAGE["output_tokens"]) == (320, 70))
check("usage says live mode", agent.LAST_USAGE["mode"] == "live")
results = fake.calls[1]["messages"][-1]["content"]
check("tool result goes back to Claude", results[0]["type"] == "tool_result" and "Priya Shah" in results[0]["content"])

# ---- roles and scope are enforced in code ----

check("manager's team argument is forced to Web", seen["list_team"] == "Web")
use([reply(tool_use(1, "list_employees", {"team": "Mobile"}), stop="tool_use"), reply(text("ok"))])
agent.run_agent(ask("Show me Mobile"), anita)
check("a manager asking for Mobile still gets Web", seen["list_team"] == "Web")

seen.pop("attendance_called", None)
fake = use([reply(tool_use(1, "get_attendance", {"name_or_id": "E7"}), stop="tool_use"), reply(text("ok"))])
agent.run_agent(ask("Attendance of Arjun"), anita)
blocked = fake.calls[1]["messages"][-1]["content"][0]
check("another team's person is refused", "not in your team" in blocked["content"] and blocked.get("is_error"))
check("the tool was never run for them", "attendance_called" not in seen)

fake = use([reply(text("hello"))])
agent.run_agent(ask("hi"), recruiter)
names = {t["name"] for t in fake.calls[0]["tools"]}
check("recruiter is only offered hiring tools", "list_openings" in names and "list_employees" not in names)
fake = use([reply(tool_use(1, "get_employee", {"name_or_id": "E1"}), stop="tool_use"), reply(text("ok"))])
agent.run_agent(ask("Tell me about E1"), recruiter)
check("recruiter calling an employee tool is refused", "can't use" in fake.calls[1]["messages"][-1]["content"][0]["content"])

use([reply(tool_use(1, "team_overview", {"team": "CS"}), stop="tool_use"), reply(text("ok"))])
agent.run_agent(ask("Overview of CS"), admin)
check("a loose team name ('CS') is matched to the real one ('CS Team')", seen["overview_team"] == "CS Team")
use([reply(tool_use(1, "team_overview", {"team": "backend"}), stop="tool_use"), reply(text("ok"))])
agent.run_agent(ask("backend overview"), admin)
check("matching ignores case ('backend' is 'Backend Team')", seen["overview_team"] == "Backend Team")
use([reply(tool_use(1, "team_overview", {"team": "Mars"}), stop="tool_use"), reply(text("ok"))])
agent.run_agent(ask("Mars"), admin)
check("an unknown team name is left alone", seen["overview_team"] == "Mars")

# ---- limits and failures ----

fake = use([reply(tool_use(1, "list_employees", {}), stop="tool_use")])
answer, trace = agent.run_agent(ask("loop forever"), admin)
check("tool loop stops after 6 rounds", len(fake.calls) == agent.MAX_ROUNDS)
check("last round forbids more tools", fake.calls[-1].get("tool_choice") == {"type": "none"})
check("endless loop falls back and says so", answer.startswith("Claude was unavailable"))

use([RuntimeError("network down")])
answer, trace = agent.run_agent(ask("hello"), admin)
check("API error falls back to offline and says so", answer.startswith("Claude was unavailable") and agent.LAST_USAGE["mode"] == "offline")

fake = use([reply(text("ok"))])
agent.run_agent([{"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"} for i in range(15)] + [{"role": "user", "content": "last"}], admin)
sent = fake.calls[0]["messages"]
check("only the last 10 messages are sent", len(sent) <= agent.MAX_HISTORY and sent[0]["role"] == "user")

fake = use([reply(tool_use(1, "huge", {}), stop="tool_use"), reply(text("ok"))])
agent.ROLES["HR Admin"]["tools"].add("huge")
agent.TOOLS.append({"name": "huge", "description": "x", "input_schema": {"type": "object", "properties": {}}})
agent.run_agent(ask("big"), admin)
sent_text = fake.calls[1]["messages"][-1]["content"][0]["content"]
check("a huge tool result is cut for Claude", len(sent_text) < agent.MAX_TOOL_CHARS + 400 and "[cut:" in sent_text)

# ---- call_tool never pretends ----

check("a crashing tool becomes an error dict", "disk on fire" in agent.call_tool("boom", {}, "t")["error"])
check("an unknown tool becomes an error dict", "error" in agent.call_tool("nope", {}, "t"))
check("bad arguments become an error dict", "error" in agent.call_tool("list_employees", {"wrong": 1}, "t"))
check("every call is in the audit log", any(e["tool"] == "boom" and e["status"] == "error" for e in store.read_json("audit_log.json")))
check("every chat is in the audit log", any(e["tool"] == "chat" for e in store.read_json("audit_log.json")))

# ---- prompt rules and learned corrections ----

prompt = agent.system_prompt(anita)
for phrase in ("Never invent", "recommendations", "drafts only", "supportive", "salary amounts", "Nexus",
               "never create approvals on your own"):
    check(f"system prompt says: {phrase}", phrase.lower() in prompt.lower())
check("system prompt names the signed-in user", "Anita Desai" in prompt)
store.add_feedback("who is out", "x", "down", "Always include the leave type", "tester")
check("a thumbs-down comment becomes a rule in the prompt", "Always include the leave type" in agent.system_prompt(anita))

# ---- llm.py ----

use([reply(text('Sure! {"a": {"b": "}"}} hope that helps'))])
check("complete_json pulls the first JSON object out", llm.complete_json("x") == {"a": {"b": "}"}})
fake = use([reply(text("no json here")), reply(text('{"ok": true}'))])
check("complete_json retries once with a JSON-only nudge", llm.complete_json("x") == {"ok": True} and "JSON only" in fake.calls[1]["messages"][0]["content"])
use([reply(text("still nothing"))])
try:
    llm.complete_json("x")
    check("complete_json raises LLMError when it never gets JSON", False)
except llm.LLMError:
    check("complete_json raises LLMError when it never gets JSON", True)

agent.FUNCS.clear()
agent.FUNCS.update(original_funcs)
agent.TOOLS[:] = original_tools
print(f"\n{'ALL PASSED' if not failures else str(len(failures)) + ' FAILED'}")
sys.exit(1 if failures else 0)
