#!/usr/bin/env python3
"""Read-only access to the data stores for the diagnosis agent, with hard limits.

The agent never sends a shell command or a query string. It sends one small JSON request;
this file checks it against the rules below and only then builds the command itself.
Anything that is not explicitly allowed is refused.

RULES
  All stores   read only; one of the 13 store containers of docker-compose.yml; at most
               MAX_OUTPUT characters come back; every command has a time limit.
  MongoDB      find: needs a filter; every field in the filter must be the leading field of
               an index of that collection (looked up in the store at call time); values are
               text, whole numbers, true/false, or {$in, $eq, $gt, $gte, $lt, $lte}; at most
               20 documents; 2 seconds (maxTimeMS).
               Metadata, which reads no documents: list_collections, list_indexes,
               estimated_count (collection metadata, not a scan).
  Redis        on one named key: exists, type, ttl, zcard, zscore, zrange (at most 50 members,
               start and stop not negative). On the server: dbsize, info (a few sections),
               and scan: one step of SCAN, at most 100 keys looked at per call (it does not
               block the server, unlike KEYS).
               Not allowed, among others: KEYS, EVAL, CONFIG, FLUSH*, any write.
  memcached    get on one named key.

  store.py '<request as JSON>'        run one request against the live system
  store.py --dry '<request as JSON>'  check it and print the command, run nothing

The requests and examples the agent is shown are in agent.py (STORE_TOOL).
When the environment variable INTENT_STORE_LOG names a file, every request and its answer is
appended to it. run.py sets it to a file outside the incident folder; the grader reads that
record to check a quote the agent says it got from a store.
The memcached path needs bash in the JUMP container to open a connection to memcached.
"""
import json
import os
import re
import subprocess
import sys

MONGO = ("social-graph-mongodb", "post-storage-mongodb", "user-timeline-mongodb",
         "url-shorten-mongodb", "user-mongodb", "media-mongodb")
REDIS = ("social-graph-redis", "home-timeline-redis", "user-timeline-redis")
MEMCACHED = ("post-storage-memcached", "url-shorten-memcached", "user-memcached", "media-memcached")

MAX_DOCS = 20
MAX_TIME_MS = 2000
MAX_IN = 20
MAX_RANGE = 50
MAX_SCAN = 100
MAX_OUTPUT = 8000
TIMEOUT_S = 10
JUMP = "nginx-thrift"            # container used to reach memcached (the memcached image has no client)

NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")                 # database and collection names
FIELD = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)*$")
KEY = re.compile(r"^[\x21-\x7e]{1,250}$")                   # no spaces, no control characters
FILTER_OPS = ("$in", "$eq", "$gt", "$gte", "$lt", "$lte")
INFO_SECTIONS = ("server", "clients", "memory", "persistence", "stats", "replication", "keyspace")
FIELDS = {
    "list_collections": {"db"}, "list_indexes": {"db", "collection"}, "estimated_count": {"db", "collection"},
    "find": {"db", "collection", "filter", "limit", "fields"},
    "dbsize": set(), "info": {"section"}, "exists": {"key"}, "type": {"key"}, "ttl": {"key"}, "zcard": {"key"},
    "zscore": {"key", "member"}, "zrange": {"key", "start", "stop", "rev", "withscores"},
    "scan": {"cursor", "count", "match"},
    "get": {"key"},
}
OPS = {"mongo": ("list_collections", "list_indexes", "estimated_count", "find"),
       "redis": ("dbsize", "info", "exists", "type", "ttl", "zcard", "zscore", "zrange", "scan"),
       "memcached": ("get",)}
OPTIONAL = {"limit", "fields", "rev", "withscores", "count", "match"}
PATTERN = re.compile(r"^[\x21-\x7e]{1,64}$")

ROOT = os.path.dirname(os.path.abspath(__file__))
COMPOSE_DIR = os.environ.get("INTENT_BASE") or os.path.join(ROOT, "base", "socialNetwork")


class Refused(Exception):
    """The request breaks a rule. The message says which one."""


def kind_of(store):
    if store in MONGO:
        return "mongo"
    if store in REDIS:
        return "redis"
    if store in MEMCACHED:
        return "memcached"
    raise Refused("unknown store %r; allowed: %s" % (store, ", ".join(MONGO + REDIS + MEMCACHED)))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _scalar(v):
    return isinstance(v, (str, bool)) or _is_int(v)


