#!/usr/bin/env python3
"""Run our agent on one world while that world is live, then undo the world.

  run_live.py <world-id> [--harness own|cc] [--agent v0|v1] [--model ID] [--cases A,B,N] [--max-turns N] [--tag NAME] [--keep-live]

  --harness own  our loop in agent.py, through OpenRouter (default). Needs OPENROUTER_API_KEY.
  --harness cc   Claude Code in headless mode on the Claude subscription (agent_cc.py). Default model: haiku.

  --tag NAME  written into every result line, with a short hash of the prompt, harness, format, grader and labels.
              A case that already has an answer under the same tag is skipped, so a stopped series can be continued.
              The exit code is 3 when the API failed, so a loop over worlds stops there.

  --agent v0  the baseline (default): one prompt, one loop.
  --agent v1  the same, plus one check in the harness: an answer that says "mistake" and removes
              the constraint is not saved the first time; the agent is asked to re-read the decision.

What it does, in order:
  1. applies the world (world_live.py: baseline probes, change, probes, logs and traces saved)
  2. for each case builds one incident folder under agent/runs/<time>/ with a neutral name:
     TICKET.md, source/ (the deployed tree as it is now), evidence/, DECISIONS.md of that
     case, ANSWER_FORMAT.md
  3. runs the agent in that folder and grades answer.json with grade.py
  4. appends one line per case to agent/results/results.jsonl
  5. reverts the world, also when something above failed (unless --keep-live)

The agent talks to the model through OpenRouter; set OPENROUTER_API_KEY first.
NOT TESTED end to end: written without access to Docker or to the API.
"""
import hashlib
import json
import os
import shutil
import sys
import time
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import agent
import agent_cc
import grade
import scenario_v2
import world
import world_live

RESULTS = os.path.join(HERE, "results", "results.jsonl")
PER_CASE_USD = 0.35            # the most one case has cost so far was $0.27 (50 turns on Haiku 4.5)
LABELS = os.path.join(ROOT, "scenarios-v2", "_answers")


def file_hash(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:12]


def option(argv, name, default):
    return argv[argv.index(name) + 1] if name in argv else default


def build_folder(run_dir, entry, entries, case):
    """One incident folder. Its name says nothing about the world or the case."""
    top = os.path.join(run_dir, "incident-" + uuid.uuid4().hex[:8])
    os.makedirs(top)
    shutil.copytree(world.BASE, os.path.join(top, "source"), symlinks=False,
                    ignore=lambda d, names: [n for n in names if d == world.BASE and n in scenario_v2.SKIP_TOP],
                    ignore_dangling_symlinks=True)
    ev = os.path.join(world_live.EVID, entry["id"])
    os.makedirs(os.path.join(top, "evidence", "logs"))
    for name in sorted(os.listdir(os.path.join(ev, "logs"))):
        with open(os.path.join(ev, "logs", name), encoding="utf-8", errors="replace") as f:
            kept = [line for line in f if not scenario_v2.TOOL_MARKS.search(line)]
        with open(os.path.join(top, "evidence", "logs", name), "w", encoding="utf-8") as f:
            f.writelines(kept)
    if os.path.exists(os.path.join(ev, "traces.json")):
        shutil.copyfile(os.path.join(ev, "traces.json"), os.path.join(top, "evidence", "traces.json"))
    with open(os.path.join(top, "TICKET.md"), "w", encoding="utf-8") as f:
        f.write("# Incident ticket\n\n" + entry["ticket"].strip() + "\n")
    text, _, _ = scenario_v2.decisions_text(entries, entry["id"], case)
    with open(os.path.join(top, "DECISIONS.md"), "w", encoding="utf-8") as f:
        f.write(text)
    shutil.copyfile(os.path.join(HERE, "ANSWER_FORMAT.md"), os.path.join(top, "ANSWER_FORMAT.md"))
    scenario_v2.set_dates(top)
    return top


