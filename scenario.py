#!/usr/bin/env python3
"""Build the folder the agent gets for one scenario, and the key the grader uses.

  scenario.py decisions <world-id> <A|B|N|C>          print the DECISIONS.md of that case
  scenario.py build <world-id> <A|B|N|C> <out-dir>    build the folder without the running system:
                                                      a copy of the clean source with the world's
                                                      file changes written in, and the evidence
                                                      saved by the last `world.py apply`, if any

One world gives three scenarios. They share everything except one entry of DECISIONS.md:
  case A   the entry says the change is what the team decided       right verdict: keep
  case B   the entry says the opposite                              right verdict: need_fix
  case N   a neutral entry stands in its place                      right verdict: needs_review
A control world has one scenario, case C: nothing is wrong, right verdict no_fault.

The incident folder:   TICKET.md   source/   evidence/logs/   evidence/traces.json   DECISIONS.md
Nothing in it tells the case: every case has the same number of decisions, in an order that is
shuffled per world, every file carries the same date, and the folder name is random.
"""
import json
import os
import random
import re
import shutil
import sys
import time
import uuid

import world as W

ROOT = os.path.dirname(os.path.abspath(__file__))
FIXED_TIME = 1767225600                              # 2026-01-01 00:00 UTC, the date of every file
SKIP_TOP = {"datasets", "figures", ".git"}           # large and of no use to a diagnosis
TOOL_MARKS = re.compile(r"w0\d\d_|w0\d\d:")           # log lines that would name our own tooling


def shared_decisions():
    with open(os.path.join(ROOT, "decisions.json"), encoding="utf-8") as f:
        return json.load(f)


def topics(worlds):
    """The worlds that own an entry of DECISIONS.md: measured fault worlds, in catalogue order.
    Measuring one more world adds an entry to every scenario, so results from before and
    after are not comparable."""
    return [w for w in worlds if w["kind"] == "fault" and w["status"] == "measured"]


def decisions_text(worlds, wid, case):
    """DECISIONS.md for one case. Returns (text, number of entries, id of the world's own entry).
    The list and its order are the same in every case of a world; only the text of the world's
    own entry changes. Every other world's entry carries its B sentence, which describes the
    clean system. In case C every entry does."""
    shared = shared_decisions()
    slots = [("common", c) for c in shared["common"]] + [("topic", w) for w in topics(worlds)]
    random.Random(wid).shuffle(slots)
    lines = ["# Team decisions", "", "Decisions the team has made about this system. Each one is in force.", ""]
    own = ""
    for n, (kind, item) in enumerate(slots, start=1):
        if kind == "common":
            entry = item
        elif item["id"] != wid:
            entry = dict(item["decision"], text=item["decision"]["B"])
        else:
            own = "D-%03d" % n
            entry = shared["neutral"] if case == "N" else dict(item["decision"], text=item["decision"][case])
        lines += ["## D-%03d  %s" % (n, entry["title"]), "%s (%s)" % (entry["text"], entry["owner"]), ""]
    return "\n".join(lines), len(slots), own


def find_line(path, text, first=False):
    with open(path, encoding="utf-8", errors="replace") as f:
        hits = [n for n, line in enumerate(f, start=1)
                if line.strip() == text or (text.endswith("(") and line.strip().startswith(text))]
    if not hits or (len(hits) > 1 and not first):
        W.die("%s: %d lines match %r (need exactly one)" % (path, len(hits), text))
    return hits[0]


