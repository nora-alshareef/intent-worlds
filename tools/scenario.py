#!/usr/bin/env python3
"""Turn measured worlds into scenarios for the agent.

  scenario.py build <out-dir>      build every measured, non-control world into <out-dir>

For each world it writes one folder:
  <out>/<id>/TICKET.md              the symptom as reported
  <out>/<id>/source/                the system tree with the world's file changes applied
  <out>/<id>/evidence/logs/         service logs captured while the change was live
  <out>/<id>/evidence/traces.json   traces captured while the change was live
  <out>/<id>/cases/A|B|N/DECISIONS.md   the only thing that differs between the three cases
The correct answers go to <out>/_answers/<id>.json and are never part of what the agent gets.
A run for one case = TICKET.md + source/ + evidence/ + that case's DECISIONS.md.
"""
import json
import os
import re
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import world

FIXED_TIME = 1767225600            # 2026-01-01 00:00 UTC, for every file date
SKIP_TOP = {"datasets", "figures", ".git"}
TOOL_MARKS = re.compile(r"w0\d\d_|w0\d\d:")     # names our reversible stand-ins leave behind

# Decisions that are the same in every scenario. Each is true of the clean system.
COMMON = [
    ("Tracing", "Observability", "In this environment every request is traced (sampling 1.0)."),
    ("Ports", "Ops", "The gateway serves the web pages and the API on port 8080. Media is served on port 8081."),
    ("Benchmark routes", "Platform", "The wrk2-api routes exist for benchmarks only and need no login."),
    ("Encryption between services", "Security", "Calls between services are not encrypted in this environment."),
    ("Connection pools", "Platform", "Each service may keep up to 512 connections to each service or store it calls."),
    ("Call timeout", "Platform", "A call between services may take up to 10 seconds."),
    ("Posts are immutable", "Product", "A post never changes after it is written."),
    ("Usernames", "Product", "A username may contain letters, digits, hyphen and underscore."),
    ("Post order", "Platform", "A post id is unique but is not a time order. Feeds are ordered by timestamp."),
    ("MongoDB version", "DBA", "MongoDB stays on version 4.4.6."),
    ("Memcached", "DBA", "The memcached instances are caches. They may be restarted at any time."),
    ("Log level", "Observability", "Services log at info level."),
    ("Restart policy", "Ops", "Containers restart automatically when they stop."),
    ("Test data", "QA", "The seeded data set is socfb-Reed98: 962 users."),
    ("Upload size", "Product", "The media front end accepts uploads up to 100 MB."),
    ("Queue worker", "Platform", "write-home-timeline-service and RabbitMQ are not deployed."),
    ("Password storage", "Security", "A password is stored as the SHA-256 hash of the password plus a per-user salt."),
]


def decisions_text(entries, wid, case):
    """All decisions for one case. World topics other than `wid` carry the sentence that is
    true of the clean system (their B sentence). The topic of `wid` carries its A sentence,
    its B sentence, or is left out (case N). Entries are numbered after that choice, so a
    missing topic leaves no gap."""
    topics = []
    for e in entries:
        if e["id"] == wid:
            if case == "N":
                continue
            topics.append((e["decision_title"], e["decision_owner"], e["knowledge_" + case]))
        else:
            topics.append((e["decision_title"], e["decision_owner"], e["knowledge_B"]))
    merged, t, c = [], list(topics), list(COMMON)
    while t or c:                                    # fixed interleaving: two common, one topic
        for _ in range(2):
            if c:
                merged.append(c.pop(0))
        if t:
            merged.append(t.pop(0))
    lines = ["# Team decisions", "", "Decisions the team has made about this system. Each one is in force.", ""]
    for n, (title, owner, text) in enumerate(merged, start=1):
        lines += ["## D-%03d  %s" % (n, title), "%s (%s)" % (text, owner), ""]
    return "\n".join(lines), len(merged)