def main(argv):
    if len(argv) < 2 or argv[1].startswith("-"):
        print(__doc__)
        return 2
    wid = argv[1]
    harness = option(argv, "--harness", "own")
    if harness not in ("own", "cc"):
        world.die("--harness must be own or cc. Nothing was changed.")
    model = option(argv, "--model", agent.DEFAULT_MODEL if harness == "own" else agent_cc.DEFAULT_MODEL)
    cases = [c for c in option(argv, "--cases", "A,B,N").split(",") if c in ("A", "B", "N")]
    turns = int(option(argv, "--max-turns", agent.MAX_TURNS))
    tag = option(argv, "--tag", "")
    if tag:                                             # with a tag, cases that already have an answer under it are not run again
        done = set()
        if os.path.isfile(RESULTS):
            with open(RESULTS, encoding="utf-8") as f:
                for line in f:
                    try:
                        old = json.loads(line)
                    except ValueError:
                        continue
                    if old.get("tag") == tag and old.get("world") == wid and old["run"].get("stopped") != "api_error":
                        done.add(old.get("case"))
        skipped = [c for c in cases if c in done]
        cases = [c for c in cases if c not in done]
        if skipped:
            print("tag %s: already done for %s: %s" % (tag, wid, ", ".join(skipped)))
        if not cases:
            print("nothing left to run for %s under tag %s. Nothing was changed." % (wid, tag))
            return 0
    version = option(argv, "--agent", "v0")
    if version not in ("v0", "v1"):
        world.die("--agent must be v0 or v1. Nothing was changed.")
    if harness == "own" and not os.environ.get("OPENROUTER_API_KEY"):
        world.die("OPENROUTER_API_KEY is not set. Nothing was changed.")
    need = PER_CASE_USD * len(cases)
    try:
        left, limit = agent.key_remaining() if harness == "own" else (None, None)
    except RuntimeError as err:
        print("note: the key's spending limit could not be checked (%s). Going on." % err)
    else:
        if harness == "cc":
            print("harness cc: Claude Code on the subscription, model %s. No API key is used." % model)
        elif left is None:
            print("key: no spending limit of its own (the account balance is not checked here)")
        elif left < need:
            world.die("the API key may still spend $%.2f of its $%.2f limit, and %d case(s) need about $%.2f. "
                      "Raise the key's credit limit on openrouter.ai (Keys), or run fewer cases. Nothing was changed."
                      % (left, limit or 0, len(cases), need))
        else:
            print("key: $%.2f left of its limit, about $%.2f needed for %d case(s)" % (left, need, len(cases)))
    label_file = os.path.join(LABELS, wid + ".json")
    if not os.path.isfile(label_file):
        world.die("no labels for %s in %s. Nothing was changed." % (wid, LABELS))
    with open(label_file, encoding="utf-8") as f:
        labels = json.load(f)
    with open(world.CATALOGUE, encoding="utf-8") as f:
        entries = [e for e in json.load(f) if e.get("status") == "MEASURED" and e.get("labels")]
    entry = next((e for e in entries if e["id"] == wid), None)
    if entry is None:
        world.die("%s is not a measured world with labels. Nothing was changed." % wid)
    with open(os.path.join(HERE, "PROMPT_LIVE.md" if harness == "own" else "PROMPT_CC.md"), encoding="utf-8") as f:
        prompt = f.read()
    run_dir = os.path.join(HERE, "runs", time.strftime("%Y%m%d-%H%M%S") + "-" + wid)
    os.makedirs(run_dir)
    os.makedirs(os.path.dirname(RESULTS), exist_ok=True)

    setup = {name: file_hash(os.path.join(HERE, name)) for name in
             ("PROMPT_CC.md" if harness == "cc" else "PROMPT_LIVE.md", "agent_cc.py" if harness == "cc" else "agent.py",
              "ANSWER_FORMAT.md", "grade.py", "dbtool.py")}
    setup["labels"] = file_hash(label_file)
    failed_api = False
    print("=== applying %s" % wid)
    world_live.apply(wid)
    try:
        for case in cases:
            if harness == "cc":            # outside the project, so that nothing about the answers is near the agent
                work = os.path.join(os.path.expanduser("~"), ".intent-runs", os.path.basename(run_dir))
                os.makedirs(work, exist_ok=True)
                folder = build_folder(work, entry, entries, case)
            else:
                folder = build_folder(run_dir, entry, entries, case)
            print("=== %s case %s   harness %s   agent %s   model %s   folder %s" % (wid, case, harness, version, model, os.path.basename(folder)))
            if harness == "cc":
                kept = os.path.join(run_dir, os.path.basename(folder))
                summary = agent_cc.run(folder, prompt, model, turns, log=kept + ".log.jsonl", version=version)
                os.makedirs(kept)
                for name in ("answer.json", "TICKET.md", "DECISIONS.md"):
                    if os.path.isfile(os.path.join(folder, name)):
                        shutil.copyfile(os.path.join(folder, name), os.path.join(kept, name))
                shutil.rmtree(folder, ignore_errors=True)
                folder = kept
            else:
                summary = agent.run(folder, prompt, model, turns, log=folder + ".log.jsonl", version=version)
            answer_path = os.path.join(folder, "answer.json")
            try:
                with open(answer_path, encoding="utf-8") as f:
                    answer = json.load(f)
            except (OSError, ValueError):
                answer = "no answer.json"
            marks = grade.grade(answer, labels, case)
            line = {"when": time.strftime("%Y-%m-%d %H:%M:%S"), "tag": tag, "setup": setup, "world": wid, "case": case, "folder": folder, "run": summary, "grade": marks}
            with open(RESULTS, "a", encoding="utf-8") as f:
                f.write(json.dumps(line, ensure_ascii=False) + "\n")
            print("    stopped: %s   turns: %d   tokens in/out: %d/%d   cost: %s" % (
                summary["stopped"], summary["turns"], summary["input_tokens"], summary["output_tokens"],
                "$%.4f" % summary["cost_usd"] if summary["cost_usd"] is not None else "not reported"))
            if summary.get("outside_folder") or summary.get("other_commands"):
                print("    WARNING, check this run by hand. Outside the folder: %s   Other commands: %s" % (
                    summary.get("outside_folder") or "none", summary.get("other_commands") or "none"))
            if summary.get("harness") == "cc":
                print("    follow-ups: %d   asked the v1 question: %s   denied tool calls: %d   cost if it were the API: %s" % (
                    summary["follow_ups"], summary["challenged"], summary["denials"],
                    "$%.4f" % summary["notional_cost_usd"] if summary["notional_cost_usd"] is not None else "not reported"))
            if summary["error"]:
                print("    error: %s" % summary["error"])
            print("    verdict %s   r_loc %s  r_verdict %s  r_fix %s  forbidden %s  schema_valid %s" % (
                answer.get("verdict") if isinstance(answer, dict) else "-", marks["r_loc"], marks["r_verdict"], marks["r_fix"],
                marks["forbidden"], marks["schema_valid"]))
            for k, why in marks["reasons"].items():
                print("      %s: %s" % (k, why))
            if summary["stopped"] == "api_error":
                print("    the API failed; stopping the remaining cases")
                failed_api = True
                break
    finally:
        if "--keep-live" in argv:
            print("=== %s is still LIVE (--keep-live). Undo it with: python3 agent/world_live.py revert %s" % (wid, wid))
        else:
            print("=== reverting %s" % wid)
            world_live.revert(wid)
    print("results appended to %s\nfolders and logs of this run: %s" % (RESULTS, run_dir))
    return 3 if failed_api else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