def _js(v):
    """A checked value as mongo shell text. Whole numbers become NumberLong so that large ids
    keep every digit; text goes through json.dumps, which escapes quotes and line breaks."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if _is_int(v):
        return 'NumberLong("%d")' % v
    if isinstance(v, str):
        return json.dumps(v)
    if isinstance(v, list):
        return "[" + ", ".join(_js(x) for x in v) + "]"
    return "{" + ", ".join("%s: %s" % (json.dumps(k), _js(x)) for k, x in v.items()) + "}"


def check_filter(flt, indexed):
    if not isinstance(flt, dict) or not flt:
        raise Refused("find needs a filter with at least one field")
    for field, value in flt.items():
        if not isinstance(field, str) or not FIELD.match(field):
            raise Refused("filter field %r is not a plain field name (operators such as $where and $or are not allowed)" % (field,))
        if field not in indexed:
            raise Refused("filter field %r has no index in this collection; indexed fields: %s" % (field, ", ".join(sorted(indexed))))
        if _scalar(value):
            continue
        if not isinstance(value, dict) or not value:
            raise Refused("the value for %r must be text, a whole number, true/false, or an object with %s" % (field, ", ".join(FILTER_OPS)))
        for op, arg in value.items():
            if op not in FILTER_OPS:
                raise Refused("operator %r is not allowed; allowed: %s" % (op, ", ".join(FILTER_OPS)))
            if op == "$in":
                if not (isinstance(arg, list) and 1 <= len(arg) <= MAX_IN and all(_scalar(x) for x in arg)):
                    raise Refused("$in needs a list of 1 to %d plain values" % MAX_IN)
            elif not _scalar(arg):
                raise Refused("%s needs one plain value" % op)


def check(req):
    """Checks everything that does not need the store. Returns the kind of store."""
    if not isinstance(req, dict):
        raise Refused("the request must be a JSON object")
    if not isinstance(req.get("store"), str) or not isinstance(req.get("op"), str):
        raise Refused("the request needs \"store\" and \"op\"")
    kind = kind_of(req["store"])
    op = req["op"]
    if op not in OPS[kind]:
        raise Refused("operation %r is not allowed on a %s store; allowed: %s" % (op, kind, ", ".join(OPS[kind])))
    extra = set(req) - {"store", "op"} - FIELDS[op]
    if extra:
        raise Refused("unknown field(s) for %s: %s" % (op, ", ".join(sorted(extra))))
    missing = FIELDS[op] - OPTIONAL - set(req)
    if missing:
        raise Refused("%s needs: %s" % (op, ", ".join(sorted(missing))))
    for k in ("db", "collection"):
        if k in req and not (isinstance(req[k], str) and NAME.match(req[k])):
            raise Refused("%s is not a plain name (letters, digits, - and _ only)" % k)
    if "key" in req and not (isinstance(req["key"], str) and KEY.match(req["key"])):
        raise Refused("key must be 1 to 250 printable characters with no spaces")
    if "member" in req and not (isinstance(req["member"], str) and KEY.match(req["member"])):
        raise Refused("member must be 1 to 250 printable characters with no spaces")
    if op == "find":
        lim = req.get("limit", MAX_DOCS)
        if not (_is_int(lim) and 1 <= lim <= MAX_DOCS):
            raise Refused("limit must be a whole number from 1 to %d" % MAX_DOCS)
        fl = req.get("fields", [])
        if not (isinstance(fl, list) and len(fl) <= 20 and all(isinstance(x, str) and FIELD.match(x) for x in fl)):
            raise Refused("fields must be a list of at most 20 plain field names")
        if not isinstance(req["filter"], dict) or not req["filter"]:
            raise Refused("find needs a filter with at least one field")
    if op == "info" and req["section"] not in INFO_SECTIONS:
        raise Refused("info section must be one of: %s" % ", ".join(INFO_SECTIONS))
    if op == "zrange":
        a, b = req["start"], req["stop"]
        if not (_is_int(a) and _is_int(b) and 0 <= a <= b and b - a < MAX_RANGE):
            raise Refused("zrange needs 0 <= start <= stop and at most %d members" % MAX_RANGE)
        for k in ("rev", "withscores"):
            if not isinstance(req.get(k, False), bool):
                raise Refused("%s must be true or false" % k)
    if op == "scan":
        if not (_is_int(req["cursor"]) and req["cursor"] >= 0):
            raise Refused("cursor must be a whole number, 0 to start")
        n = req.get("count", 50)
        if not (_is_int(n) and 1 <= n <= MAX_SCAN):
            raise Refused("count must be a whole number from 1 to %d" % MAX_SCAN)
        if "match" in req and not (isinstance(req["match"], str) and PATTERN.match(req["match"])):
            raise Refused("match must be 1 to 64 printable characters with no spaces")
    return kind


def _exec(store, *args):
    return ["docker", "compose", "exec", "-T", store] + list(args)


def _mongo(store, db, script):
    return _exec(store, "mongo", "--quiet", db, "--eval", script)


def indexes_command(req):
    return _mongo(req["store"], req["db"], "print(JSON.stringify(db.getCollection(%s).getIndexes()))" % json.dumps(req["collection"]))


def leading_fields(index_json):
    """The first field of every index: a filter on such a field does not scan the collection."""
    try:
        return {next(iter(ix["key"])) for ix in json.loads(index_json) if ix.get("key")}
    except (ValueError, TypeError, AttributeError, StopIteration):
        raise Refused("could not read the index list of the collection, so no find is allowed on it")


def command(req, indexed=None):
    """The command for a request that passed check(). For find, `indexed` is the set of
    indexed fields of the collection, read from the store just before."""
    kind = check(req)
    op, store = req["op"], req["store"]
    if kind == "mongo":
        coll = "db.getCollection(%s)" % json.dumps(req.get("collection", ""))
        if op == "list_collections":
            return _mongo(store, req["db"], "print(JSON.stringify(db.getCollectionNames()))")
        if op == "list_indexes":
            return indexes_command(req)
        if op == "estimated_count":
            return _mongo(store, req["db"], "print(%s.estimatedDocumentCount())" % coll)
        if indexed is None:
            raise Refused("the index list of the collection is needed before a find")
        check_filter(req["filter"], indexed)
        proj = "{" + ", ".join("%s: 1" % json.dumps(f) for f in req.get("fields", [])) + "}"
        script = "%s.find(%s, %s).limit(%d).maxTimeMS(%d).forEach(function (d) { print(tojsononeline(d)); })" % (
            coll, _js(req["filter"]), proj, req.get("limit", MAX_DOCS), MAX_TIME_MS)
        return _mongo(store, req["db"], script)
    if kind == "redis":
        if op == "dbsize":
            words = ["DBSIZE"]
        elif op == "info":
            words = ["INFO", req["section"]]
        elif op == "zscore":
            words = ["ZSCORE", req["key"], req["member"]]
        elif op == "scan":
            words = ["SCAN", str(req["cursor"]), "COUNT", str(req.get("count", 50))]
            if "match" in req:
                words += ["MATCH", req["match"]]
        elif op == "zrange":
            words = ["ZREVRANGE" if req.get("rev") else "ZRANGE", req["key"], str(req["start"]), str(req["stop"])]
            if req.get("withscores"):
                words.append("WITHSCORES")
        else:
            words = [op.upper(), req["key"]]
        return _exec(store, "redis-cli", "--no-raw", *words)
    # memcached: the key is passed as an argument, never pasted into the script text
    script = 'exec 3<>"/dev/tcp/$0/11211"; printf "get %s\\r\\nquit\\r\\n" "$1" >&3; cat <&3'
    return _exec(JUMP, "bash", "-c", script, store, req["key"])


def _run(argv):
    r = subprocess.run(argv, cwd=COMPOSE_DIR, text=True, capture_output=True, timeout=TIMEOUT_S)
    if r.returncode != 0:
        raise Refused("the store did not answer: " + (r.stderr or r.stdout).strip()[:300])
    return r.stdout


def query(req, run=_run):
    """Check a request, run it, return {"ok": true, "output": ...} or {"ok": false, "refused": reason}.
    `run` takes a command (list of words) and returns its output; tests pass a fake."""
    try:
        kind = check(req)
        indexed = None
        if kind == "mongo" and req["op"] == "find":
            indexed = leading_fields(run(indexes_command(req)))
        out = run(command(req, indexed))
    except Refused as why:
        return {"ok": False, "refused": str(why)}
    except subprocess.TimeoutExpired:
        return {"ok": False, "refused": "the store did not answer within %d seconds" % TIMEOUT_S}
    cut = len(out) > MAX_OUTPUT
    return {"ok": True, "output": out[:MAX_OUTPUT], "truncated": cut}


def record(req, result):
    """Append the request and its answer to the run's record, if there is one."""
    path = os.environ.get("INTENT_STORE_LOG")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"request": req, "result": result}, ensure_ascii=False) + "\n")


def main(argv):
    dry = "--dry" in argv
    args = [a for a in argv[1:] if a != "--dry"]
    if len(args) != 1:
        print(__doc__)
        return 2
    try:
        req = json.loads(args[0])
    except ValueError as err:
        print(json.dumps({"ok": False, "refused": "the request is not JSON: %s" % err}))
        return 1
    if dry:
        try:
            if check(req) == "mongo" and req["op"] == "find":
                print(json.dumps({"ok": True, "note": "the filter is checked against the collection's indexes when it runs",
                                  "first_command": indexes_command(req)}))
            else:
                print(json.dumps({"ok": True, "command": command(req)}))
        except Refused as why:
            print(json.dumps({"ok": False, "refused": str(why)}))
            return 1
        return 0
    result = query(req)
    record(req, result)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
