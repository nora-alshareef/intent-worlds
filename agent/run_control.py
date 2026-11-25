#!/usr/bin/env python3
"""Run the agent on a CONTROL world: something harmless happened and nothing is wrong.
The right answer is the verdict no_fault. Any other verdict is a false alarm (r_fa = 1).

  run_control.py <W-000|W-010C> [--harness own|cc] [--model ID] [--max-turns N] [--tag NAME]

Same steps as run_live.py: apply (the harmless event), build one incident folder, run, grade, revert.
The folder looks like any other incident: TICKET.md, source/, evidence/, DECISIONS.md, ANSWER_FORMAT.md.
DECISIONS.md has the same 27 entries as everywhere, each with the sentence that describes the system as it is.
The tickets are in controls.json. There is one case per control, called C.
NOT TESTED with Docker or a model.
"""
import json
import os
import random
import shutil
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import run_live                                   # also puts tools/ on the path
import agent
import agent_cc
import grade
import scenario_v2
import world
import world_live


def control_decisions(entries, wid, case):
    """All 27 entries, in an order of this control's own, none of them changed."""
    slots = [("common", c) for c in scenario_v2.COMMON] + [("topic", e) for e in entries]
    random.Random(wid).shuffle(slots)
    lines = ["# Team decisions", "", "Decisions the team has made about this system. Each one is in force.", ""]
    for n, (kind, item) in enumerate(slots, start=1):
        if kind == "common":
            title, owner, text = item
        else:
            title, owner, text = item["decision_title"], item["decision_owner"], scenario_v2.SENTENCES[item["id"]]["B"]
        lines += ["## D-%03d  %s" % (n, title), "%s (%s)" % (text, owner), ""]
    return "\n".join(lines), len(slots), None


def ticket_text(template, began, ended):
    """Fill the clock times of this run into the ticket. The harmless event happened between `began` and `ended`.
    {from} and {to} are full hours around it, {at} is the minute it ended. All in UTC, as in the service logs."""
    start = time.gmtime(began - began % 3600)
    stop = time.gmtime(ended - ended % 3600 + 3600)
    return template.format(**{"from": time.strftime("%H:%M", start), "to": time.strftime("%H:%M", stop),
                              "at": time.strftime("%H:%M", time.gmtime(ended))})


