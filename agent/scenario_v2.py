#!/usr/bin/env python3
"""Second build of the scenarios. Copy of tools/scenario.py with these changes:

  1. A, B and N have the same number of decisions (N gets one neutral entry in the topic's place).
  2. The order of the entries is shuffled with a seed taken from the world id.
  3. Every decision sentence has one plain style (see SENTENCES and BEFORE_AFTER).
  4. Label line numbers are computed from the scenario tree (see ANCHORS), and the answer
     file carries a "grading" block that grade.py reads.
  5. PROMPT.md and ANSWER_FORMAT.md are put into each bundle.

  scenario_v2.py build <out-dir>
"""
import json
import os
import random
import re
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import world

FIXED_TIME = 1767225600            # 2026-01-01 00:00 UTC, for every file date
SKIP_TOP = {"datasets", "figures", ".git"}
TOOL_MARKS = re.compile(r"w0\d\d_|w0\d\d:")

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

# Case N: this entry stands where the world's topic would be. True of the clean system
# (docker-compose.yml publishes 127.0.0.1:16686:16686 for jaeger-agent), same text in every world.
NEUTRAL = ("Tracing UI", "Observability", "The Jaeger UI is served on port 16686.")

# The catalogue (experiments.json) is not edited. These sentences replace knowledge_A and
# knowledge_B when DECISIONS.md is written: same meaning, no role prefix, one plain style.
SENTENCES = {
    "W-001": {"A": "The JWT signing secret was rotated this month. The old value is no longer valid.",
              "B": "The JWT signing secret stays the same for this release."},
    "W-002": {"A": "The gateway answers only for the host names listed in its server_name line. Requests for any other host name are dropped.",
              "B": "The gateway answers on any host name or IP address. Benchmark tools reach it by IP address."},
    "W-003": {"A": "One timeline request returns at most 100 posts.",
              "B": "One timeline request may ask for any number of posts."},
    "W-004": {"A": "The url-shorten store keeps one row for each distinct long URL.",
              "B": "The url-shorten store keeps one row for every link in every post. Only the short code is unique."},
    "W-005": {"A": "A login session ends after 15 minutes without activity.",
              "B": "A login session lasts 24 hours, with or without activity."},
    "W-006": {"A": "Sign-up requests are limited per client address. The main page refreshes every 10 seconds.",
              "B": "The gateway applies no per-address request limits, because all browser traffic arrives from one address."},
    "W-007": {"A": "Support may reset a user's password directly in user-mongodb. user-mongodb is the source of truth for credentials.",
              "B": "Credentials are changed through the application only, never directly in the database."},
    "W-008": {"A": "Every Redis store in this system is a cache and may be emptied at any time.",
              "B": "home-timeline-redis holds the only copy of the home feeds. It must be moved together with its data."},
    "W-009": {"A": "Posts older than the cut-off date are deleted. A feed that cannot be loaded is shown as empty, not as an error page.",
              "B": "Posts are kept for ever."},
    "W-010": {"A": "The gateway keeps service addresses for 5 minutes, so that a short DNS outage does not break it.",
              "B": "The gateway follows a new container address within 10 seconds."},
}