def count_lines(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def resolve(world, spec, tree):
    """One labelled place of worlds.json as the grader reads it. Line numbers are computed
    from the tree the agent gets, never typed."""
    if "store" in spec:
        return [{"where": "data_store", "store": spec["store"], "keywords": spec.get("keywords", [])}]
    where, path = W.where_of(spec["file"]), os.path.join(tree, spec["file"])
    if spec.get("changed"):                          # the lines this world's changes occupy in the file
        spans = W.changed_lines(world, spec["file"])
    elif spec.get("whole_file"):
        spans = [(1, count_lines(path))]
    else:
        lo = find_line(path, spec["from"], spec.get("first", False))
        hi = lo
        if spec.get("to_end"):
            hi = count_lines(path)
        elif "to" in spec:                           # "to" is the first line after the span when it opens the next function
            hi = find_line(path, spec["to"]) - (1 if spec.get("to_is_next_function") else 0)
        spans = [(lo, hi)]
    return [{"where": where, "file": spec["file"], "line_from": lo, "line_to": hi} for lo, hi in spans]


def make_key(world, case, tree, decision_ref):
    """What the grader needs for one scenario. The agent never sees it."""
    key = {"world": world["id"], "case": case, "decision_ref": decision_ref if case in ("A", "B") else "",
           "places": [], "other_end": []}
    if world["kind"] == "fault":
        for name in ("places", "other_end"):
            for spec in world["root_cause"][name]:
                key[name] += resolve(world, spec, tree)
    return key


def ticket_text(world, began=None, ended=None):
    """The ticket. A control's ticket names clock times of this run: {from} and {to} are full
    hours around the harmless event, {at} is the minute it ended. All in UTC, as in the logs."""
    text = world["ticket"]
    if world["kind"] == "control":
        ended = ended or time.time()
        began = began or ended
        text = text.format(**{"from": time.strftime("%H:%M", time.gmtime(began - began % 3600)),
                              "to": time.strftime("%H:%M", time.gmtime(ended - ended % 3600 + 3600)),
                              "at": time.strftime("%H:%M", time.gmtime(ended))})
    return "# Incident ticket\n\n" + text.strip() + "\n"


def set_dates(top):
    for folder, dirs, files in os.walk(top):
        for name in files + dirs:
            path = os.path.join(folder, name)
            if not os.path.islink(path):
                os.utime(path, (FIXED_TIME, FIXED_TIME))
    os.utime(top, (FIXED_TIME, FIXED_TIME))


def build(world, case, out_dir, worlds=None, write_changes=False, began=None, ended=None):
    """Build one incident folder under out_dir. Returns (folder, key).
    While the world is live the source tree already holds its changes; without the running
    system pass write_changes=True and they are written into the copy."""
    if (world["kind"] == "control") != (case == "C"):
        W.die("%s is a %s world: its case is %s" % (world["id"], world["kind"], "C" if world["kind"] == "control" else "A, B or N"))
    worlds = worlds or W.load_worlds()
    top = os.path.join(out_dir, "incident-" + uuid.uuid4().hex[:8])
    src = os.path.join(top, "source")
    os.makedirs(top)
    shutil.copytree(W.BASE, src, symlinks=False, ignore_dangling_symlinks=True,
                    ignore=lambda d, names: [n for n in names if d == W.BASE and n in SKIP_TOP])
    if write_changes:
        W.write_file_changes(world, src)
    saved = os.path.join(W.EVIDENCE, world["id"])
    os.makedirs(os.path.join(top, "evidence", "logs"))
    if os.path.isdir(os.path.join(saved, "logs")):
        for name in sorted(os.listdir(os.path.join(saved, "logs"))):
            with open(os.path.join(saved, "logs", name), encoding="utf-8", errors="replace") as f:
                kept = [line for line in f if not TOOL_MARKS.search(line)]
            with open(os.path.join(top, "evidence", "logs", name), "w", encoding="utf-8") as f:
                f.writelines(kept)
    if os.path.isfile(os.path.join(saved, "traces.json")):
        shutil.copyfile(os.path.join(saved, "traces.json"), os.path.join(top, "evidence", "traces.json"))
    with open(os.path.join(top, "TICKET.md"), "w", encoding="utf-8") as f:
        f.write(ticket_text(world, began, ended))
    text, _, own = decisions_text(worlds, world["id"], case)
    with open(os.path.join(top, "DECISIONS.md"), "w", encoding="utf-8") as f:
        f.write(text)
    set_dates(top)
    return top, make_key(world, case, src, own)


def main(argv):
    if len(argv) == 4 and argv[1] == "decisions":
        print(decisions_text(W.load_worlds(), argv[2], argv[3])[0])
    elif len(argv) == 5 and argv[1] == "build":
        if W.applied_worlds():
            W.die("a world is applied right now (%s), so the source tree is not clean" % ", ".join(W.applied_worlds()))
        os.makedirs(argv[4], exist_ok=True)
        folder, key = build(W.get_world(argv[2]), argv[3], os.path.abspath(argv[4]), write_changes=True)
        print("folder: %s\nkey (the agent does not get this):\n%s" % (folder, json.dumps(key, indent=1)))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
