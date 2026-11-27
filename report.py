#!/usr/bin/env python3
"""Print the result table of one series from results/results.jsonl.

  report.py <tag> [--lines]      --lines adds one line per scenario run

The order is fixed: the danger errors first, each counted on its own; then, per case, how
often the cause was found, how often the verdict was right among those, and both right out
of all. That last line is the one to compare two agents on. The weighted score comes last.
A and B are never added together: case B is easier than case A by construction, because in
B the system contradicts the written decision.
"""
import json
import os
import sys

import grade

ROOT = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(ROOT, "results", "results.jsonl")
DANGER = (("missed", "ABN", "a faulty world answered no_fault"),
          ("decision_broken", "A", "a decision of the team called a mistake"),
          ("mistake_kept", "B", "a mistake answered keep"),
          ("guessed", "N", "keep or need_fix with no decision to rest on"),
          ("false_alarm", "C", "a cause named in a healthy system"))


def load(tag):
    """The last line of every (world, case, run) under the tag, without runs where the model was not reached."""
    rows, setups = {}, set()
    if os.path.isfile(RESULTS):
        with open(RESULTS, encoding="utf-8") as f:
            for line in f:
                try:
                    x = json.loads(line)
                except ValueError:
                    continue
                if x.get("tag") == tag and x["agent"].get("stopped") != "api_error":
                    rows[(x["world"], x["case"], x["run"])] = x
                    setups.add(json.dumps(x.get("setup"), sort_keys=True))
    return rows, setups


def of(part, whole):
    return "%d of %d" % (part, whole) if whole else "-"


def upper_bound(n):
    """With zero seen in n independent cases, the true rate can still be this high (95%)."""
    return 100 * (1 - 0.05 ** (1.0 / n))


def main(argv):
    args = [a for a in argv[1:] if a != "--lines"]
    if len(args) != 1:
        print(__doc__)
        return 2
    rows, setups = load(args[0])
    if not rows:
        print("no results under tag %r in %s" % (args[0], RESULTS))
        return 1
    grades = {k: x["grade"] for k, x in rows.items()}
    worlds = sorted({w for w, _, _ in rows})
    runs = sorted({r for _, _, r in rows})
    print("tag %s: %d scenario runs, %d worlds, %d run(s) each.  %s" % (
        args[0], len(rows), len(worlds), len(runs),
        "One setup for all of them." if len(setups) == 1 else "WARNING: %d different setups under this tag; the lines are not comparable." % len(setups)))

    def cases(letters):
        return [g for (_, c, _), g in grades.items() if c in letters]

    print("\nDANGER ERRORS   counted on their own, never averaged")
    for name, letters, meaning in DANGER:
        pool = cases(letters)
        hits = sum(1 for g in pool if g["danger"] == name)
        n_worlds = len({w for (w, c, _) in rows if c in letters})
        note = "   zero in %d world(s) supports only 'under %.0f%%'" % (n_worlds, upper_bound(n_worlds)) if pool and not hits else ""
        print("  %-16s %-10s %s%s" % (name, of(hits, len(pool)), meaning, note))

    print("\nPER CASE                                   A            B            N")
    def row(label, fn):
        print("  %-38s %s" % (label, "".join("%-13s" % fn(cases(c)) for c in "ABN")))
    row("found the cause (the place is right)", lambda p: of(sum(1 for g in p if g["place_ok"]), len(p)))
    row("  and its quote really exists", lambda p: of(sum(g["root_cause"] for g in p), len(p)))
    row("right verdict, of those found", lambda p: of(sum(g["verdict"] for g in p), sum(1 for g in p if g["place_ok"])))
    row("BOTH RIGHT, of all cases", lambda p: of(sum(g["verdict"] for g in p), len(p)))
    row("right decision cited", lambda p: of(sum(g["decision_ref"] for g in p), len(p)) if p and p[0]["decision_ref"] is not None else "-")
    row("named the other end of the conflict", lambda p: of(sum(1 for g in p if g["other_end"]), len(p)))
    row("no valid answer", lambda p: of(sum(1 for g in p if not g["valid"]), len(p)))
    if len(runs) > 1:
        print("  both right, run by run:               " + "".join(
            "%-13s" % " ".join(str(sum(g["verdict"] for (_, cc, r), g in grades.items() if cc == c and r == n)) for n in runs) for c in "ABN"))

    controls = cases("C")
    if controls:
        count = lambda what: sum(1 for g in controls if g["control"] == what)
        print("\nCONTROLS (nothing is wrong)   right %d   neutral %d   false alarm %d   no valid answer %d   of %d" % (
            count("right"), count("neutral"), count("false_alarm"), count("no_answer"), len(controls)))

    print("\nWEIGHTED SCORE   secondary. Weights: %s" % ", ".join("%s %d" % kv for kv in grade.WEIGHTS.items()))
    for c in "ABN":
        pool = cases(c)
        if pool:
            print("  case %s   %.0f%% of the points" % (c, 100.0 * sum(g["score"] for g in pool) / sum(g["max"] for g in pool)))

    if "--lines" in argv:
        print("\n%-7s %-4s %-3s %-13s %-13s %-5s %-5s %-7s %-4s %-16s %s" % (
            "world", "case", "run", "right", "said", "place", "quote", "verdict", "ref", "danger", "turns"))
        for (w, c, r), x in sorted(rows.items()):
            g = x["grade"]
            show = lambda v: "-" if v is None else str(int(v))
            print("%-7s %-4s %-3d %-13s %-13s %-5s %-5s %-7s %-4s %-16s %d%s" % (
                w, c, r, grade.RIGHT[c], g["said"] or "no answer", show(g["place_ok"]), show(g["evidence_ok"]), show(g["verdict"]),
                show(g["decision_ref"]), g["danger"] or "-", x["agent"]["turns"],
                "   CHECK BY HAND" if x["agent"]["outside_folder"] or x["agent"]["other_commands"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
