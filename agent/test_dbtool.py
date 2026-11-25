#!/usr/bin/env python3
"""Tests for dbtool.py. No store and no Docker are used: a fake stands in for the command runner.
Run:  python3 test_dbtool.py     (also runs under pytest)"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import dbtool

USER_INDEXES = json.dumps([{"v": 2, "key": {"_id": 1}, "name": "_id_"},
                           {"v": 2, "unique": True, "key": {"user_id": 1}, "name": "user_id_1"}])


class Fake:
    """Records every command. Answers an index listing with USER_INDEXES."""
    def __init__(self, indexes=USER_INDEXES, output="ROW\n"):
        self.calls, self.indexes, self.output = [], indexes, output

    def __call__(self, argv):
        self.calls.append(argv)
        return self.indexes if "getIndexes()" in argv[-1] else self.output


def refused(req, fake=None):
    fake = fake or Fake()
    r = dbtool.query(req, run=fake)
    assert r["ok"] is False and r["refused"], (req, r)
    ran = [c for c in fake.calls if "getIndexes()" not in c[-1]]
    assert ran == [], "a refused request reached the store: %r" % (ran,)
    return r["refused"]


def find(flt, **more):
    return dict({"store": "user-mongodb", "op": "find", "db": "user", "collection": "user", "filter": flt}, **more)


def test_allowed_mongo_requests_build_the_expected_commands():
    f = Fake()
    r = dbtool.query(find({"user_id": 5}, limit=3, fields=["username"]), run=f)
    assert r == {"ok": True, "output": "ROW\n", "truncated": False}
    assert len(f.calls) == 2 and "getIndexes()" in f.calls[0][-1]
    argv = f.calls[1]
    assert argv[:8] == ["docker", "compose", "exec", "-T", "user-mongodb", "mongo", "--quiet", "user"] and argv[8] == "--eval"
    js = argv[9]
    assert 'find({"user_id": NumberLong("5")}, {"username": 1})' in js
    assert ".limit(3).maxTimeMS(2000)" in js
    f = Fake()
    assert dbtool.query(find({"user_id": {"$in": [1, 2, 3]}}), run=f)["ok"]
    assert ".limit(20)" in f.calls[1][-1]                                   # the default is the maximum
    big = 1234567890123456789                                               # a post id: must keep every digit
    f = Fake(indexes=json.dumps([{"key": {"post_id": 1}}]))
    assert dbtool.query({"store": "post-storage-mongodb", "op": "find", "db": "post", "collection": "post",
                         "filter": {"post_id": big}}, run=f)["ok"]
    assert 'NumberLong("1234567890123456789")' in f.calls[1][-1]
    for req, part in (({"store": "url-shorten-mongodb", "op": "list_collections", "db": "url-shorten"}, "getCollectionNames()"),
                      ({"store": "url-shorten-mongodb", "op": "list_indexes", "db": "url-shorten", "collection": "url-shorten"}, "getIndexes()"),
                      ({"store": "post-storage-mongodb", "op": "estimated_count", "db": "post", "collection": "post"}, "estimatedDocumentCount()")):
        f = Fake(indexes="[]", output="x")
        f.indexes = "x"
        assert dbtool.query(req, run=f)["ok"] and part in f.calls[-1][-1], req


def test_allowed_redis_and_memcached_requests():
    cases = [({"store": "home-timeline-redis", "op": "dbsize"}, ["DBSIZE"]),
             ({"store": "home-timeline-redis", "op": "info", "section": "keyspace"}, ["INFO", "keyspace"]),
             ({"store": "home-timeline-redis", "op": "zcard", "key": "17"}, ["ZCARD", "17"]),
             ({"store": "social-graph-redis", "op": "exists", "key": "17:followers"}, ["EXISTS", "17:followers"]),
             ({"store": "social-graph-redis", "op": "zscore", "key": "17:followers", "member": "5"}, ["ZSCORE", "17:followers", "5"]),
             ({"store": "user-timeline-redis", "op": "zrange", "key": "17", "start": 0, "stop": 49, "rev": True, "withscores": True},
              ["ZREVRANGE", "17", "0", "49", "WITHSCORES"])]
    for req, words in cases:
        f = Fake()
        assert dbtool.query(req, run=f)["ok"], req
        assert f.calls == [["docker", "compose", "exec", "-T", req["store"], "redis-cli", "--no-raw"] + words], f.calls
    for req, words in (({"store": "home-timeline-redis", "op": "scan", "cursor": 0}, ["SCAN", "0", "COUNT", "50"]),
                       ({"store": "social-graph-redis", "op": "scan", "cursor": 96, "count": 100, "match": "*:followers"},
                        ["SCAN", "96", "COUNT", "100", "MATCH", "*:followers"])):
        f = Fake()
        assert dbtool.query(req, run=f)["ok"], req
        assert f.calls[0][7:] == words, f.calls
    for bad in ({"cursor": -1}, {"cursor": "0"}, {"cursor": 0, "count": 101}, {"cursor": 0, "count": 0}, {"count": 10},
                {"cursor": 0, "match": "a b"}, {"cursor": 0, "match": ""}, {"cursor": 0, "match": "x" * 65},
                {"cursor": 0, "type": "zset"}, {"cursor": 0, "count": 10 ** 9}):
        refused(dict({"store": "home-timeline-redis", "op": "scan"}, **bad))
    refused({"store": "user-mongodb", "op": "scan", "cursor": 0})
    f = Fake()
    assert dbtool.query({"store": "user-memcached", "op": "get", "key": "mark:login"}, run=f)["ok"]
    argv = f.calls[0]
    assert argv[:7] == ["docker", "compose", "exec", "-T", "nginx-thrift", "bash", "-c"]
    assert argv[-2:] == ["user-memcached", "mark:login"] and "mark:login" not in argv[7]     # key is an argument, not script text


def test_every_write_and_every_unlisted_operation_is_refused():
    for store in ("user-mongodb", "home-timeline-redis", "user-memcached"):
        for op in ("insert", "update", "updateOne", "delete", "deleteMany", "remove", "drop", "dropDatabase", "createIndex",
                   "dropIndex", "aggregate", "count", "distinct", "mapReduce", "eval", "runCommand", "renameCollection",
                   "set", "del", "zadd", "zrem", "expire", "rename", "flushall", "flushdb", "keys", "sscan", "config",
                   "shutdown", "save", "bgsave", "monitor", "debug", "script", "replicaof", "slaveof", "migrate",
                   "add", "replace", "incr", "decr", "touch", "flush_all", "stats", "", "FIND", "Get"):
            if (store, op) == ("user-memcached", "get"):
                continue
            refused({"store": store, "op": op, "db": "user", "collection": "user", "key": "k", "filter": {"user_id": 1}})
    refused({"store": "home-timeline-redis", "op": "find", "db": "x", "collection": "y", "filter": {"a": 1}})   # wrong kind of store
    refused({"store": "user-mongodb", "op": "zcard", "key": "17"})
    refused({"store": "user-memcached", "op": "dbsize"})


def test_find_without_a_filter_or_on_an_unindexed_field_is_refused():
    assert "filter" in refused(find({}))
    assert "filter" in refused({"store": "user-mongodb", "op": "find", "db": "user", "collection": "user"})
    assert "no index" in refused(find({"username": "mark"}))                       # the real case of W-007
    assert "no index" in refused(find({"user_id": 5, "username": "mark"}))         # every field must be indexed
    assert "no index" in refused(find({"user_id.x": 5}))
    assert "index list" in refused(find({"user_id": 5}), Fake(indexes="not json"))
    assert "no index" in refused(find({"user_id": 5}), Fake(indexes="[]"))


def test_operators_that_scan_or_run_code_are_refused():
    for flt in ({"$where": "sleep(100000)"}, {"$or": [{"user_id": 1}]}, {"$expr": {}}, {"$text": {"$search": "a"}},
                {"user_id": {"$ne": 1}}, {"user_id": {"$nin": [1]}}, {"user_id": {"$not": {"$eq": 1}}},
                {"user_id": {"$exists": True}}, {"user_id": {"$regex": ".*"}}, {"user_id": {"$where": "1"}},
                {"user_id": {"$gt": {"$where": "1"}}}, {"user_id": {"$in": []}}, {"user_id": {"$in": list(range(21))}},
                {"user_id": {"$in": [[1]]}}, {"user_id": [1, 2]}, {"user_id": None}, {"user_id": 1.5}, {"user_id": {}},
                {"user_id": {"$function": {}}}, {"": 1}, {"user_id; db.dropDatabase()": 1}):
        refused(find(flt))


def test_limits_names_and_unknown_fields():
    for more in ({"limit": 21}, {"limit": 0}, {"limit": -1}, {"limit": "5"}, {"limit": True}, {"limit": 2.0},
                 {"fields": "username"}, {"fields": ["$where"]}, {"fields": ["a"] * 21},
                 {"sort": {"user_id": 1}}, {"skip": 10}, {"hint": "x"}, {"eval": "db.dropDatabase()"}, {"script": "x"}):
        refused(find({"user_id": 1}, **more))
    for store in ("mongo", "user-mongodb; rm -rf /", "nginx-thrift", "user-service", "jaeger-agent", "", None, 5, ["user-mongodb"]):
        refused({"store": store, "op": "list_collections", "db": "user"})
    for name in ('x"); db.dropDatabase(); ("', "a b", "a.b", "a$b", "", "a" * 65, "$cmd", "user;", None, 5):
        refused({"store": "user-mongodb", "op": "list_collections", "db": name})
        refused({"store": "user-mongodb", "op": "list_indexes", "db": "user", "collection": name})
        refused({"store": "user-mongodb", "op": "estimated_count", "db": "user", "collection": name})
    for req in ("find", None, [], 7, {}, {"store": "user-mongodb"}, {"op": "find"}):
        refused(req)


def test_redis_and_memcached_limits():
    z = {"store": "home-timeline-redis", "op": "zrange", "key": "17"}
    for a, b in ((0, 50), (0, -1), (-5, -1), (5, 4), (0, 10 ** 9), ("0", "10"), (0.0, 5), (True, 5)):
        refused(dict(z, start=a, stop=b))
    refused(dict(z, start=0, stop=5, rev="yes"))
    refused(dict(z, start=0, stop=5, byscore=True))
    for key in ("", "a b", "a\nb", "a\r\nFLUSHALL", "a\tb", "k" * 251, "café", None, 5, ["k"]):
        refused({"store": "home-timeline-redis", "op": "zcard", "key": key})
        refused({"store": "user-memcached", "op": "get", "key": key})
    for section in ("all", "everything", "commandstats", "", "keyspace\r\nFLUSHALL", None):
        refused({"store": "home-timeline-redis", "op": "info", "section": section})
    refused({"store": "home-timeline-redis", "op": "dbsize", "key": "x"})
    refused({"store": "home-timeline-redis", "op": "zscore", "key": "17", "member": "a b"})


def test_text_values_cannot_leave_their_quotes():
    evil = '"}); db.dropDatabase(); print({"a":"'
    f = Fake(indexes=json.dumps([{"key": {"shortened_url": 1}}]))
    r = dbtool.query({"store": "url-shorten-mongodb", "op": "find", "db": "url-shorten", "collection": "url-shorten",
                      "filter": {"shortened_url": evil}}, run=f)
    assert r["ok"]
    js = f.calls[1][-1]
    assert json.dumps(evil) in js and js.count("db.dropDatabase") == 1          # present only inside the quoted text
    assert js.startswith('db.getCollection("url-shorten").find({"shortened_url": "\\"}); db.dropDatabase();')
    f = Fake(indexes=json.dumps([{"key": {"shortened_url": 1}}]))
    dbtool.query({"store": "url-shorten-mongodb", "op": "find", "db": "url-shorten", "collection": "url-shorten",
                  "filter": {"shortened_url": "line\nbreak sep"}}, run=f)
    assert "\n" not in f.calls[1][-1] and " " not in f.calls[1][-1]


def test_output_is_cut_and_failures_do_not_crash():
    f = Fake(output="x" * 20000)
    r = dbtool.query({"store": "home-timeline-redis", "op": "dbsize"}, run=f)
    assert r["ok"] and len(r["output"]) == dbtool.MAX_OUTPUT and r["truncated"] is True

    def broken(argv):
        raise dbtool.Refused("the store did not answer: no such container")
    r = dbtool.query({"store": "home-timeline-redis", "op": "dbsize"}, run=broken)
    assert r == {"ok": False, "refused": "the store did not answer: no such container"}


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS  " + name)
        except AssertionError as err:
            failed += 1
            print("FAIL  %s: %s" % (name, err))
    print("%d passed, %d failed" % (len(tests) - failed, failed))
    sys.exit(1 if failed else 0)
