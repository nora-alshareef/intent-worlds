#!/usr/bin/env python3
"""Tests for agent.py. No API and no store: a scripted stand-in plays the model.
Run:  python3 test_agent.py"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent

GOOD = {"schema_version": "1", "verdict": "cannot_decide",
        "findings": [{"id": "F1", "kind": "file", "service": "nginx-thrift", "file": "source/a.conf", "line": 2, "symbol": None,
                      "object_type": None, "object_name": None, "action": None, "what": "A limit.",
                      "evidence": [{"source": "store", "ref": "{\"store\":\"home-timeline-redis\",\"op\":\"dbsize\"}", "quote": "(integer) 3"}]}],
        "constraint": "F1", "decision_used": None, "decision_quote": None,
        "if_intended": {"root_cause": "F1", "fix_class": "finish", "fix_what": "x"},
        "if_mistake": {"root_cause": "F1", "fix_class": "revert", "fix_what": "y"},
        "open_question": "Was the limit decided?"}


def folder():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "source", "sub"))
    open(os.path.join(d, "TICKET.md"), "w").write("# Incident ticket\n\nIt is broken.\n")
    open(os.path.join(d, "source", "a.conf"), "w").write("line one\nlimit 5;\nline three\n")
    open(os.path.join(d, "source", "sub", "b.bin"), "wb").write(b"\xff\xfe\x00limit")
    open(os.path.join(d, "..", "outside-secret.txt"), "w").write("secret")
    os.symlink(os.path.join(d, "..", "outside-secret.txt"), os.path.join(d, "source", "link"))
    return d


def script(*steps):
    """A fake model: each step is a list of (tool, args) it calls, or a text with no call."""
    steps = list(steps)
    seen = []

    def call(model, messages):
        seen.append(json.loads(json.dumps(messages)))
        step = steps.pop(0) if steps else "done"
        if isinstance(step, str):
            return {"choices": [{"message": {"content": step}}], "usage": {"prompt_tokens": 10, "completion_tokens": 2, "cost": 0.001}}
        calls = [{"id": "c%d" % i, "type": "function", "function": {"name": n, "arguments": a if isinstance(a, str) else json.dumps(a)}}
                 for i, (n, a) in enumerate(step)]
        return {"choices": [{"message": {"content": "", "tool_calls": calls}}], "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0.01}}
    call.seen = seen
    return call


def tool_texts(call):
    return [m["content"] for m in call.seen[-1] if m["role"] == "tool"]


def test_a_normal_run_reads_queries_and_saves():
    d = folder()
    asked = []
    store = lambda req: asked.append(req) or {"ok": True, "output": "(integer) 3\n", "truncated": False}
    call = script([("list_dir", {"path": "."}), ("read_file", {"path": "source/a.conf"})],
                  [("search", {"pattern": "limit"})],
                  [("query_store", {"request": {"store": "home-timeline-redis", "op": "dbsize"}})],
                  [("write_answer", {"answer": GOOD})], "extra turn that must not happen")
    r = agent.run(d, "PROMPT", "test/model", call=call, store=store)
    assert r["stopped"] == "finished" and r["answer_written"] and r["turns"] == 4, r
    assert r["tool_calls"] == {"list_dir": 1, "read_file": 1, "search": 1, "query_store": 1, "write_answer": 1}, r
    assert r["input_tokens"] == 400 and abs(r["cost_usd"] - 0.04) < 1e-9, r
    assert json.load(open(os.path.join(d, "answer.json"))) == GOOD
    assert asked == [{"store": "home-timeline-redis", "op": "dbsize"}]
    msgs = call.seen[-1]
    assert msgs[0] == {"role": "system", "content": "PROMPT"}
    texts = [m["content"] for m in msgs if m["role"] == "tool"]
    assert "TICKET.md" in texts[0] and "source/" in texts[0]
    assert "     2\tlimit 5;" in texts[1]
    assert "source/a.conf:2: limit 5;" in texts[2] and "b.bin" not in texts[2]          # binary files are skipped


def test_files_outside_the_folder_cannot_be_read():
    d = folder()
    call = script([("read_file", {"path": "../outside-secret.txt"}), ("read_file", {"path": "/etc/passwd"}),
                   ("read_file", {"path": "source/link"}), ("list_dir", {"path": ".."}), ("search", {"pattern": "secret", "path": ".."}),
                   ("search", {"pattern": "secret"}), ("list_dir", {"path": "source"})], "stop", "stop")
    agent.run(d, "P", "m", call=call, store=lambda r: {})
    t = tool_texts(call)
    assert all("secret" not in x or "outside" in x for x in t[:5]) and all(x.startswith("Error:") for x in t[:5]), t[:5]
    assert t[5] == "(no match)", t[5]                                                    # the link is not followed
    assert "link" not in t[6], t[6]                                                      # and not listed


def test_bad_calls_get_an_error_text_and_the_run_goes_on():
    d = folder()
    call = script([("no_such_tool", {}), ("read_file", "not json {"), ("read_file", {"wrong": 1}), ("search", {"pattern": "("}),
                   ("read_file", {"path": "source/a.conf", "start_line": 99}), ("write_answer", {"answer": {"verdict": "bug"}})],
                  [("write_answer", {"answer": GOOD})])
    r = agent.run(d, "P", "m", call=call, store=lambda r: {})
    assert r["stopped"] == "finished" and r["turns"] == 2, r
    t = [m["content"] for m in call.seen[-1] if m["role"] == "tool"]
    assert t[0].startswith("No such tool") and "not valid JSON" in t[1] and t[2].startswith("Error") and "regular expression" in t[3]
    assert "the file has 3 lines" in t[4] and t[5].startswith("NOT SAVED")
    assert not os.path.exists(os.path.join(d, "x"))


def test_the_run_always_ends():
    d = folder()
    r = agent.run(d, "P", "m", max_turns=3, call=script(*[[("list_dir", {"path": "."})]] * 10), store=lambda r: {})
    assert r["stopped"] == "max_turns" and r["turns"] == 3 and not r["answer_written"], r
    call = script("I think it is the limit.", "Still no tool call.")
    r = agent.run(folder(), "P", "m", call=call, store=lambda r: {})
    assert r["stopped"] == "no_answer" and r["turns"] == 2, r
    assert any("write_answer" in m["content"] for m in call.seen[-1] if m["role"] == "user")
    bad = [[("write_answer", {"answer": {"verdict": "bug"}})]] * 6
    r = agent.run(folder(), "P", "m", call=script(*bad), store=lambda r: {})
    assert r["stopped"] == "answer_never_valid" and r["turns"] == 4, r

    def broken(model, messages):
        raise RuntimeError("HTTP 401 from the API: no key")
    r = agent.run(folder(), "P", "m", call=broken, store=lambda r: {})
    assert r["stopped"] == "api_error" and "401" in r["error"] and r["turns"] == 0, r


def test_long_outputs_are_cut():
    d = folder()
    open(os.path.join(d, "big.txt"), "w").write("".join("line %d %s\n" % (i, "x" * 100) for i in range(1000)))
    call = script([("read_file", {"path": "big.txt"}), ("read_file", {"path": "big.txt", "max_lines": 100000}), ("search", {"pattern": "line"})], "s", "s")
    agent.run(d, "P", "m", call=call, store=lambda r: {})
    t = tool_texts(call)
    assert all(len(x) <= agent.MAX_OUT + 60 for x in t), [len(x) for x in t]
    assert "stopped at %d hits" % agent.MAX_HITS in t[2] or "cut at" in t[2]


def test_the_tool_list_is_the_five_tools_and_no_shell():
    assert [t["name"] for t in agent.TOOLS] == ["list_dir", "read_file", "search", "query_store", "write_answer"]
    assert not any(w in json.dumps(agent.TOOLS).lower() for w in ("bash", "shell", "exec", "http_get", "fetch"))


def test_v1_asks_once_before_saving_an_answer_that_removes_the_constraint():
    bad = json.loads(json.dumps(GOOD)); bad["verdict"] = "mistake"; bad["decision_used"] = "D-008"; bad["decision_quote"] = "One row per URL."
    bad["open_question"] = None
    fixed = json.loads(json.dumps(bad)); fixed["verdict"] = "intended"
    assert agent.grade.validate(bad) == [] and agent.grade.validate(fixed) == [], (agent.grade.validate(bad), agent.grade.validate(fixed))
    # v0 saves it at once
    d = folder(); r = agent.run(d, "P", "m", call=script([("write_answer", {"answer": bad})]), store=lambda r: {})
    assert r["turns"] == 1 and r["agent"] == "v0" and json.load(open(os.path.join(d, "answer.json")))["verdict"] == "mistake"
    # v1 holds it once, quotes the decision, then saves what comes next
    d = folder(); call = script([("write_answer", {"answer": bad})], [("write_answer", {"answer": fixed})], "x")
    r = agent.run(d, "P", "m", call=call, store=lambda r: {}, version="v1")
    assert r["turns"] == 2 and r["agent"] == "v1" and r["stopped"] == "finished", r
    assert json.load(open(os.path.join(d, "answer.json")))["verdict"] == "intended"
    held = [m["content"] for m in call.seen[-1] if m["role"] == "tool"][0]
    assert held.startswith("NOT SAVED YET") and "D-008" in held and "One row per URL." in held and "A limit." in held
    # v1 saves the same answer the second time: the check is made once
    d = folder(); r = agent.run(d, "P", "m", call=script([("write_answer", {"answer": bad})], [("write_answer", {"answer": bad})]), store=lambda r: {}, version="v1")
    assert r["turns"] == 2 and json.load(open(os.path.join(d, "answer.json")))["verdict"] == "mistake"
    # v1 does not touch other answers
    d = folder(); r = agent.run(d, "P", "m", call=script([("write_answer", {"answer": GOOD})]), store=lambda r: {}, version="v1")
    assert r["turns"] == 1 and r["answer_written"]


def test_the_agent_is_told_once_when_turns_run_out():
    call = script(*[[("list_dir", {"path": "."})]] * 20)
    r = agent.run(folder(), "P", "m", max_turns=12, call=call, store=lambda r: {})
    assert r["stopped"] == "max_turns" and r["budget_warned"], r
    warns = [m["content"] for m in call.seen[-1] if m["role"] == "user" and "turns left" in m["content"]]
    assert warns == [w for w in warns if w.startswith("You have 8 turns left")] and len(warns) == 1, warns
    r = agent.run(folder(), "P", "m", call=script([("write_answer", {"answer": GOOD})]), store=lambda r: {})
    assert not r["budget_warned"]


def test_the_key_limit_is_read_and_nothing_of_the_key_is_shown():
    os.environ["OPENROUTER_API_KEY"] = "sk-or-test-not-real"
    assert agent.key_remaining(lambda: {"data": {"label": "sk-or-v1-abc...xyz", "limit": 1, "limit_remaining": 0.06, "usage": 0.94}}) == (0.06, 1.0)
    assert agent.key_remaining(lambda: {"data": {"limit": None, "limit_remaining": None}}) == (None, None)
    for bad in (lambda: {"error": {"code": 401}}, lambda: [], lambda: {"data": {"limit_remaining": "sk-or-v1-secret"}}):
        try:
            agent.key_remaining(bad)
            assert False, "no error"
        except RuntimeError as err:
            assert "sk-or" not in str(err), err


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS  " + name)
        except AssertionError as err:
            failed += 1
            print("FAIL  %s: %s" % (name, str(err)[:600]))
    print("%d passed, %d failed" % (len(tests) - failed, failed))
    sys.exit(1 if failed else 0)
