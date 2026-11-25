#!/usr/bin/env python3
"""Make one test world on the running socialNetwork deployment.

  world.py probe           run the functional probes, print PASS/FAIL, change nothing
  world.py apply  <id>     apply the changes of one catalogue entry and restart services
  world.py revert <id>     undo them and restart
  world.py run    <id>     baseline probes -> apply -> probes, logs, traces -> revert -> probes
  world.py status          say whether a world is currently applied

Only the standard library is used. The catalogue is experiments.json next to tools/.
Nothing here runs the agent under test.
"""
import datetime
import hashlib
import http.client as httpclient
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "base", "socialNetwork")
CATALOGUE = os.path.join(ROOT, "experiments.json")
EVIDENCE = os.path.join(ROOT, "evidence")
STATE = os.path.join(ROOT, "state")
GATEWAY = ("127.0.0.1", 8080)
JAEGER = ("127.0.0.1", 16686)
NO_DOCKER = os.environ.get("WORLD_NO_DOCKER") == "1"   # for dry tests of apply/revert


def die(msg):
    print("STOP: " + msg, file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------- HTTP

def http(method, path, data=None, cookie=None, host=GATEWAY, timeout=30, host_header=None):
    """Return (status, headers dict, body text). Never follows redirects. status 0 = no answer."""
    headers = {}
    body = None
    if data is not None:
        body = urllib.parse.urlencode(data)
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if cookie:
        headers["Cookie"] = cookie
    if host_header:
        headers["Host"] = host_header
    try:
        conn = httpclient.HTTPConnection(host[0], host[1], timeout=timeout)
        conn.request(method, path, body=body, headers=headers)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8", "replace")
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        conn.close()
        return resp.status, hdrs, text
    except Exception as exc:                       # connection refused, timeout, reset
        return 0, {}, "NO ANSWER: %s" % exc


def as_json(text):
    try:
        return json.loads(text)
    except Exception:
        return None


# ---------------------------------------------------------------- probes

def run_probes(extras=()):
    """Functional probes. Same checks as Janice's step1_probe.sh (P1-P9) plus three
    checks through the logged-in /api/ routes (A1-A3). Creates three fresh users."""
    now = int(time.time())
    uid_a = 7000000 + (now % 900000)
    uid_b, uid_c = uid_a + 1, uid_a + 2
    ua, ub, uc = "probe_a_%d" % uid_a, "probe_b_%d" % uid_b, "probe_c_%d" % uid_c
    marker = "PROBEMARK%d" % now
    results = []
    cookies = {}
    raw_cookie = {}

    def rec(name, status, code, detail=""):
        results.append({"probe": name, "status": status, "http": code, "detail": detail[:300]})
        print("  %-3s %-5s http=%s %s" % (name, status.upper(), code, detail[:110].replace("\r", "").replace("\n", " ")))

    def register(uid, name):
        return http("POST", "/wrk2-api/user/register", {
            "first_name": "first_" + name, "last_name": "last_" + name,
            "username": name, "password": "pw_" + name, "user_id": uid})

    def login(name):
        code, hdrs, text = http("POST", "/api/user/login", {"username": name, "password": "pw_" + name})
        raw = hdrs.get("set-cookie", "")
        if "login_token=" in raw:
            cookies[name] = raw.split(";")[0]
            raw_cookie[name] = raw
            return True, code, ""
        return False, code, text

    def compose(uid, name, text, media_ids="[]", media_types="[]"):
        return http("POST", "/wrk2-api/post/compose", {
            "username": name, "user_id": uid, "text": text,
            "media_ids": media_ids, "media_types": media_types, "post_type": 0})

    def judge(name, code, text, test, fail_msg):
        d = as_json(text)
        if d is None:
            rec(name, "error", code, "not JSON: " + text)
        else:
            try:
                good = bool(test(d))
            except Exception as exc:
                good = False
                fail_msg += " (%s)" % exc
            rec(name, "pass" if good else "fail", code, "" if good else fail_msg + ": " + text)

    def posts_with_marker(d):
        return [p for p in d if isinstance(p, dict) and marker in p.get("text", "")] if isinstance(d, list) else []

    # P1 register + login
    regs = [register(uid_a, ua), register(uid_b, ub), register(uid_c, uc)]
    if not all("Success" in r[2] for r in regs):
        rec("P1", "fail", regs[0][0], "register: " + " | ".join(r[2] for r in regs))
    else:
        la, lb = login(ua), login(ub)
        if la[0] and lb[0]:
            rec("P1", "pass", la[1])
        else:
            rec("P1", "fail", la[1], "login returned no login_token cookie: " + (la[2] or lb[2]))

    # P2 follow + follower list
    f1 = http("POST", "/wrk2-api/user/follow", {"user_name": ua, "followee_name": ub})
    f2 = http("POST", "/wrk2-api/user/follow", {"user_name": uc, "followee_name": ub})
    if "Success" not in f1[2] or "Success" not in f2[2]:
        rec("P2", "fail", f1[0], "follow failed: %s | %s" % (f1[2], f2[2]))
    else:
        code, _, text = http("GET", "/api/user/get_follower", cookie=cookies.get(ub))
        judge("P2", code, text, lambda d: all(str(u) in json.dumps(d) for u in (uid_a, uid_c)),
              "follower list is missing a follower")

    # P3 compose + own timeline
    compose(uid_b, ub, marker + " plain text post")
    time.sleep(1)
    code, _, text = http("GET", "/wrk2-api/user-timeline/read?user_id=%d&start=0&stop=20" % uid_b)
    judge("P3", code, text, lambda d: len(posts_with_marker(d)) > 0, "marker not in own user timeline")

    # P4 mention
    compose(uid_b, ub, marker + " mentioning @username_1 here")
    time.sleep(1)
    code, _, text = http("GET", "/wrk2-api/user-timeline/read?user_id=%d&start=0&stop=20" % uid_b)
    judge("P4", code, text, lambda d: any(
        any(m.get("username") == "username_1" for m in (p.get("user_mentions") or []))
        for p in posts_with_marker(d)), "mention of username_1 not resolved")

    # P5 url shortening
    compose(uid_b, ub, marker + " link http://example.com/%s/a-very-long-path-that-should-be-shortened end" % marker)
    time.sleep(1)
    code, _, text = http("GET", "/wrk2-api/user-timeline/read?user_id=%d&start=0&stop=20" % uid_b)
    judge("P5", code, text, lambda d: any(
        any(u.get("shortened_url") and u.get("shortened_url") != u.get("expanded_url") for u in (p.get("urls") or []))
        for p in posts_with_marker(d)), "no shortened url")

    # P6 media attached to a post (media-service only; does not need media-frontend)
    media_id = "123456789012345678"
    compose(uid_b, ub, marker + " with media", '["%s"]' % media_id, '["png"]')
    time.sleep(1)
    code, _, text = http("GET", "/wrk2-api/user-timeline/read?user_id=%d&start=0&stop=20" % uid_b)
    judge("P6", code, text, lambda d: any(
        any(str(m.get("media_id")) == media_id for m in (p.get("media") or []))
        for p in posts_with_marker(d)), "media id not attached")

    # P7 fan-out to a follower's home timeline
    code, _, text = http("GET", "/wrk2-api/home-timeline/read?user_id=%d&start=0&stop=20" % uid_a)
    judge("P7", code, text, lambda d: len(posts_with_marker(d)) > 0, "posts did not reach the follower's home timeline")

    # A1-A3: the logged-in routes, with user B's cookie
    ck = cookies.get(ub)
    code, _, text = http("GET", "/api/home-timeline/read?start=0&stop=20", cookie=ck)
    judge("A1", code, text, lambda d: code == 200 and isinstance(d, (list, dict)), "logged-in home timeline did not answer 200")
    code, _, text = http("GET", "/api/user-timeline/read?start=0&stop=20", cookie=ck)
    judge("A2", code, text, lambda d: code == 200 and len(posts_with_marker(d)) > 0, "logged-in user timeline lacks the marker")
    c3 = http("POST", "/api/post/compose", {"post_type": 0, "text": marker + " via api"}, cookie=ck)
    time.sleep(1)
    code, _, text = http("GET", "/wrk2-api/user-timeline/read?user_id=%d&start=0&stop=20" % uid_b)
    d = as_json(text)
    if c3[0] == 200 and d is not None and any("via api" in p.get("text", "") for p in posts_with_marker(d)):
        rec("A3", "pass", c3[0])
    else:
        rec("A3", "fail", c3[0], "logged-in compose: " + c3[2])

    # H1, H2: how the gateway is addressed. All probes above connect to 127.0.0.1 and send
    # a plain path, so their Host header is the IP address.
    q = "/wrk2-api/user-timeline/read?user_id=%d&start=0&stop=20" % uid_b
    code, _, text = http("GET", q, host_header="localhost:8080")
    judge("H1", code, text, lambda d: code == 200, "request with Host: localhost was not answered")
    code, _, text = http("GET", "http://localhost:8080" + q)
    judge("H2", code, text, lambda d: code == 200, "request with a full URL naming localhost was not answered")

    # B1, B2: what the web page itself would ask for. The range is read from the page
    # script (pages/javascript/timeline.js), so a change in the page changes the probe.
    p_start, p_stop = page_range()
    q = "/read?start=%d&stop=%d" % (p_start, p_stop)
    code, _, text = http("GET", "/api/home-timeline" + q, cookie=ck)
    judge("B1", code, text, lambda d: code == 200, "home feed as the page asks for it (%d to %d) failed" % (p_start, p_stop))
    code, _, text = http("GET", "/api/user-timeline" + q, cookie=ck)
    judge("B2", code, text, lambda d: code == 200, "profile timeline as the page asks for it (%d to %d) failed" % (p_start, p_stop))

    # C1: how long the login cookie lives, from the Expires date the gateway sends
    life = cookie_lifetime(raw_cookie.get(ub, ""))
    if life is None:
        rec("C1", "error", 0, "no Expires date in the login cookie: " + raw_cookie.get(ub, ""))
    else:
        rec("C1", "pass" if life >= 3600 else "fail", 302, "" if life >= 3600 else "login cookie lives %d seconds" % life)
        results[-1]["cookie_lifetime_seconds"] = life

    # S1 (only for worlds that ask for it): a new user signs up while the main page is open.
    # main.html loads default_user.js, which registers three demo users on every load.
    if "S1" in extras:
        def page_load():
            for n in ("mark", "elon", "bill"):
                http("POST", "/api/user/register", {"first_name": n, "last_name": n, "username": n, "password": "123"})
        every = page_refresh_seconds()
        wait = 4 if os.environ.get("WORLD_FAST") == "1" else 35
        page_load()
        end = time.time() + wait
        while time.time() < end:
            if every:
                time.sleep(min(every, max(0.0, end - time.time())))
                if time.time() < end:
                    page_load()
            else:
                time.sleep(max(0.0, end - time.time()))
        new = "signup_%d" % uid_a
        code, _, text = http("POST", "/api/user/register", {"first_name": new, "last_name": new, "username": new, "password": "pw"})
        rec("S1", "pass" if code in (200, 302) else "fail", code,
            "" if code in (200, 302) else "sign-up %d s after the page was opened (page refresh: %s): %s" % (wait, every or "none", text))

    # V1, V2 (only for worlds that ask for them): one fixed account, reset_target. The probe
    # reads its salt and password hash from user-mongodb, works out which of two known
    # passwords the database holds, and checks that the site agrees with the database.
    if "V1" in extras:
        import hashlib
        vname, known = "reset_target", ["pw_reset_old", "pw_reset_new"]
        http("POST", "/wrk2-api/user/register", {"first_name": vname, "last_name": vname, "username": vname,
                                                  "password": known[0], "user_id": 6000094})   # no effect if it exists
        code, out = service_exec("user-mongodb", ["mongo", "--quiet", "user", "--eval",
            'var u = db.user.findOne({username: "reset_target"}); print(u ? u.salt + " " + u.password : "none");'])
        parts = out.split()
        in_db = [k for k in known if len(parts) == 2 and hashlib.sha256((k + parts[0]).encode()).hexdigest() == parts[1]]
        if len(in_db) != 1:
            rec("V1", "error", 0, "could not tell which password user-mongodb holds: " + out[:120])
            rec("V2", "error", 0, "could not tell which password user-mongodb holds")
        else:
            good = in_db[0]
            other = [k for k in known if k != good][0]
            c1, h1, t1 = http("POST", "/api/user/login", {"username": vname, "password": good})
            ok1 = "login_token=" in h1.get("set-cookie", "")
            rec("V1", "pass" if ok1 else "fail", c1, "" if ok1 else "the password that user-mongodb holds (%s) is refused: %s" % (good, t1))
            c2, h2, t2 = http("POST", "/api/user/login", {"username": vname, "password": other})
            ok2 = "login_token=" not in h2.get("set-cookie", "")
            rec("V2", "pass" if ok2 else "fail", c2, "" if ok2 else "a password that user-mongodb does not hold (%s) is accepted" % other)

    # T1, T2 (only for worlds that ask for them): two fixed accounts with older posts.
    # veteran_a follows veteran_b, and veteran_b wrote three posts when the accounts were made.
    if "T1" in extras:
        va, vb = 6000088, 6000089
        flag = os.path.join(ROOT, "_scratch", "veterans_ready")
        if not os.path.exists(flag):                 # made once, never again, so a fault cannot be papered over
            for u, n in ((va, "veteran_a"), (vb, "veteran_b")):
                http("POST", "/wrk2-api/user/register", {"first_name": n, "last_name": n, "username": n, "password": "pw_" + n, "user_id": u})
            http("POST", "/wrk2-api/user/follow", {"user_name": "veteran_a", "followee_name": "veteran_b"})
            for i in (1, 2, 3):
                compose(vb, "veteran_b", "veteran post %d" % i)
            time.sleep(1)
            os.makedirs(os.path.dirname(flag), exist_ok=True)
            with open(flag, "w") as f:
                f.write(now_iso())
        def veteran_posts(path):
            code, _, text = http("GET", path)
            d = as_json(text)
            n = len([p for p in d if isinstance(p, dict) and "veteran post" in p.get("text", "")]) if isinstance(d, list) else 0
            return code, n, text
        code, n, text = veteran_posts("/wrk2-api/home-timeline/read?user_id=%d&start=0&stop=20" % va)
        rec("T1", "pass" if n >= 3 else "fail", code, "" if n >= 3 else "home feed of veteran_a shows %d of 3 older posts: %s" % (n, text))
        code, n, text = veteran_posts("/wrk2-api/user-timeline/read?user_id=%d&start=0&stop=20" % vb)
        rec("T2", "pass" if n >= 3 else "fail", code, "" if n >= 3 else "profile of veteran_b shows %d of 3 older posts: %s" % (n, text))

    # U1: two posts that carry the same link
    same = "http://example.com/%s/same-link" % marker
    compose(uid_b, ub, marker + " dup one " + same)
    c2 = compose(uid_b, ub, marker + " dup two " + same)
    time.sleep(1)
    code, _, text = http("GET", "/wrk2-api/user-timeline/read?user_id=%d&start=0&stop=20" % uid_b)
    d = as_json(text)
    got = [p.get("text", "") for p in posts_with_marker(d)] if d is not None else []
    if any("dup one" in t for t in got) and any("dup two" in t for t in got):
        rec("U1", "pass", c2[0])
    else:
        rec("U1", "fail", c2[0], "second post with the same link: " + c2[2])

    # P8 unfollow, list still non-empty
    u1 = http("POST", "/wrk2-api/user/unfollow", {"user_name": ua, "followee_name": ub})
    if "Success" not in u1[2]:
        rec("P8", "fail", u1[0], "unfollow failed: " + u1[2])
    else:
        code, _, text = http("GET", "/api/user/get_follower", cookie=ck)
        judge("P8", code, text, lambda d: str(uid_a) not in json.dumps(d) and str(uid_c) in json.dumps(d),
              "after unfollow the follower list is wrong")

    # P9 empty follower list. Upstream has a known defect here (Janice's FF-000): the
    # answer contains a Redis ZADD error. We only record what the system does.
    u2 = http("POST", "/wrk2-api/user/unfollow", {"user_name": uc, "followee_name": ub})
    code, _, text = http("GET", "/api/user/get_follower", cookie=ck)
    if "ZADD" in text:
        rec("P9", "known", code, "known upstream defect reproduced (ZADD on empty list)")
    else:
        d = as_json(text)
        rec("P9", "pass" if d in ([], {}) else "fail", code, text)

    return {"users": [ua, ub, uc], "marker": marker, "results": results}


def page_range():
    """The start and stop values the web page sends, from the first function in timeline.js."""
    import re
    try:
        with open(os.path.join(BASE, "nginx-web-server/pages/javascript/timeline.js"), encoding="utf-8") as f:
            js = f.read()
        a = re.search(r"const start = (\d+)", js)
        b = re.search(r"const stop = (\d+)", js)
        return int(a.group(1)), int(b.group(1))
    except Exception:
        return 0, 100


def cookie_lifetime(raw):
    """Seconds between now and the Expires date of a Set-Cookie header, or None."""
    import calendar
    import re
    m = re.search(r"Expires=\w+, (\d{2})-(\w{3})-(\d{2,4}) (\d{2}):(\d{2}):(\d{2}) GMT", raw)
    if not m:
        return None
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    year = int(m.group(3))
    year += 2000 if year < 100 else 0
    when = calendar.timegm((year, months.index(m.group(2)) + 1, int(m.group(1)), int(m.group(4)), int(m.group(5)), int(m.group(6))))
    return int(when - time.time())


def page_refresh_seconds():
    """The auto-refresh interval of main.html in seconds, or 0 when the page does not refresh."""
    import re
    try:
        with open(os.path.join(BASE, "nginx-web-server/pages/main.html"), encoding="utf-8") as f:
            m = re.search(r'http-equiv="refresh" content="(\d+)"', f.read())
        return int(m.group(1)) if m else 0
    except Exception:
        return 0


def summary(probe_run):
    return {r["probe"]: r["status"] for r in probe_run["results"]}


# ---------------------------------------------------------------- catalogue and files

def load_entry(wid):
    with open(CATALOGUE, encoding="utf-8") as f:
        entries = json.load(f)
    for e in entries:
        if e.get("id") == wid:
            return e
    die("no entry with id %s in %s" % (wid, CATALOGUE))


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def live_worlds():
    return sorted(n[:-5] for n in os.listdir(STATE) if n.endswith(".json")) if os.path.isdir(STATE) else []


def swap_line(path, line_no, expect, new):
    """Replace line line_no (1-based) if it is exactly `expect`. Keeps the line ending.
    `new` may hold several lines separated by \n."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        lines = f.readlines()
    if not 1 <= line_no <= len(lines):
        die("%s has no line %d" % (path, line_no))
    cur = lines[line_no - 1]
    ending = cur[len(cur.rstrip("\r\n")):]
    if cur.rstrip("\r\n") != expect:
        die("%s line %d is not what the catalogue says.\n  expected: %r\n  found:    %r" % (path, line_no, expect, cur.rstrip("\r\n")))
    lines[line_no - 1] = new.replace("\n", ending or "\n") + ending
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)


def compose_cmd(args, capture=False):
    if NO_DOCKER:
        print("  (dry) docker compose " + " ".join(args))
        return ""
    r = subprocess.run(["docker", "compose"] + args, cwd=BASE, text=True,
                       stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE)
    if r.returncode != 0:
        print("  docker compose %s failed: %s" % (" ".join(args), r.stderr.strip()[-400:]), file=sys.stderr)
    return r.stdout if capture else ""


def service_exec(service, args):
    """Run a command inside one container. Returns (exit code, output text)."""
    if NO_DOCKER:
        print("  (dry) docker compose exec -T %s %s" % (service, " ".join(args)))
        return 0, "(dry)"
    r = subprocess.run(["docker", "compose", "exec", "-T", service] + list(args), cwd=BASE, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = (r.stdout or "").strip()
    if r.returncode != 0:                            # docker's own messages only when it failed
        out = (out + "\n" + (r.stderr or "")).strip()
    return r.returncode, out


def command_failed(change, code, out):
    return code != 0 or any(mark in out for mark in change.get("fail_if", []))


def restart(services, settle):
    if not services:
        return
    print("  restarting: " + ", ".join(services))
    compose_cmd(["restart"] + list(services))
    if NO_DOCKER:
        return
    time.sleep(settle)
    for _ in range(30):                              # wait until the gateway answers again
        q = "/wrk2-api/user-timeline/read?user_id=1&start=0&stop=1"
        if http("GET", q, timeout=5)[0] != 0 or http("GET", q, timeout=5, host_header="localhost:8080")[0] != 0:
            break
        time.sleep(2)
    else:
        print("  warning: the gateway did not answer after the restart", file=sys.stderr)
    n = warm_up()
    if n is None:                                    # the gateway may answer only by name
        n = warm_up(host_header="localhost:8080", max_rounds=6)
    print("  warm-up: " + ("clean after %d rounds" % n if n else "never clean; the change itself may block these requests"))


def warm_up(max_rounds=10, host_header=None):
    """After a restart, connections that other services keep open to the restarted
    service are dead, and the first calls through them fail. That is an effect of the
    restart, not of the injected change. Send throwaway requests until two rounds in a
    row are clean, so the probes measure the change only."""
    clean = 0
    for i in range(1, max_rounds + 1):
        uid = 8000000 + (int(time.time() * 10) % 400000) * 2
        a, b = "warm_a_%d" % uid, "warm_b_%d" % (uid + 1)
        ok = True
        for u, n in ((uid, a), (uid + 1, b)):
            ok &= "Success" in http("POST", "/wrk2-api/user/register", {
                "first_name": n, "last_name": n, "username": n, "password": "pw_" + n, "user_id": u}, host_header=host_header)[2]
        ok &= "Success" in http("POST", "/wrk2-api/user/follow", {"user_name": a, "followee_name": b}, host_header=host_header)[2]
        for text in ("warm-up one", "warm-up two @username_1 http://example.com/warm/%d/%d" % (uid, i)):
            ok &= http("POST", "/wrk2-api/post/compose", {"username": b, "user_id": uid + 1, "text": text,
                       "media_ids": "[]", "media_types": "[]", "post_type": 0}, host_header=host_header)[0] == 200
        ok &= http("POST", "/api/user/login", {"username": a, "password": "pw_" + a}, host_header=host_header)[0] in (200, 302)
        ok &= http("GET", "/wrk2-api/user-timeline/read?user_id=%d&start=0&stop=5" % (uid + 1), host_header=host_header)[0] == 200
        ok &= http("GET", "/wrk2-api/home-timeline/read?user_id=%d&start=0&stop=5" % uid, host_header=host_header)[0] == 200
        clean = clean + 1 if ok else 0
        if clean >= 2:
            return i
        time.sleep(1)
    return None


def apply(entry):
    wid = entry["id"]
    if live_worlds():
        die("a world is already applied: %s. Revert it first." % ", ".join(live_worlds()))
    commands = [c for c in entry["changes"] if c.get("type") == "command"]
    changes = [c for c in entry["changes"] if c.get("type", "file") == "file"]
    for c in entry["changes"]:
        if c.get("type", "file") not in ("file", "command"):
            die("change type %r is not supported" % c.get("type"))
    for c in changes:
        if not os.path.isfile(os.path.join(BASE, c["file"])):
            die("file not found: " + c["file"])
    for c in changes:                                # check every line first, change nothing yet
        with open(os.path.join(BASE, c["file"]), encoding="utf-8", newline="") as f:
            lines = f.readlines()
        found = lines[c["line"] - 1].rstrip("\r\n") if 1 <= c["line"] <= len(lines) else None
        if found != c["original"]:
            die("%s line %d is not what the catalogue says.\n  expected: %r\n  found:    %r" % (c["file"], c["line"], c["original"], found))
    os.makedirs(STATE, exist_ok=True)
    files = sorted({c["file"] for c in changes})
    before = {f: sha(os.path.join(BASE, f)) for f in files}
    keep = os.path.join(STATE, wid + ".files")
    os.makedirs(keep, exist_ok=True)
    for n, f in enumerate(files):                    # a copy of every file before it is touched
        shutil.copy2(os.path.join(BASE, f), os.path.join(keep, str(n)))
    with open(os.path.join(STATE, wid + ".json"), "w", encoding="utf-8") as f:
        json.dump({"id": wid, "applied_at": now_iso(), "files": files, "sha_before": before}, f, indent=1)
    # line numbers refer to the clean file, so in each file work from the bottom up
    for c in sorted(changes, key=lambda c: (c["file"], -c["line"])):
        swap_line(os.path.join(BASE, c["file"]), c["line"], c["original"], c["injected"])
        print("  changed %s line %d" % (c["file"], c["line"]))
    outputs = []
    for c in commands:                               # a command on a data store or a container
        code, out = service_exec(c["service"], c["apply"])
        outputs.append({"service": c["service"], "apply": c["apply"], "exit": code, "output": out[:2000]})
        print("  ran on %s: %s" % (c["service"], out[:200].replace("\n", " ")))
        if command_failed(c, code, out):
            print("  the command failed, undoing everything", file=sys.stderr)
            revert(entry)
            die("command on %s failed: %s" % (c["service"], out[:400]))
    restart(entry.get("restart", []), entry.get("settle_seconds", 15))
    return outputs


def revert(entry):
    wid = entry["id"]
    spath = os.path.join(STATE, wid + ".json")
    if not os.path.isfile(spath):
        die("world %s is not applied (no state file)" % wid)
    with open(spath, encoding="utf-8") as f:
        state = json.load(f)
    for c in reversed([c for c in entry["changes"] if c.get("type") == "command"]):
        code, out = service_exec(c["service"], c["revert"])
        print("  undone on %s: %s" % (c["service"], out[:200].replace("\n", " ")))
    keep = os.path.join(STATE, wid + ".files")
    for n, f in enumerate(state.get("files", [])):   # put back the saved copies
        shutil.copy2(os.path.join(keep, str(n)), os.path.join(BASE, f))
        print("  restored %s" % f)
    for path, digest in state["sha_before"].items():
        if sha(os.path.join(BASE, path)) != digest:
            die("%s is not identical to its state before the change. Check it by hand." % path)
    os.remove(spath)
    shutil.rmtree(keep, ignore_errors=True)
    restart(entry.get("restart", []), entry.get("settle_seconds", 15))
    print("  reverted, files are identical to before")


def note_in_catalogue(wid, fields):
    with open(CATALOGUE, encoding="utf-8") as f:
        entries = json.load(f)
    for e in entries:
        if e.get("id") == wid:
            e.update(fields)
    with open(CATALOGUE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=1, ensure_ascii=False)


def run_all(ids):
    """Run several worlds one after another. Stop at the first one that fails to run or
    does not leave the system clean, so nothing is built on a dirty system."""
    os.makedirs(EVIDENCE, exist_ok=True)
    log = os.path.join(EVIDENCE, "run-all-%s.txt" % now_iso().replace(":", ""))
    def say(text):
        print(text)
        with open(log, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    for wid in ids:
        say("===== %s  %s" % (wid, now_iso()))
        try:
            run(load_entry(wid))
        except SystemExit:
            say("STOPPED: %s could not be run. Worlds after it were not started." % wid)
            return
        except Exception as exc:
            say("STOPPED: %s raised %r. Worlds after it were not started." % (wid, exc))
            if wid in live_worlds():
                say("trying to revert %s" % wid)
                revert(load_entry(wid))
            return
        with open(os.path.join(EVIDENCE, wid, "record.json"), encoding="utf-8") as f:
            r = json.load(f)
        say("%s changed: %s | clean again: %s" % (wid, ", ".join(r["probes_changed_by_the_fault"]) or "none", r["clean_again_after_revert"]))
        if not r["clean_again_after_revert"]:
            say("STOPPED: the system is not clean after %s. Worlds after it were not started." % wid)
            return
    say("ALL DONE")


def container_ip(service):
    """The address of a service's container on the Docker network."""
    if NO_DOCKER:
        return "(dry)"
    cid = compose_cmd(["ps", "-q", service], capture=True).strip().split("\n")[0]
    if not cid:
        return "none"
    r = subprocess.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", cid],
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return r.stdout.strip() or "none"


def do_trigger(trig):
    """An event that happens after the change is in place, for example a deployment that
    recreates containers. It is part of the world: without it the change shows nothing."""
    watch = trig.get("watch", [])
    before = {svc: container_ip(svc) for svc in watch}
    steps = list(trig.get("compose", []))
    if trig.get("swap"):                             # recreate two services so that they trade addresses:
        def as_number(svc):                          # the one with the higher address is started first
            try:                                     # and takes the lower free address
                return [int(x) for x in before.get(svc, container_ip(svc)).split(".")]
            except ValueError:
                return [0]
        first, second = sorted(trig["swap"], key=as_number, reverse=True)
        steps = [["rm", "-sf", first, second], ["up", "-d", first], ["up", "-d", second]]
    for args in steps:
        print("  docker compose " + " ".join(args))
        compose_cmd(args, capture=True)
    time.sleep(0 if NO_DOCKER else trig.get("wait_seconds", 20))
    after = {svc: container_ip(svc) for svc in watch}
    for svc in watch:
        print("  %s address: %s -> %s" % (svc, before[svc], after[svc]))
    rounds = None if NO_DOCKER else warm_up()
    print("  warm-up after the event: " + ("clean after %d rounds" % rounds if rounds else "never clean"))
    changed = any(before[svc] != after[svc] for svc in watch) if watch else None
    if changed is False:
        print("  NOTE: no address changed, so this event tested nothing")
    return {"addresses_before": before, "addresses_after": after, "addresses_changed": changed, "warm_up_rounds": rounds}


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_logs(out_dir, since_iso):
    os.makedirs(os.path.join(out_dir, "logs"), exist_ok=True)
    names = compose_cmd(["ps", "--services"], capture=True).split()
    for name in names:
        text = compose_cmd(["logs", "--no-color", "--no-log-prefix", "--since", since_iso, name], capture=True)
        with open(os.path.join(out_dir, "logs", name + ".log"), "w", encoding="utf-8") as f:
            f.write(text or "")
    return len(names)


def save_traces(out_dir, start_epoch):
    q = "/api/traces?service=nginx-web-server&limit=1500&start=%d&end=%d" % (
        int(start_epoch * 1_000_000), int(time.time() * 1_000_000))
    code, _, text = http("GET", q, host=JAEGER)
    with open(os.path.join(out_dir, "traces.json"), "w", encoding="utf-8") as f:
        f.write(text)
    d = as_json(text) or {}
    return code, len(d.get("data") or [])


def run(entry):
    wid = entry["id"]
    out_dir = os.path.join(EVIDENCE, wid)
    os.makedirs(out_dir, exist_ok=True)
    record = {"id": wid, "idea": entry.get("idea"), "started": now_iso(), "changes": entry["changes"]}

    for c in entry.get("prepare", []):               # housekeeping before the baseline
        code, out = service_exec(c["service"], c["cmd"])
        print("prepare on %s: %s" % (c["service"], out[:200].replace("\n", " ")))
    print("[1/5] baseline probes (clean system)")
    extras = entry.get("extra_probes", [])
    record["baseline"] = run_probes(extras)
    base = summary(record["baseline"])
    if any(v in ("fail", "error") for v in base.values()):
        print("  note: the clean system already fails some probes; they are kept as the baseline")

    print("[2/5] apply the change")
    record["command_output"] = apply(entry)
    t0 = time.time()
    since = now_iso()
    try:
        if entry.get("trigger"):
            print("[2b] the event")
            record["trigger"] = do_trigger(entry["trigger"])
        print("[3/5] probes with the change live")
        record["with_change"] = run_probes(extras)
        time.sleep(15)                                  # services send spans every 10 s (bufferFlushInterval)
        print("[4/5] save logs and traces")
        record["log_files"] = save_logs(out_dir, since)
        record["traces_http"], record["traces_saved"] = save_traces(out_dir, t0)
    finally:
        print("[5/5] revert")
        revert(entry)
    record["after_revert"] = run_probes(extras)
    live = summary(record["with_change"])
    after = summary(record["after_revert"])
    record["probes_changed_by_the_fault"] = sorted(p for p in base if live.get(p) != base[p])
    record["clean_again_after_revert"] = (after == base)
    record["finished"] = now_iso()
    with open(os.path.join(out_dir, "record.json"), "w", encoding="utf-8") as f:
        json.dump(record, f, indent=1)

    no_effect = (record.get("trigger") or {}).get("addresses_changed") is False
    note_in_catalogue(wid, {"status": "EVENT_HAD_NO_EFFECT" if no_effect else "MEASURED", "measured_at": record["finished"],
                            "observed_probes_changed": record["probes_changed_by_the_fault"],
                            "clean_again_after_revert": record["clean_again_after_revert"]})
    print("\nRESULT for %s" % wid)
    print("  probes that changed with the fault: %s" % (", ".join(record["probes_changed_by_the_fault"]) or "none"))
    for p in record["probes_changed_by_the_fault"]:
        r = next(x for x in record["with_change"]["results"] if x["probe"] == p)
        print("    %s: %s -> %s  http=%s  %s" % (p, base[p], r["status"], r["http"], r["detail"][:120].replace("\r", "").replace("\n", " ")))
    print("  clean again after revert: %s" % record["clean_again_after_revert"])
    print("  evidence saved in: %s" % out_dir)


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    cmd = sys.argv[1]
    if cmd == "probe":
        s = summary(run_probes(sys.argv[2:]))
        print("\n" + ", ".join("%s=%s" % kv for kv in s.items()))
    elif cmd == "status":
        print("applied worlds: " + (", ".join(live_worlds()) or "none"))
    elif cmd == "run-all":
        run_all(sys.argv[2:])
    elif cmd in ("apply", "revert", "run"):
        if len(sys.argv) < 3:
            die("give the world id, for example: world.py %s W-001" % cmd)
        entry = load_entry(sys.argv[2])
        {"apply": apply, "revert": revert, "run": run}[cmd](entry)
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
