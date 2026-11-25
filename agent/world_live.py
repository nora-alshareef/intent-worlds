#!/usr/bin/env python3
"""Apply a world to the running system and LEAVE IT LIVE, so that an agent can look at it.

  world_live.py status          which world is applied
  world_live.py apply  <id>     baseline probes -> apply -> probes -> save logs and traces
  world_live.py revert <id>     undo, restore the stores from the backups, probes again

For W-004, W-007, W-008 and W-009 the store change is made here in a way that leaves nothing
behind inside the store: no side collection, no renamed key. The backup needed for the revert
is kept on this machine, in agent/live_state/<id>/. The catalogue's own store commands for
these four worlds (which keep their backup inside the store) are not used.
File changes, restarts and the event of W-010 are done by tools/world.py, unchanged.

Tested live on W-007 only (2026-11-21: apply, clean store, revert, probes back to baseline).
W-004, W-009 and W-008 have NOT been run yet. Do W-008 last: it stops and starts two Redis containers.
With WORLD_NO_DOCKER=1 nothing is run; the commands are printed.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "tools"))
import world

KEEP = os.path.join(HERE, "live_state")
EVID = os.path.join(HERE, "live_evidence")
STORE_WORLDS = ("W-004", "W-007", "W-008", "W-009")
NEW_PASSWORD = "pw_reset_new"                       # the password probe V1 tries
NEW_SALT = "Qm3xT7vLp9ZsKd2HfYc8RwNb4JgUa6Ee"       # 32 characters, like the service's own salts
REDIS_OF_W008 = ("home-timeline-redis", "user-timeline-redis")
HEX24 = re.compile(r"^[0-9a-f]{24}$")


def say(text):
    print("  " + text)


def mongo(service, db, script):
    code, out = world.service_exec(service, ["mongo", "--quiet", db, "--eval", script])
    if code != 0 or "uncaught exception" in out or "errmsg" in out:
        raise RuntimeError("mongo on %s failed: %s" % (service, out[:400]))
    return out


def redis(service, *words):
    code, out = world.service_exec(service, ["redis-cli"] + list(words))
    if code != 0 or out.startswith("ERR") or "(error)" in out:
        raise RuntimeError("redis-cli %s on %s failed: %s" % (" ".join(words), service, out[:300]))
    return out


def compose(args, data=None, binary=False):
    """docker compose with bytes in or out (for dumps). Returns stdout."""
    if world.NO_DOCKER:
        print("  (dry) docker compose " + " ".join(args))
        return b"" if binary else ""
    r = subprocess.run(["docker", "compose"] + args, cwd=world.BASE, input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode != 0:
        raise RuntimeError("docker compose %s failed: %s" % (" ".join(args), r.stderr.decode("utf-8", "replace")[-400:]))
    return r.stdout if binary else r.stdout.decode("utf-8", "replace")


def number(text, what):
    m = re.search(r"-?\d+", text or "")
    if not m:
        if world.NO_DOCKER:
            return 0
        raise RuntimeError("no number in the answer for %s: %r" % (what, text))
    return int(m.group(0))


def keep_dir(wid):
    path = os.path.join(KEEP, wid)
    os.makedirs(path, exist_ok=True)
    return path


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=1)


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- W-004: a unique index on the long URL
def apply_w004(keep):
    svc, coll = "url-shorten-mongodb", 'db.getCollection("url-shorten")'
    listing = mongo(svc, "url-shorten",
                    "var seen = {}; %s.find().sort({_id: 1}).forEach(function (d) { if (seen[d.expanded_url]) { "
                    "print('DUP ' + JSON.stringify({id: d._id.str, s: d.shortened_url, e: d.expanded_url})); } "
                    "else { seen[d.expanded_url] = 1; } });" % coll)
    dups = [json.loads(line[4:]) for line in listing.splitlines() if line.startswith("DUP ")]
    if any(not HEX24.match(d["id"]) for d in dups):
        raise RuntimeError("an _id in url-shorten is not an ObjectId; stopping before any change")
    write_json(os.path.join(keep, "removed_rows.json"), dups)          # the backup, before anything is deleted
    say("%d duplicate rows saved to %s" % (len(dups), os.path.join(keep, "removed_rows.json")))
    for i in range(0, len(dups), 200):                                 # a unique index cannot be built over duplicates
        ids = ", ".join('ObjectId("%s")' % d["id"] for d in dups[i:i + 200])
        mongo(svc, "url-shorten", "print('deleted=' + %s.deleteMany({_id: {$in: [%s]}}).deletedCount);" % (coll, ids))
    out = mongo(svc, "url-shorten", "printjson(%s.createIndex({expanded_url: 1}, {unique: true}));" % coll)
    say("index built: " + " ".join(out.split())[:160])


def revert_w004(keep):
    svc, coll = "url-shorten-mongodb", 'db.getCollection("url-shorten")'
    try:
        say("index dropped: " + " ".join(mongo(svc, "url-shorten", "printjson(%s.dropIndex('expanded_url_1'));" % coll).split())[:120])
    except RuntimeError as err:
        say("the index was not there (%s)" % str(err)[:80])
    dups = read_json(os.path.join(keep, "removed_rows.json"))
    back = 0
    for i in range(0, len(dups), 100):
        docs = json.dumps([[d["id"], d["s"], d["e"]] for d in dups[i:i + 100]])
        out = mongo(svc, "url-shorten",
                    "var n = 0; %s.forEach(function (x) { if (!%s.findOne({_id: ObjectId(x[0])})) { "
                    "%s.insert({_id: ObjectId(x[0]), shortened_url: x[1], expanded_url: x[2]}); n++; } }); print('restored=' + n);"
                    % (docs, coll, coll))
        back += number(out, "restored rows")
    say("%d of %d rows put back" % (back, len(dups)))


# ---------------------------------------------------------------- W-007: one password edited by hand
def apply_w007(keep):
    old = mongo("user-mongodb", "user",
                'var u = db.user.findOne({username: "reset_target"}); print(u ? JSON.stringify({salt: u.salt, password: u.password}) : "none");')
    line = [x for x in old.splitlines() if x.startswith("{")]
    if not line and not world.NO_DOCKER:
        raise RuntimeError("the account reset_target does not exist; probe V1 should have made it")
    write_json(os.path.join(keep, "old_credentials.json"), json.loads(line[0]) if line else {})
    digest = hashlib.sha256((NEW_PASSWORD + NEW_SALT).encode()).hexdigest()          # UserHandler.h: hash256(password + salt)
    out = mongo("user-mongodb", "user",
                'print("modified=" + db.user.updateOne({username: "reset_target"}, {$set: {salt: %s, password: %s}}).modifiedCount);'
                % (json.dumps(NEW_SALT), json.dumps(digest)))
    if number(out, "modified") != 1 and not world.NO_DOCKER:
        raise RuntimeError("expected one changed document, got: " + out)
    say("salt and password hash of reset_target replaced in user-mongodb")


def revert_w007(keep):
    old = read_json(os.path.join(keep, "old_credentials.json"))
    out = mongo("user-mongodb", "user",
                'print("restored=" + db.user.updateOne({username: "reset_target"}, {$set: {salt: %s, password: %s}}).modifiedCount);'
                % (json.dumps(old["salt"]), json.dumps(old["password"])))
    say("old salt and hash written back (%s)" % out.strip())


# ---------------------------------------------------------------- W-008: both timeline Redis stores really empty
def apply_w008(keep):
    sizes = {}
    for svc in REDIS_OF_W008:                                           # every backup first, then every flush
        sizes[svc] = number(redis(svc, "DBSIZE"), "DBSIZE")
        redis(svc, "SAVE")
        compose(["cp", "%s:/data/dump.rdb" % svc, os.path.join(keep, svc + ".rdb")])
        if not world.NO_DOCKER and os.path.getsize(os.path.join(keep, svc + ".rdb")) < 20:
            raise RuntimeError("the backup of %s is empty; nothing was changed" % svc)
        say("%s: %d keys, backup copied out" % (svc, sizes[svc]))
    write_json(os.path.join(keep, "sizes.json"), sizes)
    for svc in REDIS_OF_W008:
        redis(svc, "FLUSHALL")
        say("%s emptied" % svc)


def revert_w008(keep):
    sizes = read_json(os.path.join(keep, "sizes.json"))
    for svc in REDIS_OF_W008:
        compose(["stop", svc])                                          # on stop Redis writes its file; ours goes in after that
        compose(["cp", os.path.join(keep, svc + ".rdb"), "%s:/data/dump.rdb" % svc])
        compose(["start", svc])
    if not world.NO_DOCKER:
        time.sleep(5)
    for svc in REDIS_OF_W008:
        now = number(redis(svc, "DBSIZE"), "DBSIZE")
        say("%s: %d keys now, %d before the change%s" % (svc, now, sizes[svc], "" if now >= sizes[svc] else "   <-- FEWER THAN BEFORE, CHECK BY HAND"))
    n = world.warm_up()
    say("warm-up: " + ("clean after %d rounds" % n if n else "never clean"))


# ---------------------------------------------------------------- W-009: older posts deleted
def apply_w009(keep):
    svc = "post-storage-mongodb"
    before = number(mongo(svc, "post", "print(db.post.count());"), "post count")
    dump = compose(["exec", "-T", svc, "mongodump", "--quiet", "--db", "post", "--collection", "post", "--archive"], binary=True)
    with open(os.path.join(keep, "post.archive"), "wb") as f:
        f.write(dump)
    if not world.NO_DOCKER and len(dump) < 100:
        raise RuntimeError("the dump of the post collection is empty; nothing was changed")
    write_json(os.path.join(keep, "count.json"), {"before": before})
    say("%d posts, dump of %d bytes saved" % (before, len(dump)))
    out = mongo(svc, "post", 'var cut = new Date().getTime(); print("removed=" + db.post.deleteMany({timestamp: {$lt: cut}}).deletedCount);')
    say("posts deleted (%s)" % out.strip())


def revert_w009(keep):
    svc = "post-storage-mongodb"
    with open(os.path.join(keep, "post.archive"), "rb") as f:
        dump = f.read()
    # documents that still exist are reported as duplicates and skipped; the deleted ones are inserted
    compose(["exec", "-T", svc, "mongorestore", "--quiet", "--archive"], data=dump, binary=True)
    before = read_json(os.path.join(keep, "count.json"))["before"]
    now = number(mongo(svc, "post", "print(db.post.count());"), "post count")
    say("%d posts now, %d before the change%s" % (now, before, "" if now >= before else "   <-- FEWER THAN BEFORE, CHECK BY HAND"))


STORE = {"W-004": (apply_w004, revert_w004), "W-007": (apply_w007, revert_w007),
         "W-008": (apply_w008, revert_w008), "W-009": (apply_w009, revert_w009)}


def files_only(entry):
    """The catalogue entry without its store commands: tools/world.py then does the file
    changes, the state file and the restarts, and nothing inside a store."""
    return dict(entry, changes=[c for c in entry["changes"] if c.get("type", "file") == "file"])


def failed(probe_run):
    """Probes that fail. A probe marked as a known upstream defect is not counted."""
    return sorted(r["probe"] for r in probe_run["results"] if str(r["status"]).lower() == "fail")


def apply(wid):
    entry = world.load_entry(wid)
    if world.live_worlds():
        world.die("a world is already applied: %s. Revert it first." % ", ".join(world.live_worlds()))
    extras = entry.get("extra_probes", [])
    print("baseline probes (they also create the accounts this world needs)")
    base = failed(world.run_probes(extras))
    say("failing before the change: %s" % (", ".join(base) or "none"))
    since, start = world.now_iso(), time.time()
    if wid in STORE:
        keep = keep_dir(wid)
        if os.listdir(keep):
            world.die("%s is not empty: a backup of an earlier apply is still there. Revert that first, or move it away." % keep)
        if wid == "W-009" and not world.NO_DOCKER and world.service_exec("post-storage-mongodb", ["which", "mongodump"])[0] != 0:
            world.die("mongodump is not in the post-storage-mongodb container; W-009 cannot be backed up this way")
        print("store change for %s" % wid)
        try:
            STORE[wid][0](keep)
        except Exception as err:
            print("STOP: %s\nThe backup, if one was made, is in %s. Run: world_live.py revert %s" % (err, keep, wid), file=sys.stderr)
            write_json(os.path.join(world.STATE, wid + ".json"), {"id": wid, "applied_at": since, "files": [], "sha_before": {}})
            sys.exit(1)
    print("file changes and restarts")
    world.apply(files_only(entry))
    if entry.get("trigger"):
        print("event")
        world.do_trigger(entry["trigger"])
    print("probes with the change")
    now = failed(world.run_probes(extras))
    say("failing now: %s" % (", ".join(now) or "none"))
    say("changed by this world: %s   (the catalogue says: %s)" % (", ".join(sorted(set(now) - set(base))) or "none",
                                                               ", ".join(entry.get("observed_probes_changed", [])) or "none"))
    out = os.path.join(EVID, wid)
    shutil.rmtree(out, ignore_errors=True)
    os.makedirs(out)
    if not world.NO_DOCKER:
        time.sleep(12)                                  # services send their spans every 10 seconds
        world.save_logs(out, since)
        world.save_traces(out, start)
    say("logs and traces saved in %s" % out)
    print("%s is LIVE. Undo it with: world_live.py revert %s" % (wid, wid))


def revert(wid):
    entry = world.load_entry(wid)
    if wid in STORE:
        print("restoring the store for %s" % wid)
        STORE[wid][1](keep_dir(wid))
    print("file changes and restarts")
    world.revert(files_only(entry))
    if wid in STORE:                                    # before the probes: a stop during the probes must not leave it behind
        done = os.path.join(KEEP, "_done", "%s-%d" % (wid, int(time.time())))
        os.makedirs(os.path.dirname(done), exist_ok=True)
        shutil.move(keep_dir(wid), done)                # the backup is kept, out of the way
        say("backup moved to %s" % done)
    print("probes after the revert")
    left = failed(world.run_probes(entry.get("extra_probes", [])))
    say("failing after the revert: %s" % (", ".join(left) or "none"))


def main(argv):
    if len(argv) == 2 and argv[1] == "status":
        print("applied worlds: " + (", ".join(world.live_worlds()) or "none"))
    elif len(argv) == 3 and argv[1] in ("apply", "revert"):
        {"apply": apply, "revert": revert}[argv[1]](argv[2])
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