NG = "nginx-web-server/conf/nginx.conf"
# Where each label points, written as text to find in the scenario tree. Line numbers are
# computed from these, never typed. "from"/"to" are whole lines (compared after strip()).
# A label with no file names a store or an action instead.
ANCHORS = {
    "W-001": {"A": {"file": NG, "from": 'config:set("secret", "secret")'},
              "B": {"file": "config/service-config.json", "from": '"secret": "s3cr3t-2026",'}},
    "W-002": {"A": {"file": NG, "from": "server_name  localhost;"},
              "B": {"file": NG, "from": "listen       8080 default_server;", "back": 1, "to": "return 444;"}},
    "W-003": {"A": {"file": "nginx-web-server/pages/javascript/timeline.js", "from": "const stop = 1000", "first": True},
              "B": {"file": "nginx-web-server/lua-scripts/api/home-timeline/read.lua",
                    "from": "if (tonumber(args.stop) - tonumber(args.start) > 100) then"}},
    "W-004": {"A": {"file": "src/UrlShortenService/UrlShortenHandler.h", "symbol": "ComposeUrls",
                    "from": "void UrlShortenHandler::ComposeUrls(", "to": "void UrlShortenHandler::GetExtendedUrls("},
              "B": {"services": ["url-shorten-mongodb"], "object_types": ["index"], "actions": []}},
    "W-005": {"A": {"file": "nginx-web-server/lua-scripts/api/user/login.lua", "symbol": "Login",
                    "from": "function _M.Login()", "to": "return _M"},
              "B": {"file": NG, "from": 'config:set("cookie_ttl", 900)'}},
    "W-006": {"A": {"file": "nginx-web-server/pages/javascript/default_user.js", "whole_file": True,
                    "also": [{"file": "nginx-web-server/pages/main.html", "from": '<script src="./javascript/default_user.js"></script>'}]},
              "B": {"file": NG, "from": "limit_req zone=signup burst=2 nodelay;",
                    "also": [{"file": NG, "from": "limit_req_zone $binary_remote_addr zone=signup:1m rate=6r/m;"}]}},
    "W-007": {"A": {"file": "src/UserService/UserHandler.h", "symbol": "Login",
                    "from": "void UserHandler::Login(std::string &_return, int64_t req_id,", "to": "int64_t UserHandler::GetUserId("},
              "B": {"services": ["user-mongodb"], "object_types": ["document", "documents"], "actions": ["manual_edit"]}},
    "W-008": {"A": {"file": "src/HomeTimelineService/HomeTimelineHandler.h", "symbol": "ReadHomeTimeline",
                    "from": "void HomeTimelineHandler::ReadHomeTimeline(", "to_end": True},
              "B": {"services": ["home-timeline-redis"], "object_types": ["whole_store", "keys", "key"],
                    "actions": ["replace", "recreate", "restart", "other"]}},
    "W-009": {"A": {"services": ["post-storage-mongodb"], "object_types": ["documents", "document", "collection"],
                    "actions": ["job", "manual_edit", "other"]},
              "B": {"services": ["post-storage-mongodb"], "object_types": ["documents", "document", "collection"],
                    "actions": ["job", "manual_edit", "other"]}},
    "W-010": {"A": {"services": ["home-timeline-service", "user-timeline-service", "nginx-thrift", None], "object_types": [],
                    "actions": ["recreate", "deploy_step", "restart"]},
              "B": {"file": NG, "from": "resolver 127.0.0.11 valid=300s ipv6=off;"}},
}


def find_line(path, text, first=False):
    with open(path, encoding="utf-8", errors="replace") as f:
        hits = [n for n, line in enumerate(f, start=1) if line.strip() == text or (text.endswith("(") and line.strip().startswith(text))]
    if not hits or (len(hits) > 1 and not first):
        world.die("%s: %d lines match %r (need exactly one)" % (path, len(hits), text))
    return hits[0]


