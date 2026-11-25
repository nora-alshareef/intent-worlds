#!/usr/bin/env python3
"""Write the answer a perfect agent would give, for every world and case, into
agent/reference_answers/. Decision numbers and sentences are read from the built scenarios,
and every quote that points into source/ or evidence/ is checked against that file and line.

  make_reference.py <scenarios-dir>
"""
import copy
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
NG = "nginx-web-server/conf/nginx.conf"
RL = "nginx-web-server/lua-scripts/api/home-timeline/read.lua"
WR = "nginx-web-server/lua-scripts/wrk2-api/home-timeline/read.lua"
UH = "src/UrlShortenService/UrlShortenHandler.h"
US = "src/UserService/UserHandler.h"
HT = "src/HomeTimelineService/HomeTimelineHandler.h"


def F(id, kind, service, what, ev, file=None, line=None, symbol=None, ot=None, on=None, action=None):
    return {"id": id, "kind": kind, "service": service, "file": file, "line": line, "symbol": symbol,
            "object_type": ot, "object_name": on, "action": action, "what": what,
            "evidence": [{"source": s, "ref": r, "quote": q} for s, r, q in ev]}


W = {}
W["W-001"] = dict(q="Was the JWT signing secret rotated on purpose, and which value is the current one?",
 f=[F("F1","file",None,"The secret that user-service signs login tokens with was changed.",[("source","config/service-config.json:9","\"secret\": \"s3cr3t-2026\",")],file="config/service-config.json",line=9,symbol="secret"),
    F("F2","file","nginx-thrift","The gateway still checks tokens with the old secret.",[("source",NG+":68","config:set(\"secret\", \"secret\")"),("ticket","TICKET.md","Users can log in, but nothing works after login.")],file=NG,line=68,symbol="secret")],
 A=("F2","finish","Set the new secret in the gateway too and restart it."), B=("F1","revert","Put the old secret back in service-config.json and restart user-service."))
W["W-002"] = dict(q="Must the gateway answer for any host name or IP address, or only for listed names?",
 f=[F("F1","file","nginx-thrift","A catch-all server closes every request whose host name is not listed.",[("source",NG+":74","listen       8080 default_server;"),("source",NG+":75","return 444;")],file=NG,line=73,symbol="server"),
    F("F2","file","nginx-thrift","Only localhost is listed, so a request to 127.0.0.1 falls into the catch-all.",[("source",NG+":82","server_name  localhost;"),("ticket","TICKET.md","The benchmark connects to 127.0.0.1:8080.")],file=NG,line=82,symbol="server_name"),
    F("F3","file",None,"The benchmark script sends paths without a host name.",[("source","wrk2/scripts/social-network/mixed-workload.lua:69","local path = \"/wrk2-api/post/compose\"")],file="wrk2/scripts/social-network/mixed-workload.lua",line=69)],
 A=("F2","finish","Add the address the benchmark uses to server_name, or run the benchmark against a listed name."), B=("F1","revert","Remove the catch-all server block."))
W["W-003"] = dict(q="Is there a limit on how many posts one timeline request may ask for?",
 f=[F("F1","file","nginx-thrift","The gateway refuses a home-timeline request for more than 100 posts.",[("source",RL+":100","if (tonumber(args.stop) - tonumber(args.start) > 100) then"),("ticket","TICKET.md","answered with HTTP 400")],file=RL,line=100),
    F("F2","file","nginx-thrift","The page asks for 1000 posts.",[("source","nginx-web-server/pages/javascript/timeline.js:3","const stop = 1000")],file="nginx-web-server/pages/javascript/timeline.js",line=3,symbol="stop")],
 A=("F2","revert","Set the page back to at most 100 posts per request."), B=("F1","revert","Remove the size check from the gateway handler."))
W["W-004"] = dict(q="Does the url-shorten store keep one row per distinct long URL, or one row per link in every post?",
 f=[F("F1","store","url-shorten-mongodb","A unique index on the long URL rejects a second row for the same URL.",[("log","evidence/logs/url-shorten-mongodb.log:4","\"msg\":\"Index build: starting\""),("ticket","TICKET.md","All reported cases are posts that contain a link.")],ot="index",on="url-shorten.url-shorten: expanded_url_1 (unique)"),
    F("F2","file","url-shorten-service","ComposeUrls always inserts a new row and never looks for an existing one.",[("source",UH+":131","mongoc_bulk_operation_insert (bulk, doc);")],file=UH,line=131,symbol="ComposeUrls"),
    F("F3","file","text-service","The URL pattern has no '/', so only the host part is stored and many links collide.",[("source","src/TextService/TextHandler.h:65","e = \"(http://|https://)([a-zA-Z0-9_!~*'().&=+$%-]+)\";")],file="src/TextService/TextHandler.h",line=65,symbol="ComposeText")],
 A=("F2","finish","Look up the long URL first and reuse its row, or handle the duplicate-key error."), B=("F1","revert","Drop the index expanded_url_1."))
