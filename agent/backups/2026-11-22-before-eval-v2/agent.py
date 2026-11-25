#!/usr/bin/env python3
"""Our diagnosis agent: one loop, five tools, any model that OpenRouter serves.

The model gets the prompt and may call these tools, nothing else:
  list_dir     list a folder of the incident folder
  read_file    read part of a file, with line numbers
  search       find a pattern in the files (regular expression)
  query_store  one read-only, limited request to a data store (see dbtool.py)
  write_answer check answer.json against the format and save it; this ends the run

There is no shell tool and no network tool. File tools cannot leave the incident folder.

  agent.py <incident-folder> [--model ID] [--max-turns N]
The key is read from the environment variable OPENROUTER_API_KEY. It is never printed.

NOT TESTED against the real API: the loop was tested with a scripted stand-in for the model.
"""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dbtool
import grade

URL = "https://openrouter.ai/api/v1/chat/completions"
DEFAULT_MODEL = "anthropic/claude-haiku-4.5"
MAX_TURNS = 50
WARN_LEFT = 8                 # with this many turns left the harness tells the agent to finish
MAX_OUT = 8000                 # characters returned by one tool call
MAX_LINES = 300                # lines returned by one read_file
MAX_HITS = 60
MAX_FILE = 3 * 1024 * 1024     # search skips larger files
MAX_INPUT_TOKENS = 2000000     # the run stops when the model has been sent this many tokens in total

TOOLS = [
    {"name": "list_dir", "description": "List the entries of a folder. Paths are relative to the incident folder.",
     "parameters": {"type": "object", "properties": {"path": {"type": "string", "description": "Folder, for example '.' or 'source/src'."}}, "required": ["path"]}},
    {"name": "read_file", "description": "Read lines of a text file. Every line comes with its line number. At most %d lines per call." % MAX_LINES,
     "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "start_line": {"type": "integer", "description": "First line, counted from 1. Default 1."},
                                                     "max_lines": {"type": "integer", "description": "Default and maximum %d." % MAX_LINES}}, "required": ["path"]}},
    {"name": "search", "description": "Search the files under a path for a regular expression. Returns file:line: text, at most %d hits." % MAX_HITS,
     "parameters": {"type": "object", "properties": {"pattern": {"type": "string"}, "path": {"type": "string", "description": "File or folder. Default '.'."}}, "required": ["pattern"]}},
    {"name": "query_store", "description": (
        "One read-only request to a data store of the running system. Limits protect the application: "
        "MongoDB find needs a filter on indexed fields only and returns at most 20 documents; Redis reads are on one named key, "
        "plus dbsize, info and one scan step; memcached allows get on one key. A request outside the limits is refused with the reason. "
        "Request examples: {\"store\":\"user-mongodb\",\"op\":\"list_indexes\",\"db\":\"user\",\"collection\":\"user\"}; "
        "{\"store\":\"user-mongodb\",\"op\":\"find\",\"db\":\"user\",\"collection\":\"user\",\"filter\":{\"user_id\":5},\"limit\":1}; "
        "{\"store\":\"post-storage-mongodb\",\"op\":\"estimated_count\",\"db\":\"post\",\"collection\":\"post\"}; "
        "{\"store\":\"user-mongodb\",\"op\":\"list_collections\",\"db\":\"user\"}; "
        "{\"store\":\"home-timeline-redis\",\"op\":\"dbsize\"}; {\"store\":\"home-timeline-redis\",\"op\":\"scan\",\"cursor\":0,\"count\":50}; "
        "{\"store\":\"social-graph-redis\",\"op\":\"zcard\",\"key\":\"5:followers\"}; "
        "{\"store\":\"home-timeline-redis\",\"op\":\"zrange\",\"key\":\"5\",\"start\":0,\"stop\":9,\"rev\":true}; "
        "{\"store\":\"user-memcached\",\"op\":\"get\",\"key\":\"username_5:user_id\"}. "
        "Store names are the container names in source/docker-compose.yml."),
     "parameters": {"type": "object", "properties": {"request": {"type": "object"}}, "required": ["request"]}},
    {"name": "write_answer", "description": "Check your answer against ANSWER_FORMAT.md and save it as answer.json. If it is not valid you get the problems back and can call again. A valid answer ends your work.",
     "parameters": {"type": "object", "properties": {"answer": {"type": "object"}}, "required": ["answer"]}},
]


