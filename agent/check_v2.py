#!/usr/bin/env python3
"""Checks on a built scenario folder. Prints one line per check and per world.

  check_v2.py <scenarios-dir>
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BASE = os.path.join(ROOT, "base", "socialNetwork")
SKIP_TOP = {"datasets", "figures", ".git"}
MARKS = re.compile(rb"W-0\d\d|w0\d\d[_:]|world\.py|scenario(_v2)?\.py|intent-worlds|grade\.py|cases/[ABN]\b|_answers")
PREFIX = re.compile(r"^(Security rule|Ops decision|Data policy|Data decision|Data model|API contract|Rule)\s*:")


def entries(path):
    out = []
    for block in open(path, encoding="utf-8").read().split("## ")[1:]:
        head, body = block.split("\n")[0], block.split("\n")[1]
        out.append((head[:5], head[5:].strip(), body))
    return out


def files_under(top):
    for folder, dirs, files in os.walk(top):
        for name in files:
            yield os.path.join(folder, name)


def source_diff(src, changes):
    """Files that differ from base, and whether undoing the catalogue's changes gives base back."""
    differ = []
    for path in files_under(src):
        rel = os.path.relpath(path, src)
        b = os.path.join(BASE, rel)
        if os.path.islink(path):
            continue
        if not os.path.exists(b) or open(path, "rb").read() != open(b, "rb").read():
            differ.append(rel)
    missing = [os.path.relpath(p, BASE) for p in files_under(BASE)
               if os.path.relpath(p, BASE).split(os.sep)[0] not in SKIP_TOP
               and not os.path.islink(p) and not os.path.exists(os.path.join(src, os.path.relpath(p, BASE)))]
    expected = sorted({c["file"] for c in changes if c.get("type", "file") == "file"})
    undone = True                      # apply the catalogue's changes to the base file ourselves and compare
    for rel in expected:
        lines = open(os.path.join(BASE, rel), encoding="utf-8", newline="").read().split("\n")
        for c in sorted((c for c in changes if c.get("type", "file") == "file" and c["file"] == rel), key=lambda c: -c["line"]):
            if lines[c["line"] - 1].rstrip("\r") != c["original"]:
                undone = False
            lines[c["line"] - 1:c["line"]] = c["injected"].split("\n")
        if "\n".join(lines) != open(os.path.join(src, rel), encoding="utf-8", newline="").read():
            undone = False
    return sorted(differ), expected, missing, undone


def main(scen):
    worlds = sorted(d for d in os.listdir(scen) if os.path.isdir(os.path.join(scen, d)) and not d.startswith("_"))
    fails, order_seen, topic_at = 0, set(), []
    for wid in worlds:
        top = os.path.join(scen, wid)
        ans = json.load(open(os.path.join(scen, "_answers", wid + ".json"), encoding="utf-8"))
        A, B, N = (entries(os.path.join(top, "cases", c, "DECISIONS.md")) for c in "ABN")
        ab = [i for i in range(len(A)) if A[i] != B[i]] if len(A) == len(B) else None
        an = [i for i in range(len(A)) if A[i] != N[i]] if len(A) == len(N) else None
        titles = lambda L: [(n, t) for n, t, _ in L]
        c1 = ab is not None and len(ab) == 1 and titles(A) == titles(B)
        c2 = len(N) == len(A) and an == ab
        c2b = an is not None and len(an) == 1 and N[an[0]][1] == "Tracing UI"
        prefixed = [e[0] for L in (A, B, N) for e in L if PREFIX.match(e[2])]
        order_seen.add(tuple(t for _, t, _ in B))
        topic_at.append(A[ab[0]][0] if ab else "?")
        times = {int(os.lstat(p).st_mtime) for p in files_under(top) if not os.path.islink(p)}
        times |= {int(os.lstat(os.path.join(f, d)).st_mtime) for f, ds, _ in os.walk(top) for d in ds}
        differ, expected, missing, undone = source_diff(os.path.join(top, "source"), ans["changes"])
        c4 = differ == expected and not missing and undone
        hits = []
        for p in files_under(top):
            if os.path.islink(p):
                continue
            m = MARKS.search(open(p, "rb").read())
            if m:
                hits.append("%s: %s" % (os.path.relpath(p, top), m.group(0).decode()))
        both = all(os.path.exists(os.path.join(top, n)) for n in ("PROMPT.md", "ANSWER_FORMAT.md"))
        ok = c1 and c2 and c2b and not prefixed and len(times) == 1 and c4 and not hits and both
        fails += not ok
        print("%s  %s" % (wid, "OK  " if ok else "FAIL"),
              "| entries A/B/N %d/%d/%d" % (len(A), len(B), len(N)),
              "| A and B differ in %s entry (%s)" % (len(ab) if ab is not None else "?", topic_at[-1]),
              "| N differs from A in the same entry only: %s, and it is the neutral one: %s" % (c2, c2b),
              "| role prefixes: %d" % len(prefixed),
              "| file dates: %d" % len(times),
              "| source differs from base in %d file(s), expected %d, missing %d, base plus the listed changes gives the scenario file: %s" % (len(differ), len(expected), len(missing), undone),
              "| prompt and format in bundle: %s" % both,
              "| tool names or world ids: %s" % (hits or "none"))
    print("orders of entries: %d different among %d worlds" % (len(order_seen), len(worlds)))
    print("place of the topic entry per world:", " ".join(topic_at))
    print("ALL CHECKS PASSED" if not fails else "%d WORLD(S) FAILED" % fails)
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(os.path.abspath(sys.argv[1])))