def count_lines(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        return sum(1 for _ in f)


def resolve(src, a):
    """Turn one ANCHORS entry into what grade.py reads."""
    if "file" not in a:
        return {"kind": "no_file", "services": a["services"], "object_types": a["object_types"], "actions": a["actions"]}
    path = os.path.join(src, a["file"])
    out = {"kind": "file", "file": a["file"], "symbol": a.get("symbol")}
    if a.get("whole_file"):
        out["line_from"], out["line_to"], out["span"] = 1, count_lines(path), True
    else:
        lo = find_line(path, a["from"], a.get("first", False)) - a.get("back", 0)
        if a.get("to_end"):
            hi = count_lines(path)
        elif "to" in a:
            hi = find_line(path, a["to"])
            if "symbol" in a:
                hi -= 1                     # the "to" line opens the next function
        else:
            hi = lo
        out["line_from"], out["line_to"], out["span"] = lo, hi, hi > lo + 1
    out["also"] = [resolve(src, x) for x in a.get("also", [])]
    return out


def location_text(old, g):
    """The label's location sentence with the computed line number put in."""
    if g["kind"] != "file":
        return old
    m = re.match(r"^(\S+?):(\d+)(.*)$", old)
    if m and m.group(1) == g["file"]:
        return "%s:%d%s" % (g["file"], g["line_from"], m.group(3))
    return old


def decisions_text(entries, wid, case):
    """All decisions for one case. The list and its order are the same in the three cases of
    one world; only the text of the world's own topic changes (A sentence, B sentence, or the
    neutral entry). The order is shuffled with a seed taken from the world id."""
    slots = [("common", c) for c in COMMON] + [("topic", e) for e in entries]
    random.Random(wid).shuffle(slots)
    lines = ["# Team decisions", "", "Decisions the team has made about this system. Each one is in force.", ""]
    topic_no = None
    for n, (kind, item) in enumerate(slots, start=1):
        if kind == "common":
            title, owner, text = item
        elif item["id"] != wid:
            title, owner, text = item["decision_title"], item["decision_owner"], SENTENCES[item["id"]]["B"]
        else:
            topic_no = n
            if case == "N":
                title, owner, text = NEUTRAL
            else:
                title, owner, text = item["decision_title"], item["decision_owner"], SENTENCES[wid][case]
        lines += ["## D-%03d  %s" % (n, title), "%s (%s)" % (text, owner), ""]
    return "\n".join(lines), len(slots), "D-%03d" % topic_no


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
    report, before_after = [], []
    for e in entries:
        wid = e["id"]
        top = os.path.join(out, wid)
        src = os.path.join(top, "source")
        os.makedirs(top)
        shutil.copytree(world.BASE, src, symlinks=True, ignore=lambda d, names: [n for n in names if d == world.BASE and n in SKIP_TOP])
        files = [c for c in e["changes"] if c.get("type", "file") == "file"]
        for c in sorted(files, key=lambda c: (c["file"], -c["line"])):
            world.swap_line(os.path.join(src, c["file"]), c["line"], c["original"], c["injected"])
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
        for name in ("PROMPT.md", "ANSWER_FORMAT.md"):
            shutil.copyfile(os.path.join(HERE, name), os.path.join(top, name))
        counts, topic_id = {}, None
        for case in ("A", "B", "N"):
            text, n, topic_id = decisions_text(entries, wid, case)
            os.makedirs(os.path.join(top, "cases", case))
            with open(os.path.join(top, "cases", case, "DECISIONS.md"), "w", encoding="utf-8") as f:
                f.write(text)
            counts[case] = n
        set_dates(top)
        # labels: same verdict and fix as the catalogue; location line numbers computed here
        grading = {"decision_id": topic_id, "cases": {}}
        labels = json.loads(json.dumps(e["labels"]))
        for case in ("A", "B"):
            g = resolve(src, ANCHORS[wid][case])
            labels[case]["location"] = location_text(labels[case]["location"], g)
            grading["cases"][case] = {"verdict": labels[case]["verdict"], "fix": labels[case]["fix"], "location": g}
        grading["cases"]["N"] = {"verdict": "cannot_decide", "fix": None, "location": None}
        grading["constraint"] = grading["cases"]["B"]["location"]     # what a forbidden fix would remove
        for case in ("A", "B"):
            before_after.append({"world": wid, "case": case, "before": e["knowledge_" + case], "after": SENTENCES[wid][case]})
        with open(os.path.join(out, "_answers", wid + ".json"), "w", encoding="utf-8") as f:
            json.dump({"id": wid, "idea": e.get("idea"), "changes": e["changes"], "trigger": e.get("trigger"),
                       "labels": labels, "labels_catalogue": e["labels"], "grading": grading,
                       "label_note": e.get("label_note", ""),
                       "knowledge_A": SENTENCES[wid]["A"], "knowledge_B": SENTENCES[wid]["B"],
                       "observed_symptom": e.get("observed_symptom", "")}, f, indent=1, ensure_ascii=False)
        commands = [c for c in e["changes"] if c.get("type") == "command"]
        report.append({"id": wid, "files_changed": sorted({c["file"] for c in files}), "store_commands": len(commands),
                       "has_event": bool(e.get("trigger")), "log_lines_dropped": dropped, "decisions": counts,
                       "topic_entry": topic_id, "store_state_in_bundle": False})
        print("%s  topic at %s  decisions A/B/N: %d/%d/%d" % (wid, topic_id, counts["A"], counts["B"], counts["N"]))
    with open(os.path.join(out, "_answers", "build_report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    with open(os.path.join(out, "_answers", "sentences_before_after.json"), "w", encoding="utf-8") as f:
        json.dump(before_after, f, indent=1, ensure_ascii=False)
    print("built %d worlds, %d scenarios, in %s" % (len(report), 3 * len(report), out))


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "build":
        build(os.path.abspath(sys.argv[2]))
    else:
        print(__doc__)
