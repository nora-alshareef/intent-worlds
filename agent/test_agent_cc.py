#!/usr/bin/env python3
"""Tests for agent_cc.py with a stand-in for the `claude` program. No model, no store.
Run:  python3 test_agent_cc.py"""
import json
import os
import stat
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent_cc
from test_agent import GOOD

FAKE = r'''#!/usr/bin/env python3
# a stand-in for `claude -p`: prints stream-json events and writes answer.json as the mode says
import json, os, sys
a = sys.argv[1:]
mode = os.environ["FAKE_MODE"]
resumed = "--resume" in a
assert "ANTHROPIC_API_KEY" not in os.environ and a[0] == "-p" and "stream-json" in a and "Bash(python3 store.py:*)" in a and a[a.index("--tools") + 1] == "Read,Grep,Glob,Write,Bash"
good, bad = json.load(open(os.environ["FAKE_GOOD"])), json.load(open(os.environ["FAKE_BAD"]))
def use(name, **inp):
    print(json.dumps({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}}))
print(json.dumps({"type": "system", "subtype": "init", "session_id": "s1"}))
use("Read", file_path="TICKET.md")
use("Bash", command="python3 store.py '{\"store\":\"home-timeline-redis\",\"op\":\"dbsize\"}'")
if mode == "outside":
    use("Read", file_path="../secret.json"); use("Glob", pattern="/Users/**/_answers/*"); use("Bash", command="cat /etc/passwd")
answer = good
if mode == "mistake" or (mode == "mistake_then_fixed" and not resumed):
    answer = bad
if mode == "invalid_then_good" and not resumed:
    answer = {"verdict": "bug"}
if mode != "never":
    json.dump(answer, open("answer.json", "w"))
print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "session_id": "s1", "num_turns": 5, "total_cost_usd": 0.02,
                  "usage": {"input_tokens": 10, "cache_read_input_tokens": 90, "output_tokens": 7}, "permission_denials": []}))
'''


def setup():
    d = tempfile.mkdtemp()
    folder = os.path.join(d, "incident-x")
    os.makedirs(folder)
    open(os.path.join(folder, "TICKET.md"), "w").write("broken\n")
    fake = os.path.join(d, "fake_claude")
    open(fake, "w").write(FAKE)
    os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
    bad = json.loads(json.dumps(GOOD)); bad["verdict"] = "mistake"; bad["decision_used"] = "D-008"; bad["decision_quote"] = "One row."; bad["open_question"] = None
    json.dump(GOOD, open(os.path.join(d, "good.json"), "w")); json.dump(bad, open(os.path.join(d, "bad.json"), "w"))
    os.environ.update(FAKE_GOOD=os.path.join(d, "good.json"), FAKE_BAD=os.path.join(d, "bad.json"), ANTHROPIC_API_KEY="must-not-be-passed-on")
    return folder, fake


def go(mode, version="v0"):
    folder, fake = setup()
    os.environ["FAKE_MODE"] = mode
    r = agent_cc.run(folder, "PROMPT", "haiku", 50, log=folder + ".log.jsonl", version=version, binary=fake)
    return folder, r


def test_a_normal_run():
    folder, r = go("good")
    assert r["stopped"] == "finished" and r["answer_written"] and r["turns"] == 5 and r["follow_ups"] == 0, r
    assert r["tool_calls"] == {"Read": 1, "Bash": 1} and r["outside_folder"] == [] and r["other_commands"] == [], r
    assert r["input_tokens"] == 100 and r["notional_cost_usd"] == 0.02 and r["cost_usd"] is None and r["harness"] == "cc", r
    assert os.path.isfile(os.path.join(folder, "store.py")) and "python3 store.py" in open(os.path.join(folder, "STORE_TOOL.md")).read()
    assert HERE not in open(os.path.join(folder, "store.py")).read()                      # the project path is not shown to the agent
    assert os.path.getsize(folder + ".log.jsonl") > 100


def test_steps_outside_the_folder_are_reported():
    _, r = go("outside")
    assert r["outside_folder"] == ["Read ../secret.json", "Glob /Users/**/_answers/*"] and r["other_commands"] == ["cat /etc/passwd"], r


def test_v1_asks_once_and_v0_does_not():
    _, r = go("mistake")
    assert r["stopped"] == "finished" and not r["challenged"] and r["follow_ups"] == 0, r
    folder, r = go("mistake_then_fixed", "v1")
    assert r["stopped"] == "finished" and r["challenged"] and r["follow_ups"] == 1 and r["turns"] == 10, r
    assert json.load(open(os.path.join(folder, "answer.json")))["verdict"] == "cannot_decide"
    sent = [json.loads(l)["sent"] for l in open(folder + ".log.jsonl") if '"sent"' in l[:30]]
    assert len(sent) == 2 and "D-008" in sent[1] and "write_answer" not in sent[1], sent[1]
    folder, r = go("mistake", "v1")                                                        # it keeps its answer: asked once, then final
    assert r["stopped"] == "finished" and r["follow_ups"] == 1 and json.load(open(os.path.join(folder, "answer.json")))["verdict"] == "mistake", r


def test_a_bad_or_missing_answer_is_asked_for_again_and_the_run_ends():
    _, r = go("invalid_then_good")
    assert r["stopped"] == "finished" and r["follow_ups"] == 1, r
    _, r = go("never")
    assert r["stopped"] == "max_turns" and not r["answer_written"] and r["follow_ups"] == agent_cc.MAX_FOLLOW_UPS, r
    folder, _ = setup()
    r = agent_cc.run(folder, "P", binary="/no/such/program")
    assert r["stopped"] == "api_error" and "not found" in r["error"], r


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS  " + name)
        except AssertionError as err:
            failed += 1
            print("FAIL  %s: %s" % (name, str(err)[:700]))
    print("%d passed, %d failed" % (len(tests) - failed, failed))
    sys.exit(1 if failed else 0)
