#!/usr/bin/env python3
"""The second harness: Claude Code in headless mode (`claude -p`), on the Claude subscription.

  agent_cc.py <incident-folder> [--model haiku] [--max-turns N] [--agent v0|v1]

The loop is Claude Code's, not ours (compare agent.py). What stays ours:
  - the prompt (PROMPT_CC.md)
  - the limits: file tools, plus ONE command, `python3 store.py '<json>'`, which is dbtool.py with its limits
  - the answer check (grade.validate) and, for agent v1, the one question before an answer that removes the constraint
  - an audit of every tool call: a path outside the incident folder, or any other command, is reported

The audit DETECTS a step outside the folder; it does not prevent one. Run the folder outside the project
(run_live.py does) so that nothing about the answers is near it.
NOT TESTED against the real `claude` program: written from its documentation. test_agent_cc.py uses a stand-in.
"""
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent
import grade

DEFAULT_MODEL = "haiku"
MAX_TURNS = 50
MAX_FOLLOW_UPS = 4
TIMEOUT_S = 1800
TOOLS = "Read,Grep,Glob,Write,Bash"          # the only built-in tools the model is shown (no skills, tasks, schedules, messages)
ALLOWED = ["Read", "Grep", "Glob", "Write", "Bash(python3 store.py:*)"]
DENIED = ["WebFetch", "WebSearch", "Task", "Edit", "NotebookEdit"]
PATH_TOOLS = ("Read", "Write", "Edit", "Grep", "Glob", "NotebookEdit")
STORE_PY = '''import os, subprocess, sys
sys.exit(subprocess.call([sys.executable, os.path.join(os.environ["INTENT_AGENT_DIR"], "dbtool.py")] + sys.argv[1:]))
'''


def prepare(folder):
    """Put the store command and its description into the incident folder."""
    with open(os.path.join(folder, "store.py"), "w", encoding="utf-8") as f:
        f.write(STORE_PY)
    text = next(t["description"] for t in agent.TOOLS if t["name"] == "query_store")
    intro, _, examples = text.partition("Request examples: ")
    examples, _, tail = examples.partition(". Store names")
    with open(os.path.join(folder, "STORE_TOOL.md"), "w", encoding="utf-8") as f:
        f.write("# The store command\n\nRun:  python3 store.py '<request as JSON>'\n\n" + intro.strip() + "\n\nRequest examples:\n\n"
                + "\n".join("    python3 store.py '%s'" % e.strip() for e in examples.split("; ")) + "\n\nStore names" + tail + "\n")


def audit(events, folder):
    """Every tool call of the run. Returns (counts, paths outside the folder, commands that are not the store command)."""
    root = os.path.realpath(folder)
    counts, outside, odd, inside_reads = {}, [], [], []
    for ev in events:
        if ev.get("type") != "assistant":
            continue
        for block in (ev.get("message") or {}).get("content") or []:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name, arg = block.get("name") or "", block.get("input") or {}
            counts[name] = counts.get(name, 0) + 1
            if name in PATH_TOOLS:
                for key in ("file_path", "path", "notebook_path"):
                    p = arg.get(key)
                    if isinstance(p, str) and p:
                        real = os.path.realpath(p if os.path.isabs(p) else os.path.join(root, p))
                        if real != root and not real.startswith(root + os.sep):
                            outside.append("%s %s" % (name, p))
                pat = arg.get("pattern") if name == "Glob" else None
                if isinstance(pat, str) and (pat.startswith(("/", "~")) or ".." in pat):
                    outside.append("Glob %s" % pat)
            elif name == "Bash":
                cmd = str(arg.get("command") or "")
                if not re.match(r"^python3 store\.py '[^']*'\s*$", cmd):
                    bare = re.sub(r"\"[^\"]*\"|'[^']*'", " ", cmd)          # name patterns in quotes are not paths
                    paths = re.findall(r"(?<![\w.-])(/[^\s|;&>]*|~[^\s|;&>]*|\.\.[^\s|;&>]*)", bare)
                    inside = all(os.path.realpath(os.path.expanduser(p)).startswith(root + os.sep) or os.path.realpath(p) == root
                                 or p in ("/dev/null",) for p in paths)
                    reads_only = re.match(r"^(find|ls|cat|head|tail|wc|grep|rg|sort|uniq|cut|file|stat|tree|pwd|echo)\b", cmd) and \
                        all(re.match(r"^\s*(find|ls|cat|head|tail|wc|grep|rg|sort|uniq|cut|file|stat|tree|pwd|echo|xargs)\b", part)
                            for part in re.split(r"\|\||&&|[|;]", bare)) and ">" not in bare.replace("2>/dev/null", "") and "$(" not in cmd and "`" not in cmd
                    (odd if not (inside and reads_only) else inside_reads).append(cmd[:200])
            elif name not in ("TodoWrite",):
                odd.append("tool %s" % name)
    return counts, outside, odd, len(inside_reads)


