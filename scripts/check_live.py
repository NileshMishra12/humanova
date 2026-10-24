"""Live smoke test: run once you have an API key.  python scripts/check_live.py
Asks real Claude a few questions as different roles and checks it used our tools instead of guessing."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agent  # noqa: E402
import config  # noqa: E402

CASES = [  # (role, question, a tool Claude should call)
    ("Manager - Anita Desai (Web)", "Who is on leave this week?", "who_is_on_leave"),
    ("Manager - Anita Desai (Web)", "Give me a 1:1 summary of Rahul", "summarise_employee"),
    ("HR Admin", "Who performed best this cycle, and what increments do you recommend?", "increment_recommendations"),
    ("HR Admin", "Who has capacity to take on a new project?", "team_capacity"),
    ("Recruiter - Talent Team", "Shortlist the candidates for OP1", "screen_candidates"),
]

if config.MOCK:
    sys.exit("No API key found. Put ANTHROPIC_API_KEY=... in .env (and unset HR_MOCK), then run this again.")

failed = 0
for label, question, expected in CASES:
    answer, trace = agent.run_agent([{"role": "user", "content": question}], agent.ROLES[label])
    used = [t["tool"] for t in trace]
    ok = expected in used or expected not in agent.FUNCS  # a tool that isn't built yet can't be expected
    failed += not ok
    usage = agent.LAST_USAGE
    print(f"{'PASS' if ok else 'FAIL'}  [{label}] {question}")
    print(f"      tools: {used} · tokens: {usage['input_tokens']} in / {usage['output_tokens']} out · mode: {usage['mode']}")
    print(f"      {answer[:220].replace(chr(10), ' ')}")
print(f"\n{'All cases used the right tools.' if not failed else f'{failed} case(s) did not call the expected tool.'}")
sys.exit(1 if failed else 0)