W["W-005"] = dict(q="What is the rule for session length: a fixed lifetime, or an end after a time without activity, and how long?",
 f=[F("F1","file","nginx-thrift","The login cookie lifetime is 900 seconds.",[("source",NG+":69","config:set(\"cookie_ttl\", 900)"),("ticket","TICKET.md","the login cookie now expires about 15 minutes after login")],file=NG,line=69,symbol="cookie_ttl"),
    F("F2","file","nginx-thrift","The cookie is set only at login and is never renewed, so the lifetime is counted from login, not from the last activity.",[("source","nginx-web-server/lua-scripts/api/user/login.lua:60","ngx.header[\"Set-Cookie\"] = \"login_token=\" .. ret .. \"; Path=/; Expires=\"")],file="nginx-web-server/lua-scripts/api/user/login.lua",line=60,symbol="Login")],
 A=("F2","finish","Renew the cookie on each authenticated request."), B=("F1","revert","Set cookie_ttl back to 3600 * 24."))
W["W-006"] = dict(q="Are per-address request limits allowed on this gateway, and are the sign-up limit and the page refresh wanted?",
 f=[F("F1","file","nginx-thrift","Sign-up requests are limited per client address.",[("source",NG+":90","limit_req zone=signup burst=2 nodelay;"),("log","evidence/logs/nginx-thrift.log:1","limiting requests, excess: 2.993 by zone \"signup\"")],file=NG,line=90,symbol="limit_req"),
    F("F2","file","nginx-thrift","A script loaded by the main page registers demo users on every page load and uses up the sign-up budget.",[("source","nginx-web-server/pages/main.html:273","<script src=\"./javascript/default_user.js\"></script>")],file="nginx-web-server/pages/javascript/default_user.js",symbol="generate_default_user"),
    F("F3","file","nginx-thrift","The main page reloads every 10 seconds.",[("source","nginx-web-server/pages/main.html:8","<meta http-equiv=\"refresh\" content=\"10\" />")],file="nginx-web-server/pages/main.html",line=8)],
 A=("F2","finish","Stop registering the demo users on every load of the main page."), B=("F1","revert","Remove the limit_req lines."))
W["W-007"] = dict(q="May credentials be changed directly in user-mongodb, and is user-mongodb the source of truth for them?",
 f=[F("F1","store","user-mongodb","The salt and password hash of one user were changed in the store, not through the application.",[("ticket","TICKET.md","The new password is refused with 'Incorrect username or password'. The old password still opens the account.")],ot="document",on="user.user: {username: reset_target}"),
    F("F2","file","user-service","Login uses the cached credentials first, and they are stored with no expiry.",[("source",US+":600","if (login_mmc) {"),("source",US+":737","login_str.length(), 0, 0);")],file=US,line=600,symbol="Login")],
 A=("F2","finish","Remove or expire the cached login entry when credentials change."), B=("F1","revert","Write the previous salt and hash back, and change the password through the application."))
W["W-008"] = dict(q="Is home-timeline-redis a cache that may be emptied, or the only copy of the home feeds?",
 f=[F("F1","runtime","home-timeline-redis","home-timeline-redis no longer holds the older feed entries: it was emptied or replaced without its data.",[("ticket","TICKET.md","their home feed is empty: the older posts are gone")],action="replace"),
    F("F2","file","home-timeline-service","ReadHomeTimeline reads Redis only; there is no way to rebuild a feed.",[("source",HT+":237","_redis_client_pool->zrevrange(std::to_string(user_id), start_idx,")],file=HT,line=237,symbol="ReadHomeTimeline")],
 A=("F2","finish","Rebuild a missing home feed from the social graph and the stored posts."), B=("F1","repair_data","Restore the feed data of home-timeline-redis from the previous instance or a backup."))
