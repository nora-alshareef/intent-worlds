#!/usr/bin/env python3
"""Read agent/results/results.jsonl and print the table for one tag.

  summarize.py <tag>

One line per (world, case): the last run under the tag that was not an API failure. Then the totals per case.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def main(argv):
    if len(argv) != 2:
        print(__doc__)
        return 2
    rows, setups = {}, set()
    with open(os.path.join(HERE, "results", "results.jsonl"), encoding="utf-8") as f:
        for line in f:
            try:
                x = json.loads(line)
            except ValueError:
                continue
            if x.get("tag") == argv[1] and x["run"].get("stopped") != "api_error":
                rows[(x["world"], x["case"])] = x
                # the label file is one per world, so its hash is left out when the setups are compared
                setups.add(json.dumps({k: v for k, v in (x.get("setup") or {}).items() if k != "labels"}, sort_keys=True))
    if not rows:
        print("no results under tag %s" % argv[1])
        return 1
    print("tag %s: %d cases so far (30 fault cases and 2 controls make a full series).  %s" % (argv[1], len(rows), "One setup for all of them." if len(setups) == 1 else
                                          "WARNING: %d different setups under this tag; the lines are not comparable." % len(setups)))
    print("%-7s %-4s %-14s %-18s %-3s %-3s %-3s %-9s %-5s %s" % ("world", "case", "label", "answer", "loc", "ver", "fix", "forbidden", "turns", "note"))
    total = {}
    for (w, c), x in sorted(rows.items()):
        g, run = x["grade"], x["run"]
        label = {"A": "intended", "B": "mistake", "N": "cannot_decide", "C": "no_fault"}[c]
        why = (g.get("reasons") or {}).get("r_verdict", "")
        said = why.split("verdict is ")[1].split(",")[0] if "verdict is " in why else (label if g["r_verdict"] == 1 else "no answer")
        note = ("CHECK BY HAND " if run.get("outside_folder") or run.get("other_commands") else "") + ("" if run["stopped"] == "finished" else run["stopped"])
        print("%-7s %-4s %-14s %-18s %-3s %-3s %-3s %-9s %-5s %s" % (w, c, label, said, g["r_loc"], g["r_verdict"], g["r_fix"], "YES" if g["forbidden"] else "-", run["turns"], note))
        t = total.setdefault(c, {"n": 0, "loc": 0, "ver": 0, "fix": 0, "forbidden": 0, "no_answer": 0, "fa": 0})
        t["n"] += 1; t["loc"] += g["r_loc"] or 0; t["ver"] += g["r_verdict"] or 0; t["fix"] += g["r_fix"] or 0
        t["fa"] += g.get("r_fa") or 0
        t["forbidden"] += 1 if g["forbidden"] else 0; t["no_answer"] += 0 if g["schema_valid"] else 1
    print()
    for c in sorted(total):
        t = total[c]
        if c == "C":
            print("case C (controls, nothing is wrong): no_fault said %d of %d   false alarms %d   cannot_decide (neutral) %d   no valid answer %d" % (
                t["ver"], t["n"], t["fa"], t["n"] - t["ver"] - t["fa"] - t["no_answer"], t["no_answer"]))
            continue
        print("case %s: verdict right %d of %d   location right %d   fix right %d   forbidden %d   no valid answer %d" % (
            c, t["ver"], t["n"], t["loc"], t["fix"], t["forbidden"], t["no_answer"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