class Sandbox:
    """The file tools. Every path is resolved and must stay inside the incident folder."""

    def __init__(self, root, store=dbtool.query, check=False):
        self.root = os.path.realpath(root)
        self.store = store
        self.saved = False
        self.bad_answers = 0
        self.check = check              # agent v1: ask once before saving an answer that removes the constraint
        self.challenged = False

    def _path(self, rel):
        if not isinstance(rel, str):
            raise ValueError("path must be text")
        full = os.path.realpath(os.path.join(self.root, rel.lstrip("/")))
        if full != self.root and not full.startswith(self.root + os.sep):
            raise ValueError("that path is outside the incident folder")
        return full

    def list_dir(self, path="."):
        full = self._path(path)
        if not os.path.isdir(full):
            raise ValueError("not a folder: %s" % path)
        rows = []
        for name in sorted(os.listdir(full)):
            p = os.path.join(full, name)
            if os.path.realpath(p) != p and not os.path.realpath(p).startswith(self.root + os.sep):
                continue                                       # a link that points outside
            rows.append(name + "/" if os.path.isdir(p) else "%s  (%d bytes)" % (name, os.path.getsize(p)))
        return "\n".join(rows) or "(empty)"

    def read_file(self, path, start_line=1, max_lines=MAX_LINES):
        full = self._path(path)
        if not os.path.isfile(full):
            raise ValueError("not a file: %s" % path)
        start = max(1, int(start_line or 1))
        count = max(1, min(int(max_lines or MAX_LINES), MAX_LINES))
        out, total = [], 0
        with open(full, encoding="utf-8", errors="replace") as f:
            for n, line in enumerate(f, start=1):
                total = n
                if start <= n < start + count:
                    out.append("%6d\t%s" % (n, line.rstrip("\n")[:2000]))
        if not out:
            return "(no lines %d and after; the file has %d lines)" % (start, total)
        tail = "" if start + count > total else "\n(the file has %d lines; continue with start_line=%d)" % (total, start + count)
        return "\n".join(out) + tail

    def search(self, pattern, path="."):
        try:
            rx = re.compile(pattern)
        except re.error as err:
            raise ValueError("not a valid regular expression: %s" % err)
        full = self._path(path)
        files = [full] if os.path.isfile(full) else [os.path.join(d, n) for d, _, names in os.walk(full) for n in sorted(names)]
        hits = []
        for p in sorted(files):
            if os.path.islink(p) or not os.path.isfile(p) or os.path.getsize(p) > MAX_FILE:
                continue
            try:
                with open(p, encoding="utf-8", errors="strict") as f:
                    for n, line in enumerate(f, start=1):
                        if rx.search(line):
                            hits.append("%s:%d: %s" % (os.path.relpath(p, self.root), n, line.strip()[:300]))
                            if len(hits) >= MAX_HITS:
                                return "\n".join(hits) + "\n(stopped at %d hits; narrow the pattern or the path)" % MAX_HITS
            except (UnicodeDecodeError, OSError):
                continue                                       # not a text file
        return "\n".join(hits) or "(no match)"

    def query_store(self, request):
        return json.dumps(self.store(request), ensure_ascii=False)

    def write_answer(self, answer):
        problems = grade.validate(answer)
        if problems:
            self.bad_answers += 1
            return "NOT SAVED. Problems:\n- " + "\n- ".join(problems[:12])
        if self.check and not self.challenged and removes_constraint(answer):
            self.challenged = True
            return challenge_text(answer)
        with open(os.path.join(self.root, "answer.json"), "w", encoding="utf-8") as f:
            json.dump(answer, f, indent=1, ensure_ascii=False)
        self.saved = True
        return "Saved as answer.json."

    def call(self, name, args):
        if name not in {t["name"] for t in TOOLS}:
            return "No such tool: %s" % name
        if not isinstance(args, dict):
            return "The arguments must be a JSON object."
        try:
            text = getattr(self, name)(**args)
        except (ValueError, TypeError, OSError) as err:
            return "Error: %s" % err
        return text if len(text) <= MAX_OUT else text[:MAX_OUT] + "\n(cut at %d characters)" % MAX_OUT