W["W-009"] = dict(q="Is there a retention policy for posts, and should a feed that cannot be loaded be shown as empty?",
 f=[F("F1","store","post-storage-mongodb","Older posts were deleted from post storage only. Their ids are still in home-timeline-redis, user-timeline-redis and user-timeline-mongodb.",[("log","evidence/logs/post-storage-service.log:1","(PostStorageHandler.h:613:ReadPosts) Return set incomplete"),("ticket","TICKET.md","Profile pages fail with 'Return set incomplete'.")],ot="documents",on="post.post: older posts"),
    F("F2","file","nginx-thrift","The benchmark home-timeline handler answers 200 with an empty list when the read fails.",[("source",WR+":84","ngx.status = ngx.HTTP_OK"),("source",WR+":86","ngx.say(\"[]\")")],file=WR,line=84,symbol="ReadHomeTimeline")],
 A=("F1","finish","Make the delete also remove the post ids from both timeline Redis stores and from user-timeline-mongodb."), B=("F1","repair_data","Restore the deleted posts from a backup."))
W["W-010"] = dict(q="How quickly must the gateway follow a new container address?",
 f=[F("F1","file","nginx-thrift","The gateway keeps resolved service addresses for 300 seconds.",[("source",NG+":44","resolver 127.0.0.11 valid=300s ipv6=off;"),("log","evidence/logs/nginx-thrift.log:4","Get home-timeline failure: Invalid method name: 'ReadHomeTimeline'")],file=NG,line=44,symbol="resolver"),
    F("F2","runtime",None,"A deployment recreated home-timeline-service and user-timeline-service, and the gateway was not restarted afterwards.",[("log","evidence/logs/home-timeline-service.log:1","Starting the home-timeline-service server..."),("ticket","TICKET.md","After a deployment, some home-feed requests fail with HTTP 500")],action="recreate")],
 A=("F2","finish","Restart or reload the gateway as part of any deployment that recreates a service."), B=("F1","revert","Set resolver valid back to 10s."))


def decision(path, number):
    with open(path, encoding="utf-8") as f:
        for block in f.read().split("## ")[1:]:
            if block.startswith(number):
                return block.split("\n")[1].rsplit(" (", 1)[0]
    raise SystemExit("no %s in %s" % (number, path))


def check_quotes(top, wid, findings):
    bad = 0
    for f in findings:
        for e in f["evidence"]:
            if e["source"] == "ticket":
                text = open(os.path.join(top, "TICKET.md"), encoding="utf-8").read()
                ok = e["quote"] in text
            else:
                rel, line = e["ref"].rsplit(":", 1)
                rel = rel if e["source"] == "log" else os.path.join("source", rel)
                lines = open(os.path.join(top, rel), encoding="utf-8", errors="replace").read().split("\n")
                ok = e["quote"] in lines[int(line) - 1]
            if not ok:
                bad += 1
                print("QUOTE NOT FOUND  %s %s %s: %r" % (wid, f["id"], e["ref"], e["quote"]))
        if f["kind"] == "file" and f["line"] is not None and not os.path.exists(os.path.join(top, "source", f["file"])):
            bad += 1
            print("NO SUCH FILE  %s %s" % (wid, f["file"]))
    return bad


def main(scen):
    out = os.path.join(HERE, "reference_answers")
    os.makedirs(out, exist_ok=True)
    bad = n = 0
    for wid, w in sorted(W.items()):
        top = os.path.join(scen, wid)
        with open(os.path.join(scen, "_answers", wid + ".json"), encoding="utf-8") as f:
            number = json.load(f)["grading"]["decision_id"]
        bad += check_quotes(top, wid, w["f"])
        for case in "ABN":
            fs = copy.deepcopy(w["f"])
            root = {"A": w["A"][0], "B": w["B"][0]}.get(case)
            if root:
                fs.sort(key=lambda x: (x["id"] != root, x["id"]))
            decided = case != "N"
            ans = {"schema_version": "1", "verdict": {"A": "intended", "B": "mistake", "N": "cannot_decide"}[case],
                   "findings": fs, "constraint": "F1",
                   "decision_used": number if decided else None,
                   "decision_quote": decision(os.path.join(top, "cases", case, "DECISIONS.md"), number) if decided else None,
                   "if_intended": dict(zip(["root_cause", "fix_class", "fix_what"], w["A"])),
                   "if_mistake": dict(zip(["root_cause", "fix_class", "fix_what"], w["B"])),
                   "open_question": None if decided else w["q"]}
            with open(os.path.join(out, "%s-%s.json" % (wid, case)), "w", encoding="utf-8") as f:
                json.dump(ans, f, indent=1, ensure_ascii=False)
            n += 1
    print("wrote %d reference answers; quotes not found: %d" % (n, bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(os.path.abspath(sys.argv[1])))