def main(argv):
    if len(argv) < 2 or argv[1].startswith("-"):
        print(__doc__)
        return 2
    wid = argv[1]
    with open(os.path.join(HERE, "controls.json"), encoding="utf-8") as f:
        controls = json.load(f)
    if wid not in controls or wid.startswith("_"):
        world.die("%s is not a control world in controls.json. Nothing was changed." % wid)
    harness = run_live.option(argv, "--harness", "own")
    if harness not in ("own", "cc"):
        world.die("--harness must be own or cc. Nothing was changed.")
    if harness == "own" and not os.environ.get("OPENROUTER_API_KEY"):
        world.die("OPENROUTER_API_KEY is not set. Nothing was changed.")
    model = run_live.option(argv, "--model", agent.DEFAULT_MODEL if harness == "own" else agent_cc.DEFAULT_MODEL)
    turns = int(run_live.option(argv, "--max-turns", agent.MAX_TURNS))
    tag = run_live.option(argv, "--tag", "")
    with open(world.CATALOGUE, encoding="utf-8") as f:
        catalogue = json.load(f)
    entries = [e for e in catalogue if e.get("status") == "MEASURED" and e.get("labels")]
    entry = dict(next(e for e in catalogue if e["id"] == wid))
    labels = {"id": wid, "grading": {"cases": {"C": {"verdict": "no_fault", "fix": None, "location": None}}}}
    prompt_name = "PROMPT_LIVE.md" if harness == "own" else "PROMPT_CC.md"
    with open(os.path.join(HERE, prompt_name), encoding="utf-8") as f:
        prompt = f.read()
    setup = {name: run_live.file_hash(os.path.join(HERE, name)) for name in
             (prompt_name, "agent_cc.py" if harness == "cc" else "agent.py", "ANSWER_FORMAT.md", "grade.py", "dbtool.py", "controls.json")}
    run_dir = os.path.join(HERE, "runs", time.strftime("%Y%m%d-%H%M%S") + "-" + wid)
    os.makedirs(run_dir)
    os.makedirs(os.path.dirname(run_live.RESULTS), exist_ok=True)
    failed_api = False

    print("=== applying control %s (a harmless event; nothing is broken)" % wid)
    began = time.time()
    world_live.apply(wid)
    try:
        entry["ticket"] = ticket_text(controls[wid]["ticket"], began, time.time())
        real = scenario_v2.decisions_text
        scenario_v2.decisions_text = control_decisions
        try:
            work = os.path.join(os.path.expanduser("~"), ".intent-runs", os.path.basename(run_dir)) if harness == "cc" else run_dir
            os.makedirs(work, exist_ok=True)
            folder = run_live.build_folder(work, entry, entries, "C")
        finally:
            scenario_v2.decisions_text = real
        print("=== %s case C   harness %s   agent v0   model %s   folder %s" % (wid, harness, model, os.path.basename(folder)))
        if harness == "cc":
            kept = os.path.join(run_dir, os.path.basename(folder))
            summary = agent_cc.run(folder, prompt, model, turns, log=kept + ".log.jsonl", version="v0")
            os.makedirs(kept)
            for name in ("answer.json", "TICKET.md", "DECISIONS.md"):
                if os.path.isfile(os.path.join(folder, name)):
                    shutil.copyfile(os.path.join(folder, name), os.path.join(kept, name))
            shutil.rmtree(folder, ignore_errors=True)
            folder = kept
        else:
            summary = agent.run(folder, prompt, model, turns, log=folder + ".log.jsonl", version="v0")
        try:
            with open(os.path.join(folder, "answer.json"), encoding="utf-8") as f:
                answer = json.load(f)
        except (OSError, ValueError):
            answer = "no answer.json"
        marks = grade.grade(answer, labels, "C")
        # Decision of 2026-11-22 (Norah): on a control, "cannot_decide" is not an accusation. no_fault is right,
        # cannot_decide is neutral (not right, not a false alarm), intended or mistake is the false alarm.
        # It is applied here and not in grade.py, so that grade.py stays as it was for the whole baseline series.
        said = answer.get("verdict") if isinstance(answer, dict) and marks["schema_valid"] else None
        marks["control_answer"] = {"no_fault": "right", "cannot_decide": "neutral", "intended": "false_alarm", "mistake": "false_alarm"}.get(said, "no_answer")
        if said == "cannot_decide":
            marks["r_fa"] = 0
            marks["reasons"]["r_fa"] = "cannot_decide on a control: neutral, not counted as a false alarm"
            marks["reasons"]["r_loc"] = marks["reasons"]["r_fix"] = "not no_fault, and nothing is blamed"
        with open(run_live.RESULTS, "a", encoding="utf-8") as f:
            f.write(json.dumps({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "tag": tag, "setup": setup, "world": wid, "case": "C",
                                "folder": folder, "run": summary, "grade": marks}, ensure_ascii=False) + "\n")
        print("    stopped: %s   turns: %d" % (summary["stopped"], summary["turns"]))
        if summary.get("outside_folder") or summary.get("other_commands"):
            print("    WARNING, check this run by hand. Outside the folder: %s   Other commands: %s" % (
                summary.get("outside_folder") or "none", summary.get("other_commands") or "none"))
        if summary["error"]:
            print("    error: %s" % summary["error"])
            failed_api = summary["stopped"] == "api_error"
        print("    verdict %s (the label is no_fault)   false alarm: %s   schema_valid %s" % (
            answer.get("verdict") if isinstance(answer, dict) else "-", {1: "YES", 0: "no"}.get(marks["r_fa"]), marks["schema_valid"]))
    finally:
        print("=== reverting %s" % wid)
        world_live.revert(wid)
    print("results appended to %s\nfolders and logs of this run: %s" % (run_live.RESULTS, run_dir))
    return 3 if failed_api else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