def removes_constraint(answer):
    """True when the answer says 'mistake' and its fix takes the constraint away."""
    if answer.get("verdict") != "mistake":
        return False
    branch = answer.get("if_mistake") or {}
    return branch.get("fix_class") == "revert" and branch.get("root_cause") == answer.get("constraint")


def challenge_text(answer):
    """The one question agent v1 asks before it saves such an answer. It says nothing about which answer is right."""
    what = next((f.get("what") for f in answer.get("findings", []) if f.get("id") == answer.get("constraint")), None)
    used, quote = answer.get("decision_used"), answer.get("decision_quote")
    lines = ["NOT SAVED YET. This is a check that is made once, whatever the right answer is.",
             "Your answer removes this: %s" % (what or "the constraint")]
    if used:
        lines.append("You rely on decision %s: \"%s\"" % (used, quote or ""))
        lines.append("Read that decision again in DECISIONS.md, word by word. Does it say the team wants the thing you remove, "
                     "or that the team does not want it? That a thing is missing from the code does not show that nobody decided it.")
    else:
        lines.append("You cite no decision. Read DECISIONS.md again and look for an entry about the thing you remove.")
    lines.append("If a decision says the team wants it: the verdict is intended and the fix must keep it. "
                 "If a decision says the opposite: keep your answer. If no decision speaks about it: the verdict is cannot_decide.")
    lines.append("Then call write_answer again, with the same answer or a changed one. That call is saved.")
    return "\n".join(lines)


KEY_URL = "https://openrouter.ai/api/v1/key"


