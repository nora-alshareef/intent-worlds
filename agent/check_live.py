#!/usr/bin/env python3
"""Try dbtool.py against the running system. Read only. Run it on the machine where the
stack is up:   python3 agent/check_live.py
It prints one line per request: what was asked, OK or REFUSED, and the start of the answer."""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dbtool

REQUESTS = [
    ("MongoDB: indexes of the user collection", {"store": "user-mongodb", "op": "list_indexes", "db": "user", "collection": "user"}),
    ("MongoDB: estimated number of posts", {"store": "post-storage-mongodb", "op": "estimated_count", "db": "post", "collection": "post"}),
    ("MongoDB: collections of url-shorten", {"store": "url-shorten-mongodb", "op": "list_collections", "db": "url-shorten"}),
    ("MongoDB: find user 5 by indexed field", {"store": "user-mongodb", "op": "find", "db": "user", "collection": "user", "filter": {"user_id": 5}, "fields": ["username", "user_id"]}),
    ("MongoDB: find by username (must be REFUSED: no index)", {"store": "user-mongodb", "op": "find", "db": "user", "collection": "user", "filter": {"username": "username_5"}}),
    ("Redis: number of keys in home-timeline-redis", {"store": "home-timeline-redis", "op": "dbsize"}),
    ("Redis: one SCAN step", {"store": "home-timeline-redis", "op": "scan", "cursor": 0, "count": 10}),
    ("Redis: SCAN with a pattern", {"store": "social-graph-redis", "op": "scan", "cursor": 0, "count": 100, "match": "*:followers"}),
    ("Redis: size of one follower list", {"store": "social-graph-redis", "op": "zcard", "key": "5:followers"}),
    ("Redis: KEYS (must be REFUSED)", {"store": "home-timeline-redis", "op": "keys", "key": "*"}),
    ("memcached: get a key that does not exist (expect END)", {"store": "user-memcached", "op": "get", "key": "no_such_user:login"}),
    ("memcached: get a cached user id (expect VALUE or END)", {"store": "user-memcached", "op": "get", "key": "username_5:user_id"}),
]


def main():
    try:
        subprocess.run(["docker", "compose", "ps", "--services"], cwd=dbtool.COMPOSE_DIR, capture_output=True, text=True, timeout=20, check=True)
    except Exception as err:
        print("Cannot reach docker compose in %s: %s" % (dbtool.COMPOSE_DIR, err))
        return 1
    for what, req in REQUESTS:
        r = dbtool.query(req)
        text = (r.get("output") if r["ok"] else r["refused"]).strip().replace("\n", " | ")
        print("%-8s %s\n         %s" % ("OK" if r["ok"] else "REFUSED", what, text[:300]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
