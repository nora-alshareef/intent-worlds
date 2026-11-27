#!/usr/bin/env python3
"""Run the agent on worlds while they are live, grade its answers, and undo each world.

  run.py <world-id ...|all> [--runs N] [--cases A,B,N] [--tag NAME] [--model NAME] [--max-turns N]

The model and the key come from .env (see .env.example). --model overrides the model for one series.

  all        every measured world of worlds.json, controls included
  --runs N   how many times each scenario is run (default 1). One run of a model is not a
             result; three give a range.
  --tag      written into every result line. A scenario that already has an answer under the
             same tag and run number is skipped, so a stopped series can be continued.

For each world, in this order:
  1. apply it (world.py: baseline probes, the change, probes, logs and traces saved)
  2. for each run and each case, build one incident folder OUTSIDE the project
     (~/.intent-runs/), run the agent in it, grade answer.json, and delete the folder.
     The answer, the ticket, the decisions and the full trajectory are kept in results/runs/.
  3. append one line per scenario to results/results.jsonl
  4. revert the world, also when something above failed

A control world has one case, C. The series stops when the model cannot be reached (exit
code 3) or when a revert leaves the system different from before (exit code 4), so that
nothing is run on a damaged system. The agent runs while the fault is live, because it may
query the data stores. Worlds that change data should be run while someone is watching.
"""
import hashlib
import json
import os
import shutil
import sys
import time

import agent
import grade
import scenario
import world as W

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(ROOT, "results", "results.jsonl")
KEPT = os.path.join(ROOT, "results", "runs")
OUTSIDE = os.path.join(os.path.expanduser("~"), ".intent-runs")
SETUP_FILES = ("agent.py", "grade.py", "store.py", "scenario.py", "world.py", "worlds.json", "decisions.json")


def setup_hashes():
    """A short hash of every file that shapes a result. Lines with different hashes are not comparable."""
    out = {"input_version": agent.INPUT_VERSION}
    for name in SETUP_FILES:
        with open(os.path.join(ROOT, name), "rb") as f:
            out[name] = hashlib.sha256(f.read()).hexdigest()[:12]
    return out


def already_done(tag):
    """(world, case, run number) of every scenario that has an answer under this tag."""
    done = set()
    if tag and os.path.isfile(RESULTS):
        with open(RESULTS, encoding="utf-8") as f:
            for line in f:
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                if x.get("tag") == tag and x["agent"].get("stopped") != "api_error":
                    done.add((x["world"], x["case"], x["run"]))
    return done


def run_scenario(world, case, run_no, worlds, opts, times, setup):
    """Build, run, grade and record one scenario. Returns the agent's summary."""
    stamp = "%s-%s-%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), world["id"], case, run_no)
    work = os.path.join(OUTSIDE, stamp)
    os.makedirs(work)
    folder, key = scenario.build(world, case, work, worlds, began=times[0], ended=times[1])
    kept = os.path.join(KEPT, stamp)
    os.makedirs(kept)
    store_log = os.path.join(kept, "store_requests.jsonl")
    print("=== %s case %s run %d   model %s" % (world["id"], case, run_no, opts["model"]))
    summary = agent.run(folder, opts["model"], opts["turns"], log=os.path.join(kept, "trajectory.jsonl"), store_log=store_log)
    answer = agent.read_answer(folder)
    marks = grade.grade(answer, key, folder, store_log)   # before the folder is deleted: the quote is searched in it
    for name in ("answer.json", "TICKET.md", "DECISIONS.md"):
        if os.path.isfile(os.path.join(folder, name)):
            shutil.copyfile(os.path.join(folder, name), os.path.join(kept, name))
    shutil.rmtree(work, ignore_errors=True)
    with open(RESULTS, "a", encoding="utf-8") as f:
        f.write(json.dumps({"when": time.strftime("%Y-%m-%d %H:%M:%S"), "tag": opts["tag"], "setup": setup, "world": world["id"],
                            "case": case, "run": run_no, "kept": kept, "key": key, "answer": answer, "agent": summary,
                            "grade": marks}, ensure_ascii=False) + "\n")
    print("    stopped: %s   turns: %d   follow-ups: %d" % (summary["stopped"], summary["turns"], summary["follow_ups"]))
    if summary["outside_folder"] or summary["other_commands"]:
        print("    WARNING, check this run by hand. Outside the folder: %s   Other commands: %s" % (
            summary["outside_folder"] or "none", summary["other_commands"] or "none"))
    if summary["error"]:
        print("    error: %s" % summary["error"])
    if not marks["valid"]:
        print("    no valid answer: %s" % "; ".join(marks["problems"][:3]))
    elif case == "C":
        print("    said %s (right: no_fault)   control: %s" % (marks["said"], marks["control"]))
    else:
        print("    said %s (right: %s)   place %s   quote %s   root_cause %s  verdict %s  decision_ref %s   danger: %s   score %d of %d" % (
            marks["said"], grade.RIGHT[case], marks["place_ok"], marks["evidence_in"] or "not found", marks["root_cause"],
            marks["verdict"], marks["decision_ref"], marks["danger"] or "none", marks["score"], marks["max"]))
    return summary