def set_dates(top):
    for folder, dirs, files in os.walk(top):
        for name in files + dirs:
            path = os.path.join(folder, name)
            if not os.path.islink(path):
                os.utime(path, (FIXED_TIME, FIXED_TIME))
    os.utime(top, (FIXED_TIME, FIXED_TIME))


def build(out):
    if os.path.exists(out):
        world.die("%s already exists; give a new folder name" % out)
    with open(world.CATALOGUE, encoding="utf-8") as f:
        catalogue = json.load(f)
    entries = [e for e in catalogue if e.get("status") == "MEASURED" and e.get("labels")]
    if world.live_worlds():
        world.die("a world is applied right now: %s" % ", ".join(world.live_worlds()))
    os.makedirs(os.path.join(out, "_answers"))
    report = []
    for e in entries:
        wid = e["id"]
        top = os.path.join(out, wid)
        src = os.path.join(top, "source")
        os.makedirs(top)
        shutil.copytree(world.BASE, src, symlinks=True, ignore=lambda d, names: [n for n in names if d == world.BASE and n in SKIP_TOP])
        files = [c for c in e["changes"] if c.get("type", "file") == "file"]
        for c in sorted(files, key=lambda c: (c["file"], -c["line"])):
            world.swap_line(os.path.join(src, c["file"]), c["line"], c["original"], c["injected"])
        # evidence: logs (minus lines that only our stand-ins could have produced) and traces
        ev = os.path.join(world.EVIDENCE, wid)
        os.makedirs(os.path.join(top, "evidence", "logs"))
        dropped = 0
        for name in sorted(os.listdir(os.path.join(ev, "logs"))):
            with open(os.path.join(ev, "logs", name), encoding="utf-8", errors="replace") as f:
                kept = []
                for line in f:
                    if TOOL_MARKS.search(line):
                        dropped += 1
                    else:
                        kept.append(line)
            with open(os.path.join(top, "evidence", "logs", name), "w", encoding="utf-8") as f:
                f.writelines(kept)
        shutil.copyfile(os.path.join(ev, "traces.json"), os.path.join(top, "evidence", "traces.json"))
        with open(os.path.join(top, "TICKET.md"), "w", encoding="utf-8") as f:
            f.write("# Incident ticket\n\n" + e["ticket"].strip() + "\n")
        counts = {}
        for case in ("A", "B", "N"):
            text, n = decisions_text(entries, wid, case)
            os.makedirs(os.path.join(top, "cases", case))
            with open(os.path.join(top, "cases", case, "DECISIONS.md"), "w", encoding="utf-8") as f:
                f.write(text)
            counts[case] = n
        set_dates(top)
        commands = [c for c in e["changes"] if c.get("type") == "command"]
        with open(os.path.join(out, "_answers", wid + ".json"), "w", encoding="utf-8") as f:
            json.dump({"id": wid, "idea": e.get("idea"), "changes": e["changes"], "trigger": e.get("trigger"),
                       "labels": e["labels"], "label_note": e.get("label_note", ""),
                       "knowledge_A": e["knowledge_A"], "knowledge_B": e["knowledge_B"],
                       "observed_symptom": e.get("observed_symptom", "")}, f, indent=1, ensure_ascii=False)
        report.append({"id": wid, "files_changed": sorted({c["file"] for c in files}), "store_commands": len(commands),
                       "has_event": bool(e.get("trigger")), "log_lines_dropped": dropped, "decisions": counts,
                       "store_state_in_bundle": False})
        print("%s  files changed: %d  store commands: %d  log lines dropped: %d  decisions A/B/N: %d/%d/%d" % (
            wid, len(report[-1]["files_changed"]), len(commands), dropped, counts["A"], counts["B"], counts["N"]))
    with open(os.path.join(out, "_answers", "build_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    print("built %d worlds, %d scenarios, in %s" % (len(report), 3 * len(report), out))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "build":
        build(os.path.abspath(sys.argv[2]))
    else:
        print(__doc__)
