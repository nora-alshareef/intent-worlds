#!/usr/bin/env python3
"""The baseline agent: one prompt, one model, one loop. The loop is Claude Code in headless mode.

  agent.py <incident-folder> [--model NAME] [--max-turns N]

Which model is called, and with which key, is read from the file .env next to this file.
Each person fills in their own copy; it is never committed (see .env.example).

Everything the agent is told is in this file: PROMPT, ANSWER_FORMAT and STORE_TOOL. A change
to any of them is a new agent; raise INPUT_VERSION so that results are not mixed.

What stays ours around the loop:
  the limits   file tools, plus ONE command: python3 store.py '<json>' (store.py, read-only and limited)
  the check    answer.json is checked against the format; if it does not pass, the agent is told
               why and may write it again, at most MAX_FOLLOW_UPS times
  the audit    every tool call is listed; a path outside the incident folder, or any other
               command that ran, is reported. Claude Code itself lets plain read commands
               (ls, grep, cat) run inside the folder and refuses the rest; refused calls are counted. The audit DETECTS a step outside the folder; it does not
               prevent one. run.py therefore builds the folder outside the project.
A key that is set in the shell and not in .env is never passed on, so that a run cannot be
billed to a key nobody chose. With no key in .env, the Claude login of this machine is used.
"""
import json
import os
import re
import subprocess
import sys
import time

import grade

HERE = os.path.dirname(os.path.abspath(__file__))
INPUT_VERSION = 3              # 1: first baseline (fix proposed, four verdicts). 3: this format. 2 was planned and never run.
ENV_FILE = os.path.join(HERE, ".env")
DEFAULT_MODEL = "haiku"
PASSED_ON = ("ANTHROPIC_", "CLAUDE_CODE_")          # lines of .env with these prefixes go to the `claude` program
MAX_TURNS = 50
MAX_FOLLOW_UPS = 4
TIMEOUT_S = 1800
TOOLS = "Read,Grep,Glob,Write,Bash"                 # the only built-in tools the model is shown
ALLOWED = ["Read", "Grep", "Glob", "Write", "Bash(python3 store.py:*)"]
DENIED = ["WebFetch", "WebSearch", "Task", "Edit", "NotebookEdit"]
PATH_TOOLS = ("Read", "Write", "Edit", "Grep", "Glob", "NotebookEdit")
READ_ONLY = r"(find|ls|cat|head|tail|wc|grep|rg|sort|uniq|cut|file|stat|tree|pwd|echo|true)"

PROMPT = """# Incident diagnosis

You are the on-call engineer for a social network application: several services behind an nginx gateway, with MongoDB, Redis and memcached stores. An incident has been reported. Find its root cause, and say whether that cause is something the team decided to have.

## What you have

- `TICKET.md`: the incident as reported.
- `source/`: the system as it is deployed now (code, configuration, web pages, scripts, deployment files).
- `evidence/`: service logs and request traces captured during the incident.
- `DECISIONS.md`: decisions the team has made about this system. All of them are in force.
- The running system's data stores, through one command: `python3 store.py '<request as JSON>'`. `STORE_TOOL.md` lists the requests. It is read-only and limited so that it cannot slow the application: a refused request tells you why.

You cannot send requests to the application, restart anything or change anything. The store command is the only command you may run. Work only inside this folder.

## What to keep in mind

- The root cause is a place: a line in a file under `source/`, or something inside a data store. It is not always a line in a file. Something done to the running system leaves its mark in a store or a setting; name that.
- Logs and traces show the symptom. They are your evidence, not the root cause.
- Something that causes a problem is not always a mistake. The team may have decided to have it. `DECISIONS.md` is the only record of what the team decided, so compare the cause with it before you give a verdict.
- You are not asked for a fix. You are asked whether the cause should stay as it is.
- If no decision speaks about the cause, say so. "No decision settles this" is a complete and correct answer when it is true.
- If you conclude that nothing is wrong with the system, say that.
- Do not guess file names, line numbers, log lines or store contents. Quote what you rely on.

## What to deliver

Read `ANSWER_FORMAT.md`, then write your answer to `answer.json` in this folder. It is checked against the format, and you are told if it does not pass. Only that file is read.
"""