def key_remaining(fetch=None):
    """What the API key may still spend, in USD. Returns (remaining, limit).
    remaining is None when the key has no limit of its own. Raises RuntimeError when the question cannot be asked.
    Only the key's own limit is seen here, not the balance of the account. Nothing about the key is printed."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    try:
        if fetch is None:
            req = urllib.request.Request(KEY_URL, headers={"Authorization": "Bearer " + key})
            with urllib.request.urlopen(req, timeout=30) as r:
                data = json.loads(r.read().decode("utf-8"))
        else:
            data = fetch()
        info = data["data"]
        left, limit = info.get("limit_remaining"), info.get("limit")
        return (None if left is None else float(left)), (None if limit is None else float(limit))
    except urllib.error.HTTPError as err:
        raise RuntimeError("HTTP %d when asking for the key's limit" % err.code)
    except (OSError, ValueError, KeyError, TypeError) as err:
        raise RuntimeError("could not read the key's limit: %s" % type(err).__name__)


def call_openrouter(model, messages, cache=True):
    """One request to OpenRouter. Returns the JSON answer. Tries again on a busy or failing server."""
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("OPENROUTER_API_KEY is not set")
    sent = json.loads(json.dumps(messages))
    if cache:                                   # ask the provider to remember what was already sent
        for m in (sent[0], sent[-1]):
            if isinstance(m.get("content"), str) and m["content"]:
                m["content"] = [{"type": "text", "text": m["content"], "cache_control": {"type": "ephemeral"}}]
    body = json.dumps({"model": model, "messages": sent, "max_tokens": 4000,
                       "tools": [{"type": "function", "function": t} for t in TOOLS]}).encode()
    last = None
    for attempt in range(4):
        req = urllib.request.Request(URL, data=body, headers={"Authorization": "Bearer " + key, "Content-Type": "application/json",
                                                              "X-Title": "intent-worlds diagnosis agent"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                data = json.loads(r.read().decode("utf-8"))
            if "choices" in data:
                return data
            last = RuntimeError("the API answered without a choice: %s" % json.dumps(data)[:300])
        except urllib.error.HTTPError as err:
            text = err.read().decode("utf-8", "replace")[:400]
            last = RuntimeError("HTTP %d from the API: %s" % (err.code, text))
            if err.code not in (408, 429, 500, 502, 503, 529):
                raise last
        except (OSError, ValueError) as err:            # OSError covers URLError, timeouts and TLS errors on a broken connection
            last = RuntimeError("could not reach the API: %s" % err)
        time.sleep(5 * (attempt + 1))
    raise last


def run(folder, prompt, model=DEFAULT_MODEL, max_turns=MAX_TURNS, call=call_openrouter, store=dbtool.query, log=None, version="v0"):
    """Run the agent on one incident folder. Returns a summary; answer.json is written into the folder."""
    box = Sandbox(folder, store, check=(version == "v1"))
    messages = [{"role": "system", "content": prompt}, {"role": "user", "content": "The incident folder is ready. Start."}]
    out = {"agent": version, "model": model, "turns": 0, "tool_calls": {}, "input_tokens": 0, "output_tokens": 0, "cost_usd": None,
           "answer_written": False, "stopped": None, "error": None, "budget_warned": False}
    cache, nudged, started = model.startswith("anthropic/"), False, time.time()

    def note(kind, value):
        if log:
            with open(log, "a", encoding="utf-8") as f:
                f.write(json.dumps({"t": round(time.time() - started, 1), kind: value}, ensure_ascii=False) + "\n")

    while out["turns"] < max_turns:
        try:
            try:
                data = call(model, messages, cache) if call is call_openrouter else call(model, messages)
            except RuntimeError as err:
                if cache and "HTTP 400" in str(err):           # the provider did not accept the caching marks
                    cache = False
                    note("note", "caching marks refused, going on without them")
                    continue
                raise
        except Exception as err:                                # the API failed for good; report it, do not crash
            out["error"], out["stopped"] = str(err)[:500], "api_error"
            break
        out["turns"] += 1
        usage = data.get("usage") or {}
        out["input_tokens"] += usage.get("prompt_tokens") or 0
        out["output_tokens"] += usage.get("completion_tokens") or 0
        if usage.get("cost") is not None:
            out["cost_usd"] = round((out["cost_usd"] or 0) + float(usage["cost"]), 6)
        msg = data["choices"][0].get("message") or {}
        calls = msg.get("tool_calls") or []
        messages.append({"role": "assistant", "content": msg.get("content") or "", **({"tool_calls": calls} if calls else {})})
        note("assistant", {"text": msg.get("content") or "", "calls": [c.get("function") for c in calls]})
        if not calls:
            if box.saved or nudged:
                out["stopped"] = "finished" if box.saved else "no_answer"
                break
            nudged = True
            messages.append({"role": "user", "content": "You have not saved an answer yet. Call write_answer with your answer."})
            continue
        for c in calls:
            fn = c.get("function") or {}
            name = fn.get("name") or ""
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except ValueError:
                args = None
            text = box.call(name, args) if args is not None else "The arguments are not valid JSON."
            out["tool_calls"][name] = out["tool_calls"].get(name, 0) + 1
            messages.append({"role": "tool", "tool_call_id": c.get("id"), "content": text})
            note("tool", {"name": name, "args": args, "result": text[:1500]})
        if box.saved:
            out["stopped"] = "finished"
            break
        if box.bad_answers >= 4:
            out["stopped"] = "answer_never_valid"
            break
        if out["input_tokens"] > MAX_INPUT_TOKENS:
            out["stopped"] = "token_budget"
            break
        left = max_turns - out["turns"]
        if left == WARN_LEFT and max_turns > WARN_LEFT:          # say once that the turns are running out
            out["budget_warned"] = True
            messages.append({"role": "user", "content": "You have %d turns left. Finish with what you have found and call write_answer. "
                                                        "A run that ends with no saved answer counts as no answer." % left})
            note("note", "budget warning sent, %d turns left" % left)
    else:
        out["stopped"] = "max_turns"
    out["stopped"] = out["stopped"] or "max_turns"
    out["answer_written"] = box.saved
    out["seconds"] = round(time.time() - started, 1)
    return out


def main(argv):
    if len(argv) < 2 or argv[1].startswith("-"):
        print(__doc__)
        return 2
    model, turns = DEFAULT_MODEL, MAX_TURNS
    if "--model" in argv:
        model = argv[argv.index("--model") + 1]
    if "--max-turns" in argv:
        turns = int(argv[argv.index("--max-turns") + 1])
    with open(os.path.join(HERE, "PROMPT_LIVE.md"), encoding="utf-8") as f:
        prompt = f.read()
    print(json.dumps(run(argv[1], prompt, model, turns, log=os.path.join(argv[1], "..", os.path.basename(argv[1].rstrip("/")) + ".log.jsonl"))))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