def run_world(world, worlds, opts, done, setup):
    """One world: apply, every wanted scenario, revert. Returns 0, 3 (model not reachable) or 4 (not clean)."""
    cases = ["C"] if world["kind"] == "control" else opts["cases"]
    todo = [(n, c) for n in range(1, opts["runs"] + 1) for c in cases if (world["id"], c, n) not in done]
    if not todo:
        print("%s: nothing left to run under tag %s" % (world["id"], opts["tag"]))
        return 0
    began = time.time()
    measured = W.apply(world)
    code = 0
    try:
        times = (began, time.time())
        for run_no, case in todo:
            if run_scenario(world, case, run_no, worlds, opts, times, setup)["stopped"] == "api_error":
                print("    the model could not be reached; stopping")
                code = 3
                break
    finally:
        left = W.revert(world)
    if left != measured["before"]:
        print("STOP: after reverting %s the probes differ from before (%s, was %s). Check the system by hand." % (
            world["id"], ", ".join(left) or "none", ", ".join(measured["before"]) or "none"))
        return 4
    return code


def main(argv):
    if len(argv) < 2 or argv[1].startswith("-"):
        print(__doc__)
        return 2

    def option(name, default):
        return argv[argv.index(name) + 1] if name in argv else default
    ids = []
    for a in argv[1:]:
        if a.startswith("--"):
            break
        ids.append(a)
    opts = {"runs": int(option("--runs", 1)), "tag": option("--tag", ""), "model": option("--model", agent.settings()["model"]),
            "turns": int(option("--max-turns", agent.MAX_TURNS)),
            "cases": [c for c in option("--cases", "A,B,N").split(",") if c in ("A", "B", "N")]}
    worlds = W.load_worlds()
    measured = [w for w in worlds if w["status"] == "measured"]
    chosen = measured if ids == ["all"] else [w for w in measured if w["id"] in ids]
    missing = sorted(set(ids) - {"all"} - {w["id"] for w in chosen})
    if missing or not chosen:
        W.die("not a measured world in worlds.json: %s. Nothing was changed." % (", ".join(missing) or "(none given)"))
    os.makedirs(KEPT, exist_ok=True)
    print("model %s, paid by: %s" % (opts["model"], {"api_key": "the key in .env", "subscription": "the Claude login of this machine (no key in .env)"}[agent.settings()["auth"]]))
    done, setup = already_done(opts["tag"]), setup_hashes()
    for world in chosen:
        code = run_world(world, worlds, opts, done, setup)
        if code:
            return code
    print("results appended to %s\nanswers and trajectories: %s\ntable: python3 report.py %s" % (RESULTS, KEPT, opts["tag"] or "<tag>"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