ANSWER_FORMAT = """# Format of answer.json

Write one JSON object with exactly these five fields. A program reads it and compares it field by field. Nothing outside the fields is read.

| Field | Value |
|---|---|
| `verdict` | `"keep"`, `"need_fix"`, `"needs_review"` or `"no_fault"` |
| `root_cause` | an object, described below. `null` when the verdict is `no_fault` |
| `evidence` | one quote, copied exactly |
| `decision_ref` | the id of one entry of `DECISIONS.md`, such as `"D-012"`, or `""` |
| `message` | one or two sentences for the engineer who reads the ticket |

## verdict

The verdict is about the root cause you name, and about nothing else.

- `keep`: a decision in `DECISIONS.md` says the team wants it this way. It stays as it is. This does not claim that nothing else has to change.
- `need_fix`: it contradicts a decision in `DECISIONS.md`.
- `needs_review`: no decision in `DECISIONS.md` says which of the two it is.
- `no_fault`: nothing is wrong with the system.

## root_cause

The thing the incident runs into, and where it is.

| Field | Value |
|---|---|
| `where` | `"configuration"`: a line in a settings file, which is a file whose name ends in .conf, .json, .yml, .yaml, .cfg, .ini or .env. `"code"`: a line in any other file under `source/`. `"data_store"`: something inside a data store, such as an index, a document, a key, or data that is missing or was changed. `"resource"`: the machine itself, meaning CPU, memory, disk or network. |
| `file` | The path under `source/`, for example `"nginx-web-server/conf/nginx.conf"`. `null` unless `where` is `code` or `configuration`. |
| `line` | The line number in the file you were given, one whole number. If the cause covers several lines, give the first. `null` unless `where` is `code` or `configuration`. |
| `store` | The container name of the store, as in `source/docker-compose.yml`. `null` unless `where` is `data_store`. |
| `what` | One or two sentences. For a data store, name the collection, index, key or field exactly as the store names it. |

## evidence

One quote that shows the cause at work, copied character for character from a log under `evidence/`, a file under `source/`, `DECISIONS.md`, or the output of a store request you made. At least 12 characters, one continuous piece: do not join pieces with "...". The quote is searched for, and a quote that cannot be found counts as no evidence.

## decision_ref

The entry your verdict rests on. Required for `keep` and `need_fix`. `""` for `needs_review` and `no_fault`.

## Example

The example is about a system that is not yours.

```json
{
 "verdict": "need_fix",
 "root_cause": {
  "where": "code",
  "file": "upload/handler.js",
  "line": 42,
  "store": null,
  "what": "The upload handler rejects every file larger than 1 MB."
 },
 "evidence": "WARN upload rejected: file_size_mb=2 limit_mb=1 reason=SIZE_LIMIT_EXCEEDED",
 "decision_ref": "D-012",
 "message": "The code enforces a 1 MB limit, but D-012 says uploads up to 10 MB must work."
}
```
"""

STORE_TOOL = """# The store command

Run:  python3 store.py '<request as JSON>'

One read-only request to a data store of the running system. Limits protect the application: MongoDB find needs a filter on indexed fields only and returns at most 20 documents; Redis reads are on one named key, plus dbsize, info and one scan step; memcached allows get on one key. A request outside the limits is refused with the reason.

Request examples:

    python3 store.py '{"store":"user-mongodb","op":"list_collections","db":"user"}'
    python3 store.py '{"store":"user-mongodb","op":"list_indexes","db":"user","collection":"user"}'
    python3 store.py '{"store":"user-mongodb","op":"find","db":"user","collection":"user","filter":{"user_id":5},"limit":1}'
    python3 store.py '{"store":"post-storage-mongodb","op":"estimated_count","db":"post","collection":"post"}'
    python3 store.py '{"store":"home-timeline-redis","op":"dbsize"}'
    python3 store.py '{"store":"home-timeline-redis","op":"scan","cursor":0,"count":50}'
    python3 store.py '{"store":"social-graph-redis","op":"zcard","key":"5:followers"}'
    python3 store.py '{"store":"home-timeline-redis","op":"zrange","key":"5","start":0,"stop":9,"rev":true}'
    python3 store.py '{"store":"user-memcached","op":"get","key":"username_5:user_id"}'

Store names are the container names in source/docker-compose.yml.
"""

