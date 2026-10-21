"""Time the Copilot CLI under different settings.

The CLI is a coding agent, so how it is invoked matters far more than the
prompt does. This measures the combinations rather than guessing at them.

    python bench_llm.py
"""

from __future__ import annotations

import os
import time

from dotenv import load_dotenv

from chalkboard.agents.llm import CopilotCLIClient

load_dotenv()

PROMPT = (
    "Weber won the ball from Varela in the left opposition half under "
    "pressure 0.81, twice in two minutes. One sentence, under 25 words, "
    "saying why that mattered. Use only those names."
)

COMMAND = os.environ.get("COPILOT_CMD", "copilot.cmd")
STDIN = (os.environ.get("COPILOT_STDIN") or "").lower() in ("1", "true", "yes")

VARIANTS = [
    ("baseline, in the repo",      {"isolated": False, "args_template": "-p|{prompt}|--allow-all-tools"}),
    ("isolated working directory", {"isolated": True,  "args_template": "-p|{prompt}|--allow-all-tools"}),
    ("isolated, no tools",         {"isolated": True,  "args_template": "-p|{prompt}"}),
]


def main() -> int:
    print(f"command={COMMAND}  stdin={STDIN}\n")
    for label, kwargs in VARIANTS:
        client = CopilotCLIClient(command=COMMAND, timeout_s=90, use_stdin=STDIN, **kwargs)
        started = time.monotonic()
        answer = client.complete(PROMPT)
        elapsed = time.monotonic() - started
        status = "ok " if answer else "FAIL"
        print(f"  {status} {elapsed:6.1f}s  {label}")
        if answer:
            print(f"         {answer[:90]}")
    print("\nPut the fastest working setting in .env as COPILOT_ARGS,")
    print("and COPILOT_ISOLATED=false only if isolation made it worse.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