def call_claude(folder, text, model, max_turns, session=None, binary="claude"):
    """One `claude -p` run in the folder. Returns (events, result event or None, error text or None)."""
    cmd = [binary, "-p", text, "--model", model, "--output-format", "stream-json", "--verbose", "--max-turns", str(max_turns)]
    if session:
        cmd += ["--resume", session]
    cmd += ["--tools", TOOLS, "--allowedTools"] + ALLOWED + ["--disallowedTools"] + DENIED
    env = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL")}
    env["INTENT_AGENT_DIR"] = HERE                      # the subscription is used, never an API key from the environment
    try:
        r = subprocess.run(cmd, cwd=folder, env=env, capture_output=True, text=True, timeout=TIMEOUT_S)
    except FileNotFoundError:
        return [], None, "the program `%s` was not found" % binary
    except subprocess.TimeoutExpired:
        return [], None, "no result after %d seconds" % TIMEOUT_S
    events = []
    for line in r.stdout.splitlines():
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict):
            events.append(ev)
    result = next((e for e in reversed(events) if e.get("type") == "result"), None)
    if result is None:
        return events, None, "no result event (exit code %d): %s" % (r.returncode, (r.stderr or r.stdout)[-300:])
    return events, result, None


def read_answer(folder):
    try:
        with open(os.path.join(folder, "answer.json"), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def run(folder, prompt, model=DEFAULT_MODEL, max_turns=MAX_TURNS, log=None, version="v0", binary="claude"):
    """Run Claude Code on one incident folder. The summary has the same keys as agent.run, and some more."""
    prepare(folder)
    out = {"harness": "cc", "agent": version, "model": model, "turns": 0, "tool_calls": {}, "input_tokens": 0, "output_tokens": 0,
           "cost_usd": None, "notional_cost_usd": None, "answer_written": False, "stopped": None, "error": None, "follow_ups": 0,
           "challenged": False, "outside_folder": [], "other_commands": [], "read_commands_in_folder": 0, "denials": 0}
    started, session, text, challenged = time.time(), None, prompt, False
    while True:
        events, result, err = call_claude(folder, text, model, max_turns, session, binary)
        if log:
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": round(time.time() - started, 1), "sent": text[:4000]}, ensure_ascii=False) + "\n")
                for ev in events:
                    line = json.dumps(ev, ensure_ascii=False)
                    if len(line) > 20000:               # a long tool result: keep the line valid JSON
                        line = json.dumps({"type": ev.get("type"), "cut": True, "start": line[:6000]}, ensure_ascii=False)
                    f.write(line + "\n")
        counts, outside, odd, reads = audit(events, folder)
        out["read_commands_in_folder"] += reads
        for k, v in counts.items():
            out["tool_calls"][k] = out["tool_calls"].get(k, 0) + v
        out["outside_folder"] += outside
        out["other_commands"] += odd
        if err:
            out["error"], out["stopped"] = err[:500], "api_error"
            break
        session = result.get("session_id") or session
        usage = result.get("usage") or {}
        out["turns"] += result.get("num_turns") or 0
        out["input_tokens"] += sum(usage.get(k) or 0 for k in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens"))
        out["output_tokens"] += usage.get("output_tokens") or 0
        if result.get("total_cost_usd") is not None:      # what the tokens would cost on the API; the subscription is not billed per run
            out["notional_cost_usd"] = round((out["notional_cost_usd"] or 0) + float(result["total_cost_usd"]), 6)
        out["denials"] += len(result.get("permission_denials") or [])
        if result.get("is_error") and result.get("subtype") != "error_max_turns":
            out["error"], out["stopped"] = str(result.get("result"))[:500], "api_error"
            break
        answer = read_answer(folder)
        problems = ["there is no readable answer.json in this folder"] if answer is None else grade.validate(answer)
        if not problems and version == "v1" and not challenged and agent.removes_constraint(answer):
            challenged = out["challenged"] = True
            text = agent.challenge_text(answer).replace("NOT SAVED YET.", "Your answer.json is not accepted yet.") \
                .replace("Then call write_answer again, with the same answer or a changed one. That call is saved.",
                         "Then write answer.json again, the same or changed, or say that it stays as it is. That version is final.")
        elif not problems:
            out["stopped"] = "finished"
            break
        elif out["follow_ups"] >= MAX_FOLLOW_UPS:
            out["stopped"] = "max_turns" if answer is None else "answer_never_valid"
            break
        else:
            text = "answer.json is not accepted. Problems:\n- " + "\n- ".join(problems[:12]) + "\nWrite answer.json again in this folder."
        out["follow_ups"] += 1
        if not session:
            out["error"], out["stopped"] = "no session id to continue with", "api_error"
            break
    answer = read_answer(folder)
    out["answer_written"] = answer is not None and not grade.validate(answer)
    out["seconds"] = round(time.time() - started, 1)
    return out


def main(argv):
    if len(argv) < 2 or argv[1].startswith("-"):
        print(__doc__)
        return 2
    opt = lambda name, default: argv[argv.index(name) + 1] if name in argv else default
    with open(os.path.join(HERE, "PROMPT_CC.md"), encoding="utf-8") as f:
        prompt = f.read()
    print(json.dumps(run(argv[1], prompt, opt("--model", DEFAULT_MODEL), int(opt("--max-turns", MAX_TURNS)),
                         log=argv[1].rstrip("/") + ".log.jsonl", version=opt("--agent", "v0")), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