# The command the agent runs. It hands the request to store.py of this project without showing the project's path.
STORE_WRAPPER = '''import os, subprocess, sys
sys.exit(subprocess.call([sys.executable, os.path.join(os.environ["INTENT_AGENT_DIR"], "store.py")] + sys.argv[1:]))
'''


def settings():
    """What .env says: {"model", "auth", "env"}. `env` holds the credentials to hand to the
    `claude` program; `auth` is "api_key" or "subscription" and is written into every result."""
    values = {}
    if os.path.isfile(ENV_FILE):
        with open(ENV_FILE, encoding="utf-8") as f:
            for line in f:
                name, eq, value = line.strip().partition("=")
                if eq and not name.startswith("#"):
                    values[name.strip()] = value.strip().strip("'\"")
    env = {k: v for k, v in values.items() if k.startswith(PASSED_ON) and v}
    has_key = "ANTHROPIC_API_KEY" in env or "ANTHROPIC_AUTH_TOKEN" in env
    return {"model": values.get("INTENT_MODEL") or DEFAULT_MODEL, "auth": "api_key" if has_key else "subscription", "env": env}


def prepare(folder):
    """Put the answer format, the store command and its description into the incident folder."""
    for name, text in (("ANSWER_FORMAT.md", ANSWER_FORMAT), ("STORE_TOOL.md", STORE_TOOL), ("store.py", STORE_WRAPPER)):
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            f.write(text)


def audit(events, folder):
    """Every tool call of one run. Returns (counts per tool, paths outside the folder,
    commands that are neither the store command nor a plain read inside the folder)."""
    root = os.path.realpath(folder)
    counts, outside, odd = {}, [], []

    def inside(path):
        real = os.path.realpath(path if os.path.isabs(path) else os.path.join(root, path))
        return real == root or real.startswith(root + os.sep)

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
                    if isinstance(p, str) and p and not inside(p):
                        outside.append("%s %s" % (name, p))
                pat = arg.get("pattern") if name == "Glob" else None
                if isinstance(pat, str) and (pat.startswith(("/", "~")) or ".." in pat):
                    outside.append("Glob %s" % pat)
            elif name == "Bash":
                cmd = str(arg.get("command") or "")
                if re.match(r"^python3 store\.py '[^']*'\s*$", cmd):
                    continue
                bare = re.sub(r"\"[^\"]*\"|'[^']*'", " ", cmd)            # name patterns in quotes are not paths
                paths = re.findall(r"(?<![\w.-])(/[^\s|;&>]*|~[^\s|;&>]*|\.\.[^\s|;&>]*)", bare)
                stays = all(p == "/dev/null" or inside(os.path.expanduser(p)) for p in paths)
                reads = all(re.match(r"^\s*(%s|xargs)\b" % READ_ONLY[1:-1], part) for part in re.split(r"\|\||&&|[|;]", bare)) \
                    and ">" not in bare.replace("2>/dev/null", "") and "$(" not in cmd and "`" not in cmd
                if not (stays and reads):
                    odd.append(cmd[:200])
            elif name != "TodoWrite":
                odd.append("tool %s" % name)
    return counts, outside, odd


