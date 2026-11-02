"""Stand-in agent process used only by tests to exercise the CLI runner (streaming, cancel, timeout).

Usage: python fake_cli.py <mode>   mode = ok | slow | fail
"""

import json
import sys
import time

mode = sys.argv[1]
prompt = sys.stdin.read()
print("not-json banner line", flush=True)
print(json.dumps({"type": "tool", "name": "Read", "id": "c1", "input": {"path": "a.txt"}}), flush=True)
if mode == "slow":
    time.sleep(60)
if mode == "fail":
    print("boom", file=sys.stderr, flush=True)
    sys.exit(3)
print(json.dumps({"type": "result", "id": "c1", "text": f"echo: {prompt.strip()} api_key=secretvalue123"}), flush=True)