def call_claude(folder, text, model, max_turns, session=None, binary="claude", store_log=None):
    """One `claude -p` run in the folder. Returns (events, result event or None, error text or None)."""
    cmd = [binary, "-p", text, "--model", model, "--output-format", "stream-json", "--verbose", "--max-turns", str(max_turns)]
    if session:
        cmd += ["--resume", session]
    cmd += ["--tools", TOOLS, "--allowedTools"] + ALLOWED + ["--disallowedTools"] + DENIED
    env = {k: v for k, v in os.environ.items() if not k.startswith(PASSED_ON)}
    env.update(settings()["env"])                     # only what .env names, never a key from the shell
    if "ANTHROPIC_AUTH_TOKEN" in env:                 # a gateway such as OpenRouter needs the key variable present and empty
        env.setdefault("ANTHROPIC_API_KEY", "")
    env["INTENT_AGENT_DIR"] = HERE
    if store_log:
        env["INTENT_STORE_LOG"] = store_log
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


def write_log(log, started, sent, events):
    with open(log, "a", encoding="utf-8") as f:
        f.write(json.dumps({"t": round(time.time() - started, 1), "sent": sent[:4000]}, ensure_ascii=False) + "\n")
        for ev in events:
            line = json.dumps(ev, ensure_ascii=False)
            if len(line) > 20000:                    # a long tool result: keep the line valid JSON
                line = json.dumps({"type": ev.get("type"), "cut": True, "start": line[:6000]}, ensure_ascii=False)
            f.write(line + "\n")


def run(folder, model=None, max_turns=MAX_TURNS, log=None, store_log=None, binary="claude"):
    """Run the agent on one incident folder. Returns a summary of the run; the answer is
    answer.json in the folder. `stopped` is one of: finished, max_turns, answer_never_valid, api_error."""
    prepare(folder)
    model = model or settings()["model"]
    out = {"agent": "baseline", "input_version": INPUT_VERSION, "model": model, "auth": settings()["auth"], "turns": 0, "tool_calls": {},
           "input_tokens": 0, "output_tokens": 0, "notional_cost_usd": None, "answer_written": False, "stopped": None,
           "error": None, "follow_ups": 0, "outside_folder": [], "other_commands": [], "denials": 0}
    started, session, text = time.time(), None, PROMPT
    while True:
        events, result, err = call_claude(folder, text, model, max_turns, session, binary, store_log)
        if log:
            write_log(log, started, text, events)
        counts, outside, odd = audit(events, folder)
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
        if result.get("total_cost_usd") is not None:  # what the tokens would cost on the API; the subscription is not billed per run
            out["notional_cost_usd"] = round((out["notional_cost_usd"] or 0) + float(result["total_cost_usd"]), 6)
        denied = [str((d.get("tool_input") or {}).get("command") or "")[:200] for d in result.get("permission_denials") or []]
        out["denials"] += len(denied)
        out["other_commands"] = [c for c in out["other_commands"] if c not in denied]   # asked for and refused: it did not run
        if result.get("is_error") and result.get("subtype") != "error_max_turns":
            out["error"], out["stopped"] = str(result.get("result"))[:500], "api_error"
            break
        answer = read_answer(folder)
        problems = ["there is no readable answer.json in this folder"] if answer is None else grade.validate(answer, folder)
        if not problems:
            out["stopped"] = "finished"
            break
        if out["follow_ups"] >= MAX_FOLLOW_UPS:
            out["stopped"] = "max_turns" if answer is None else "answer_never_valid"
            break
        if not session:
            out["error"], out["stopped"] = "no session id to continue with", "api_error"
            break
        text = "answer.json is not accepted. Problems:\n- " + "\n- ".join(problems[:12]) + "\nWrite answer.json again in this folder."
        out["follow_ups"] += 1
    answer = read_answer(folder)
    out["answer_written"] = answer is not None and not grade.validate(answer, folder)
    out["seconds"] = round(time.time() - started, 1)
    return out


def main(argv):
    if len(argv) < 2 or argv[1].startswith("-"):
        print(__doc__)
        return 2

    def option(name, default):
        return argv[argv.index(name) + 1] if name in argv else default
    folder = argv[1].rstrip("/")
    print(json.dumps(run(folder, option("--model", None), int(option("--max-turns", MAX_TURNS)),
                         log=folder + ".log.jsonl"), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
