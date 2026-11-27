#!/usr/bin/env python3
"""Put one world into the running socialNetwork system, measure it, and undo it.

  world.py probe [S1 V1 T1]   run the probes on the system as it is, change nothing
  world.py status             say which world is applied right now
  world.py apply   <id>       baseline probes, apply the world, probes again, save logs and traces.
                              The world stays LIVE until it is reverted.
  world.py revert  <id>       undo the world, restore the stores from their backups, probes again
  world.py measure <id>       apply, then revert, and write what was measured into worlds.json

A world is one entry of worlds.json. Its changes are of three kinds:
  a line in a file     {"file", "line", "original", "injected"}   line numbers of the clean file
  a store recipe       {"recipe": name}                           a function in this file (RECIPES)
  a command            {"service", "apply", "revert", "fail_if"}  run inside one container

The probes are small requests a user could send (sign up, log in, post, read a feed). A world's
symptom is the set of probes that pass on the clean system and fail while the world is live.
Only the standard library is used. With WORLD_NO_DOCKER=1 nothing is run and the commands are printed.
"""
import calendar
import datetime
import hashlib
import http.client as httpclient
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("INTENT_BASE") or os.path.join(ROOT, "base", "socialNetwork")   # the DeathStarBench clone
WORLDS = os.path.join(ROOT, "worlds.json")
WORK = os.path.join(ROOT, ".work")                 # everything this file writes; not in git
STATE = os.path.join(WORK, "state")                # which world is applied, and the files as they were
BACKUP = os.path.join(WORK, "backup")              # store data taken out by a recipe
EVIDENCE = os.path.join(WORK, "evidence")          # logs and traces of the last apply of each world
GATEWAY = ("127.0.0.1", 8080)
JAEGER = ("127.0.0.1", 16686)
NO_DOCKER = os.environ.get("WORLD_NO_DOCKER") == "1"
CONFIG_SUFFIXES = (".conf", ".json", ".yml", ".yaml", ".cfg", ".ini", ".env")


def die(msg):
    print("STOP: " + msg, file=sys.stderr)
    sys.exit(1)


def say(text):
    print("  " + text)


def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------- the catalogue

def load_worlds():
    with open(WORLDS, encoding="utf-8") as f:
        return json.load(f)["worlds"]


def get_world(wid):
    for w in load_worlds():
        if w["id"] == wid:
            return w
    die("no world with id %s in %s" % (wid, WORLDS))


def update_world(wid, fields):
    with open(WORLDS, encoding="utf-8") as f:
        doc = json.load(f)
    for w in doc["worlds"]:
        if w["id"] == wid:
            w.update(fields)
    with open(WORLDS, "w", encoding="utf-8") as f:
        json.dump(doc, f, indent=1, ensure_ascii=False)


def file_changes(world):
    return [c for c in world["changes"] if "file" in c]


def where_of(path):
    """A file is 'configuration' or 'code', by its suffix. The answer format states the same rule."""
    return "configuration" if path.lower().endswith(CONFIG_SUFFIXES) else "code"


def changed_lines(world, path):
    """The line ranges, in the CHANGED file, that this world's changes occupy in `path`."""
    spans, shift = [], 0
    for c in sorted((c for c in file_changes(world) if c["file"] == path), key=lambda c: c["line"]):
        height = c["injected"].count("\n") + 1
        spans.append((c["line"] + shift, c["line"] + shift + height - 1))
        shift += height - 1
    return spans


# ---------------------------------------------------------------- HTTP

def http(method, path, data=None, cookie=None, host=GATEWAY, timeout=30, host_header=None):
    """Return (status, headers, body text). Never follows redirects. Status 0 means no answer."""
    headers, body = {}, None
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
    except Exception as exc:                          # connection refused, timeout, reset
        return 0, {}, "NO ANSWER: %s" % exc


def as_json(text):
    try:
        return json.loads(text)
    except ValueError:
        return None


# ---------------------------------------------------------------- the probes

def run_probes(extras=()):
    """18 probes on every call, plus S1, V1/V2, T1/T2 for the worlds that ask for them.
    P1-P9 are the checks of the team's earlier functional set; the others were added when a
    change turned out to be invisible to those nine. Three fresh users are created each time."""
    now = int(time.time())
    uid_a = 7000000 + (now % 900000)
    uid_b, uid_c = uid_a + 1, uid_a + 2
    ua, ub, uc = "probe_a_%d" % uid_a, "probe_b_%d" % uid_b, "probe_c_%d" % uid_c
    marker = "PROBEMARK%d" % now
    results, cookies, raw_cookie = [], {}, {}

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
            return
        try:
            good = bool(test(d))
        except Exception as exc:
            good = False
            fail_msg += " (%s)" % exc
        rec(name, "pass" if good else "fail", code, "" if good else fail_msg + ": " + text)

    def with_marker(d):
        return [p for p in d if isinstance(p, dict) and marker in p.get("text", "")] if isinstance(d, list) else []

    def own_timeline():
        time.sleep(1)
        return http("GET", "/wrk2-api/user-timeline/read?user_id=%d&start=0&stop=20" % uid_b)

    # P1 register and log in
    regs = [register(uid_a, ua), register(uid_b, ub), register(uid_c, uc)]
    if not all("Success" in r[2] for r in regs):
        rec("P1", "fail", regs[0][0], "register: " + " | ".join(r[2] for r in regs))
    else:
        la, lb = login(ua), login(ub)
        if la[0] and lb[0]:
            rec("P1", "pass", la[1])
        else:
            rec("P1", "fail", la[1], "login returned no login_token cookie: " + (la[2] or lb[2]))
    ck = cookies.get(ub)

    # P2 follow, and the follower list
    f1 = http("POST", "/wrk2-api/user/follow", {"user_name": ua, "followee_name": ub})
    f2 = http("POST", "/wrk2-api/user/follow", {"user_name": uc, "followee_name": ub})
    if "Success" not in f1[2] or "Success" not in f2[2]:
        rec("P2", "fail", f1[0], "follow failed: %s | %s" % (f1[2], f2[2]))
    else:
        code, _, text = http("GET", "/api/user/get_follower", cookie=ck)
        judge("P2", code, text, lambda d: all(str(u) in json.dumps(d) for u in (uid_a, uid_c)),
              "follower list is missing a follower")

    # P3 a post appears on its author's timeline
    compose(uid_b, ub, marker + " plain text post")
    code, _, text = own_timeline()
    judge("P3", code, text, lambda d: len(with_marker(d)) > 0, "marker not in own user timeline")

    # P4 a mention is resolved
    compose(uid_b, ub, marker + " mentioning @username_1 here")
    code, _, text = own_timeline()
    judge("P4", code, text, lambda d: any(
        any(m.get("username") == "username_1" for m in (p.get("user_mentions") or []))
        for p in with_marker(d)), "mention of username_1 not resolved")

    # P5 a link is shortened
    compose(uid_b, ub, marker + " link http://example.com/%s/a-very-long-path-that-should-be-shortened end" % marker)
    code, _, text = own_timeline()
    judge("P5", code, text, lambda d: any(
        any(u.get("shortened_url") and u.get("shortened_url") != u.get("expanded_url") for u in (p.get("urls") or []))
        for p in with_marker(d)), "no shortened url")

    # P6 media attached to a post (media-service only; media-frontend is not needed)
    media_id = "123456789012345678"
    compose(uid_b, ub, marker + " with media", '["%s"]' % media_id, '["png"]')
    code, _, text = own_timeline()
    judge("P6", code, text, lambda d: any(
        any(str(m.get("media_id")) == media_id for m in (p.get("media") or []))
        for p in with_marker(d)), "media id not attached")

    # P7 the posts reach a follower's home timeline
    code, _, text = http("GET", "/wrk2-api/home-timeline/read?user_id=%d&start=0&stop=20" % uid_a)
    judge("P7", code, text, lambda d: len(with_marker(d)) > 0, "posts did not reach the follower's home timeline")

    # A1-A3 the logged-in routes, with user B's cookie
    code, _, text = http("GET", "/api/home-timeline/read?start=0&stop=20", cookie=ck)
    judge("A1", code, text, lambda d: code == 200 and isinstance(d, (list, dict)), "logged-in home timeline did not answer 200")
    code, _, text = http("GET", "/api/user-timeline/read?start=0&stop=20", cookie=ck)
    judge("A2", code, text, lambda d: code == 200 and len(with_marker(d)) > 0, "logged-in user timeline lacks the marker")
    c3 = http("POST", "/api/post/compose", {"post_type": 0, "text": marker + " via api"}, cookie=ck)
    code, _, text = own_timeline()
    d = as_json(text)
    if c3[0] == 200 and d is not None and any("via api" in p.get("text", "") for p in with_marker(d)):
        rec("A3", "pass", c3[0])
    else:
        rec("A3", "fail", c3[0], "logged-in compose: " + c3[2])

    # H1, H2 the gateway addressed by name. Every probe above connects to 127.0.0.1 with a
    # plain path, so its Host header is the IP address.
    q = "/wrk2-api/user-timeline/read?user_id=%d&start=0&stop=20" % uid_b
    code, _, text = http("GET", q, host_header="localhost:8080")
    judge("H1", code, text, lambda d: code == 200, "request with Host: localhost was not answered")
    code, _, text = http("GET", "http://localhost:8080" + q)
    judge("H2", code, text, lambda d: code == 200, "request with a full URL naming localhost was not answered")

    # B1, B2 what the web page itself asks for. The range is read from the page script, so
    # a change in the page changes the probe.
    p_start, p_stop = page_range()
    q = "/read?start=%d&stop=%d" % (p_start, p_stop)
    code, _, text = http("GET", "/api/home-timeline" + q, cookie=ck)
    judge("B1", code, text, lambda d: code == 200, "home feed as the page asks for it (%d to %d) failed" % (p_start, p_stop))
    code, _, text = http("GET", "/api/user-timeline" + q, cookie=ck)
    judge("B2", code, text, lambda d: code == 200, "profile timeline as the page asks for it (%d to %d) failed" % (p_start, p_stop))

    # C1 how long the login cookie lives, from the Expires date the gateway sends
    life = cookie_lifetime(raw_cookie.get(ub, ""))
    if life is None:
        rec("C1", "error", 0, "no Expires date in the login cookie: " + raw_cookie.get(ub, ""))
    else:
        rec("C1", "pass" if life >= 3600 else "fail", 302, "" if life >= 3600 else "login cookie lives %d seconds" % life)

    if "S1" in extras:
        probe_signup(rec, uid_a)
    if "V1" in extras:
        probe_password(rec)
    if "T1" in extras:
        probe_older_posts(rec, compose)

    # U1 two posts that carry the same link
    same = "http://example.com/%s/same-link" % marker
    compose(uid_b, ub, marker + " dup one " + same)
    c2 = compose(uid_b, ub, marker + " dup two " + same)
    code, _, text = own_timeline()
    d = as_json(text)
    got = [p.get("text", "") for p in with_marker(d)] if d is not None else []
    if any("dup one" in t for t in got) and any("dup two" in t for t in got):
        rec("U1", "pass", c2[0])
    else:
        rec("U1", "fail", c2[0], "second post with the same link: " + c2[2])

    # P8 unfollow; the list still holds the other follower
    u1 = http("POST", "/wrk2-api/user/unfollow", {"user_name": ua, "followee_name": ub})
    if "Success" not in u1[2]:
        rec("P8", "fail", u1[0], "unfollow failed: " + u1[2])
    else:
        code, _, text = http("GET", "/api/user/get_follower", cookie=ck)
        judge("P8", code, text, lambda d: str(uid_a) not in json.dumps(d) and str(uid_c) in json.dumps(d),
              "after unfollow the follower list is wrong")

    # P9 an empty follower list. The clean system has a known defect here: the answer holds a
    # Redis ZADD error. It is recorded as "known", never as a failure.
    http("POST", "/wrk2-api/user/unfollow", {"user_name": uc, "followee_name": ub})
    code, _, text = http("GET", "/api/user/get_follower", cookie=ck)
    if "ZADD" in text:
        rec("P9", "known", code, "known upstream defect reproduced (ZADD on empty list)")
    else:
        rec("P9", "pass" if as_json(text) in ([], {}) else "fail", code, text)
    return results


def probe_signup(rec, uid):
    """S1: a new user signs up while the main page is open. main.html loads default_user.js,
    which registers three demo users on every load."""
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
    new = "signup_%d" % uid
    code, _, text = http("POST", "/api/user/register", {"first_name": new, "last_name": new, "username": new, "password": "pw"})
    good = code in (200, 302)
    rec("S1", "pass" if good else "fail", code,
        "" if good else "sign-up %d s after the page was opened (page refresh: %s): %s" % (wait, every or "none", text))


def probe_password(rec):
    """V1, V2: one fixed account, reset_target. The probe reads its salt and hash from
    user-mongodb, works out which of two known passwords the database holds, and checks that
    the site agrees with the database."""
    name, known = "reset_target", ["pw_reset_old", "pw_reset_new"]
    http("POST", "/wrk2-api/user/register", {"first_name": name, "last_name": name, "username": name,
                                              "password": known[0], "user_id": 6000094})    # no effect if it exists
    _, out = service_exec("user-mongodb", ["mongo", "--quiet", "user", "--eval",
        'var u = db.user.findOne({username: "reset_target"}); print(u ? u.salt + " " + u.password : "none");'])
    parts = out.split()
    in_db = [k for k in known if len(parts) == 2 and hashlib.sha256((k + parts[0]).encode()).hexdigest() == parts[1]]
    if len(in_db) != 1:
        rec("V1", "error", 0, "could not tell which password user-mongodb holds: " + out[:120])
        rec("V2", "error", 0, "could not tell which password user-mongodb holds")
        return
    good = in_db[0]
    other = [k for k in known if k != good][0]
    c1, h1, t1 = http("POST", "/api/user/login", {"username": name, "password": good})
    ok1 = "login_token=" in h1.get("set-cookie", "")
    rec("V1", "pass" if ok1 else "fail", c1, "" if ok1 else "the password that user-mongodb holds (%s) is refused: %s" % (good, t1))
    c2, h2, _ = http("POST", "/api/user/login", {"username": name, "password": other})
    ok2 = "login_token=" not in h2.get("set-cookie", "")
    rec("V2", "pass" if ok2 else "fail", c2, "" if ok2 else "a password that user-mongodb does not hold (%s) is accepted" % other)


def probe_older_posts(rec, compose):
    """T1, T2: two fixed accounts with older posts. veteran_a follows veteran_b, and veteran_b
    wrote three posts when the accounts were made."""
    va, vb = 6000088, 6000089
    flag = os.path.join(WORK, "veterans_ready")
    if not os.path.exists(flag):                      # made once, never again, so that a fault cannot be papered over
        for u, n in ((va, "veteran_a"), (vb, "veteran_b")):
            http("POST", "/wrk2-api/user/register", {"first_name": n, "last_name": n, "username": n, "password": "pw_" + n, "user_id": u})
        http("POST", "/wrk2-api/user/follow", {"user_name": "veteran_a", "followee_name": "veteran_b"})
        for i in (1, 2, 3):
            compose(vb, "veteran_b", "veteran post %d" % i)
        time.sleep(1)
        os.makedirs(WORK, exist_ok=True)
        with open(flag, "w") as f:
            f.write(now_iso())

    def older(path):
        code, _, text = http("GET", path)
        d = as_json(text)
        n = len([p for p in d if isinstance(p, dict) and "veteran post" in p.get("text", "")]) if isinstance(d, list) else 0
        return code, n, text
    code, n, text = older("/wrk2-api/home-timeline/read?user_id=%d&start=0&stop=20" % va)
    rec("T1", "pass" if n >= 3 else "fail", code, "" if n >= 3 else "home feed of veteran_a shows %d of 3 older posts: %s" % (n, text))
    code, n, text = older("/wrk2-api/user-timeline/read?user_id=%d&start=0&stop=20" % vb)
    rec("T2", "pass" if n >= 3 else "fail", code, "" if n >= 3 else "profile of veteran_b shows %d of 3 older posts: %s" % (n, text))


def page_range():
    """The start and stop values the web page sends, from the first function in timeline.js."""
    try:
        with open(os.path.join(BASE, "nginx-web-server/pages/javascript/timeline.js"), encoding="utf-8") as f:
            js = f.read()
        return int(re.search(r"const start = (\d+)", js).group(1)), int(re.search(r"const stop = (\d+)", js).group(1))
    except (OSError, AttributeError):
        return 0, 100


def page_refresh_seconds():
    """The auto-refresh interval of main.html in seconds, or 0 when the page does not refresh."""
    try:
        with open(os.path.join(BASE, "nginx-web-server/pages/main.html"), encoding="utf-8") as f:
            m = re.search(r'http-equiv="refresh" content="(\d+)"', f.read())
        return int(m.group(1)) if m else 0
    except OSError:
        return 0


def cookie_lifetime(raw):
    """Seconds between now and the Expires date of a Set-Cookie header, or None."""
    m = re.search(r"Expires=\w+, (\d{2})-(\w{3})-(\d{2,4}) (\d{2}):(\d{2}):(\d{2}) GMT", raw)
    if not m:
        return None
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    year = int(m.group(3))
    year += 2000 if year < 100 else 0
    when = calendar.timegm((year, months.index(m.group(2)) + 1, int(m.group(1)), int(m.group(4)), int(m.group(5)), int(m.group(6))))
    return int(when - time.time())


def system_answers():
    """True when the gateway answers at all, by IP address or by name."""
    q = "/wrk2-api/user-timeline/read?user_id=1&start=0&stop=1"
    return http("GET", q, timeout=5)[0] != 0 or http("GET", q, timeout=5, host_header="localhost:8080")[0] != 0


def failing(results):
    """Probes that fail or cannot be judged. The known upstream defect is not counted."""
    return sorted(r["probe"] for r in results if r["status"] in ("fail", "error"))


# ---------------------------------------------------------------- docker

def compose_cmd(args, capture=False, data=None, binary=False, check=False):
    """docker compose in the system's folder. Returns stdout (bytes if `binary`)."""
    if NO_DOCKER:
        print("  (dry) docker compose " + " ".join(args))
        return b"" if binary else ""
    r = subprocess.run(["docker", "compose"] + list(args), cwd=BASE, input=data,
                       stdout=subprocess.PIPE if (capture or binary) else None, stderr=subprocess.PIPE)
    if r.returncode != 0:
        err = r.stderr.decode("utf-8", "replace").strip()[-400:]
        if check:
            raise RuntimeError("docker compose %s failed: %s" % (" ".join(args), err))
        print("  docker compose %s failed: %s" % (" ".join(args), err), file=sys.stderr)
    if binary:
        return r.stdout
    return r.stdout.decode("utf-8", "replace") if capture else ""


def service_exec(service, args):
    """Run a command inside one container. Returns (exit code, output text)."""
    if NO_DOCKER:
        print("  (dry) docker compose exec -T %s %s" % (service, " ".join(args)))
        return 0, "(dry)"
    r = subprocess.run(["docker", "compose", "exec", "-T", service] + list(args), cwd=BASE, text=True,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out = (r.stdout or "").strip()
    if r.returncode != 0:                             # docker's own messages only when it failed
        out = (out + "\n" + (r.stderr or "")).strip()
    return r.returncode, out


def mongo(service, db, script):
    code, out = service_exec(service, ["mongo", "--quiet", db, "--eval", script])
    if code != 0 or "uncaught exception" in out or "errmsg" in out:
        raise RuntimeError("mongo on %s failed: %s" % (service, out[:400]))
    return out


def redis(service, *words):
    code, out = service_exec(service, ["redis-cli"] + list(words))
    if code != 0 or out.startswith("ERR") or "(error)" in out:
        raise RuntimeError("redis-cli %s on %s failed: %s" % (" ".join(words), service, out[:300]))
    return out


def number(text, what):
    m = re.search(r"-?\d+", text or "")
    if m:
        return int(m.group(0))
    if NO_DOCKER:
        return 0
    raise RuntimeError("no number in the answer for %s: %r" % (what, text))


def container_ip(service):
    if NO_DOCKER:
        return "(dry)"
    cid = compose_cmd(["ps", "-q", service], capture=True).strip().split("\n")[0]
    if not cid:
        return "none"
    r = subprocess.run(["docker", "inspect", "-f", "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}", cid],
                       text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    return r.stdout.strip() or "none"


def restart(services, settle):
    if not services:
        return
    say("restarting: " + ", ".join(services))
    compose_cmd(["restart"] + list(services))
    if NO_DOCKER:
        return
    time.sleep(settle)
    q = "/wrk2-api/user-timeline/read?user_id=1&start=0&stop=1"
    for _ in range(30):                               # wait until the gateway answers again
        if http("GET", q, timeout=5)[0] != 0 or http("GET", q, timeout=5, host_header="localhost:8080")[0] != 0:
            break
        time.sleep(2)
    else:
        print("  warning: the gateway did not answer after the restart", file=sys.stderr)
    rounds = warm_up()
    if rounds is None:                                # the gateway may answer only by name
        rounds = warm_up(host_header="localhost:8080", max_rounds=6)
    say("warm-up: " + ("clean after %d rounds" % rounds if rounds else "never clean; the change itself may block these requests"))


def warm_up(max_rounds=10, host_header=None):
    """After a restart, connections that other services keep open to the restarted service are
    dead, and the first calls through them fail. That is an effect of the restart, not of the
    world. Send throwaway requests until two rounds in a row are clean, so that the probes
    measure the world only. Returns the number of rounds, or None if it never got clean."""
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


def do_trigger(trig):
    """An event that happens after the change is in place, for example a deployment that
    recreates containers. It is part of the world: without it the change shows nothing."""
    watch = trig.get("watch", [])
    before = {svc: container_ip(svc) for svc in watch}
    steps = list(trig.get("compose", []))
    if trig.get("swap"):                              # recreate two services so that they trade addresses:
        def as_number(svc):                           # the one with the higher address is started first
            try:                                      # and takes the lower free address
                return [int(x) for x in before.get(svc, container_ip(svc)).split(".")]
            except ValueError:
                return [0]
        first, second = sorted(trig["swap"], key=as_number, reverse=True)
        steps = [["rm", "-sf", first, second], ["up", "-d", first], ["up", "-d", second]]
    for args in steps:
        say("docker compose " + " ".join(args))
        compose_cmd(args, capture=True)
    time.sleep(0 if NO_DOCKER else trig.get("wait_seconds", 20))
    after = {svc: container_ip(svc) for svc in watch}
    for svc in watch:
        say("%s address: %s -> %s" % (svc, before[svc], after[svc]))
    rounds = None if NO_DOCKER else warm_up()
    say("warm-up after the event: " + ("clean after %d rounds" % rounds if rounds else "never clean"))
    changed = any(before[svc] != after[svc] for svc in watch) if watch else None
    if changed is False:
        say("NOTE: no address changed, so this event tested nothing")
    return changed


# ---------------------------------------------------------------- changes in files

def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def swap_line(path, line_no, expect, new):
    """Replace line line_no (1-based) if it is exactly `expect`. Keeps the line ending.
    `new` may hold several lines separated by \\n."""
    with open(path, "r", encoding="utf-8", newline="") as f:
        lines = f.readlines()
    cur = lines[line_no - 1] if 1 <= line_no <= len(lines) else None
    if cur is None or cur.rstrip("\r\n") != expect:
        die("%s line %d is not what worlds.json says.\n  expected: %r\n  found:    %r" % (
            path, line_no, expect, None if cur is None else cur.rstrip("\r\n")))
    ending = cur[len(cur.rstrip("\r\n")):]
    lines[line_no - 1] = new.replace("\n", ending or "\n") + ending
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.writelines(lines)


def check_file_changes(world, tree):
    """Every line must be what worlds.json says, before anything is changed."""
    for c in file_changes(world):
        path = os.path.join(tree, c["file"])
        if not os.path.isfile(path):
            die("file not found: " + c["file"])
        with open(path, encoding="utf-8", newline="") as f:
            lines = f.readlines()
        found = lines[c["line"] - 1].rstrip("\r\n") if 1 <= c["line"] <= len(lines) else None
        if found != c["original"]:
            die("%s line %d is not what worlds.json says.\n  expected: %r\n  found:    %r" % (c["file"], c["line"], c["original"], found))


def write_file_changes(world, tree):
    """Line numbers refer to the clean file, so each file is changed from the bottom up."""
    check_file_changes(world, tree)
    for c in sorted(file_changes(world), key=lambda c: (c["file"], -c["line"])):
        swap_line(os.path.join(tree, c["file"]), c["line"], c["original"], c["injected"])


def applied_worlds():
    return sorted(n[:-5] for n in os.listdir(STATE) if n.endswith(".json")) if os.path.isdir(STATE) else []


def read_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def write_json(path, value):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=1)


# ---------------------------------------------------------------- store recipes
# A recipe changes a data store and leaves nothing behind inside it: no side collection, no
# renamed key. What it takes out is kept on this machine, in .work/backup/<world id>/, and put
# back by the revert. Each recipe is (apply, revert); both get the backup folder.

HEX24 = re.compile(r"^[0-9a-f]{24}$")
NEW_PASSWORD = "pw_reset_new"                       # the password probe V1 tries
NEW_SALT = "Qm3xT7vLp9ZsKd2HfYc8RwNb4JgUa6Ee"       # 32 characters, like the service's own salts
TIMELINE_REDIS = ("home-timeline-redis", "user-timeline-redis")


def unique_url_index(keep):
    """A unique index on the long URL in url-shorten-mongodb. Duplicate rows are removed first,
    because the index cannot be built over them."""
    svc, coll = "url-shorten-mongodb", 'db.getCollection("url-shorten")'
    listing = mongo(svc, "url-shorten",
                    "var seen = {}; %s.find().sort({_id: 1}).forEach(function (d) { if (seen[d.expanded_url]) { "
                    "print('DUP ' + JSON.stringify({id: d._id.str, s: d.shortened_url, e: d.expanded_url})); } "
                    "else { seen[d.expanded_url] = 1; } });" % coll)
    dups = [json.loads(line[4:]) for line in listing.splitlines() if line.startswith("DUP ")]
    if any(not HEX24.match(d["id"]) for d in dups):
        raise RuntimeError("an _id in url-shorten is not an ObjectId; stopping before any change")
    write_json(os.path.join(keep, "removed_rows.json"), dups)          # the backup, before anything is deleted
    say("%d duplicate rows saved" % len(dups))
    for i in range(0, len(dups), 200):
        ids = ", ".join('ObjectId("%s")' % d["id"] for d in dups[i:i + 200])
        mongo(svc, "url-shorten", "print('deleted=' + %s.deleteMany({_id: {$in: [%s]}}).deletedCount);" % (coll, ids))
    out = mongo(svc, "url-shorten", "printjson(%s.createIndex({expanded_url: 1}, {unique: true}));" % coll)
    say("index built: " + " ".join(out.split())[:160])


def undo_unique_url_index(keep):
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


def edit_password(keep):
    """The salt and password hash of one account, reset_target, edited directly in user-mongodb."""
    old = mongo("user-mongodb", "user",
                'var u = db.user.findOne({username: "reset_target"}); print(u ? JSON.stringify({salt: u.salt, password: u.password}) : "none");')
    line = [x for x in old.splitlines() if x.startswith("{")]
    if not line and not NO_DOCKER:
        raise RuntimeError("the account reset_target does not exist; probe V1 should have made it")
    write_json(os.path.join(keep, "old_credentials.json"), json.loads(line[0]) if line else {})
    digest = hashlib.sha256((NEW_PASSWORD + NEW_SALT).encode()).hexdigest()          # UserHandler.h: hash256(password + salt)
    out = mongo("user-mongodb", "user",
                'print("modified=" + db.user.updateOne({username: "reset_target"}, {$set: {salt: %s, password: %s}}).modifiedCount);'
                % (json.dumps(NEW_SALT), json.dumps(digest)))
    if number(out, "modified") != 1 and not NO_DOCKER:
        raise RuntimeError("expected one changed document, got: " + out)
    say("salt and password hash of reset_target replaced in user-mongodb")


def undo_edit_password(keep):
    old = read_json(os.path.join(keep, "old_credentials.json"))
    out = mongo("user-mongodb", "user",
                'print("restored=" + db.user.updateOne({username: "reset_target"}, {$set: {salt: %s, password: %s}}).modifiedCount);'
                % (json.dumps(old["salt"]), json.dumps(old["password"])))
    say("old salt and hash written back (%s)" % out.strip())


def empty_timeline_redis(keep):
    """Both timeline Redis stores emptied. Every backup is taken before the first flush."""
    sizes = {}
    for svc in TIMELINE_REDIS:
        sizes[svc] = number(redis(svc, "DBSIZE"), "DBSIZE")
        redis(svc, "SAVE")
        compose_cmd(["cp", "%s:/data/dump.rdb" % svc, os.path.join(keep, svc + ".rdb")], check=True)
        if not NO_DOCKER and os.path.getsize(os.path.join(keep, svc + ".rdb")) < 20:
            raise RuntimeError("the backup of %s is empty; nothing was changed" % svc)
        say("%s: %d keys, backup copied out" % (svc, sizes[svc]))
    write_json(os.path.join(keep, "sizes.json"), sizes)
    for svc in TIMELINE_REDIS:
        redis(svc, "FLUSHALL")
        say("%s emptied" % svc)


def undo_empty_timeline_redis(keep):
    sizes = read_json(os.path.join(keep, "sizes.json"))
    for svc in TIMELINE_REDIS:
        compose_cmd(["stop", svc], check=True)        # on stop Redis writes its file; ours goes in after that
        compose_cmd(["cp", os.path.join(keep, svc + ".rdb"), "%s:/data/dump.rdb" % svc], check=True)
        compose_cmd(["start", svc], check=True)
    if not NO_DOCKER:
        time.sleep(5)
    for svc in TIMELINE_REDIS:
        now = number(redis(svc, "DBSIZE"), "DBSIZE")
        say("%s: %d keys now, %d before the change%s" % (svc, now, sizes[svc], "" if now >= sizes[svc] else "   <-- FEWER THAN BEFORE, CHECK BY HAND"))
    rounds = None if NO_DOCKER else warm_up()
    say("warm-up: " + ("clean after %d rounds" % rounds if rounds else "never clean"))


def delete_old_posts(keep):
    """Every post written before this moment deleted from post-storage-mongodb."""
    svc = "post-storage-mongodb"
    if not NO_DOCKER and service_exec(svc, ["which", "mongodump"])[0] != 0:
        raise RuntimeError("mongodump is not in the %s container, so the posts cannot be backed up" % svc)
    before = number(mongo(svc, "post", "print(db.post.count());"), "post count")
    dump = compose_cmd(["exec", "-T", svc, "mongodump", "--quiet", "--db", "post", "--collection", "post", "--archive"], binary=True, check=True)
    with open(os.path.join(keep, "post.archive"), "wb") as f:
        f.write(dump)
    if not NO_DOCKER and len(dump) < 100:
        raise RuntimeError("the dump of the post collection is empty; nothing was changed")
    write_json(os.path.join(keep, "count.json"), {"before": before})
    say("%d posts, dump of %d bytes saved" % (before, len(dump)))
    out = mongo(svc, "post", 'var cut = new Date().getTime(); print("removed=" + db.post.deleteMany({timestamp: {$lt: cut}}).deletedCount);')
    say("posts deleted (%s)" % out.strip())


def undo_delete_old_posts(keep):
    svc = "post-storage-mongodb"
    with open(os.path.join(keep, "post.archive"), "rb") as f:
        dump = f.read()
    # documents that still exist are reported as duplicates and skipped; the deleted ones are inserted
    compose_cmd(["exec", "-T", svc, "mongorestore", "--quiet", "--archive"], data=dump, binary=True, check=True)
    before = read_json(os.path.join(keep, "count.json"))["before"]
    now = number(mongo(svc, "post", "print(db.post.count());"), "post count")
    say("%d posts now, %d before the change%s" % (now, before, "" if now >= before else "   <-- FEWER THAN BEFORE, CHECK BY HAND"))


RECIPES = {
    "unique_url_index": (unique_url_index, undo_unique_url_index),
    "edit_password": (edit_password, undo_edit_password),
    "empty_timeline_redis": (empty_timeline_redis, undo_empty_timeline_redis),
    "delete_old_posts": (delete_old_posts, undo_delete_old_posts),
}


# ---------------------------------------------------------------- evidence

def save_evidence(out_dir, since_iso, start_epoch):
    """The logs of every service since the world was applied, and the request traces."""
    shutil.rmtree(out_dir, ignore_errors=True)
    os.makedirs(os.path.join(out_dir, "logs"))
    if NO_DOCKER:
        return
    time.sleep(12)                                    # services send their spans every 10 seconds
    for name in compose_cmd(["ps", "--services"], capture=True).split():
        text = compose_cmd(["logs", "--no-color", "--no-log-prefix", "--since", since_iso, name], capture=True)
        with open(os.path.join(out_dir, "logs", name + ".log"), "w", encoding="utf-8") as f:
            f.write(text or "")
    q = "/api/traces?service=nginx-web-server&limit=1500&start=%d&end=%d" % (int(start_epoch * 1_000_000), int(time.time() * 1_000_000))
    with open(os.path.join(out_dir, "traces.json"), "w", encoding="utf-8") as f:
        f.write(http("GET", q, host=JAEGER)[2])


# ---------------------------------------------------------------- apply, revert, measure

def apply(world):
    """Apply a world and leave it live. Returns what was measured:
    {"before": probes failing on the clean system, "live": probes failing now, "event_had_effect": bool or None}."""
    wid = world["id"]
    if applied_worlds():
        die("a world is already applied: %s. Revert it first." % ", ".join(applied_worlds()))
    unknown = [c for c in world["changes"] if not ("file" in c or "service" in c or c.get("recipe") in RECIPES)]
    if unknown:
        die("change not understood: %r" % unknown[0])
    check_file_changes(world, BASE)
    recipes = [c["recipe"] for c in world["changes"] if "recipe" in c]
    commands = [c for c in world["changes"] if "service" in c]
    keep = os.path.join(BACKUP, wid)
    if recipes and os.path.isdir(keep) and os.listdir(keep):
        die("%s is not empty: the backup of an earlier apply is still there. Revert that first, or move it away." % keep)
    extras = world.get("extra_probes", [])
    if not NO_DOCKER and not system_answers():
        die("the system does not answer on port %d. Start Docker, run `docker compose up -d` in %s, and try again. Nothing was changed." % (GATEWAY[1], BASE))

    print("[1/4] baseline probes (they also create the accounts this world needs)")
    before = failing(run_probes(extras))
    say("failing before the change: %s" % (", ".join(before) or "none"))
    since, start = now_iso(), time.time()

    print("[2/4] applying %s" % wid)
    files = sorted({c["file"] for c in file_changes(world)})
    os.makedirs(os.path.join(STATE, wid + ".files"), exist_ok=True)
    for n, f in enumerate(files):                     # a copy of every file before it is touched
        shutil.copy2(os.path.join(BASE, f), os.path.join(STATE, wid + ".files", str(n)))
    write_json(os.path.join(STATE, wid + ".json"), {"id": wid, "applied_at": since, "files": files,
                                                    "sha_before": {f: sha(os.path.join(BASE, f)) for f in files}})
    try:
        for name in recipes:
            os.makedirs(keep, exist_ok=True)
            RECIPES[name][0](keep)
        for c in commands:
            code, out = service_exec(c["service"], c["apply"])
            say("ran on %s: %s" % (c["service"], out[:200].replace("\n", " ")))
            if code != 0 or any(mark in out for mark in c.get("fail_if", [])):
                raise RuntimeError("command on %s failed: %s" % (c["service"], out[:400]))
    except Exception as err:
        print("STOP: %s\nNothing in the files was changed. Undo the store part with: world.py revert %s" % (err, wid), file=sys.stderr)
        sys.exit(1)
    write_file_changes(world, BASE)
    for f in files:
        say("changed " + f)
    restart(world.get("restart", []), world.get("settle_seconds", 15))
    effect = do_trigger(world["trigger"]) if world.get("trigger") else None

    print("[3/4] probes with the world live")
    live = failing(run_probes(extras))
    say("failing now: %s" % (", ".join(live) or "none"))
    changed = sorted(set(live) ^ set(before))
    say("changed by this world: %s   (worlds.json says: %s)" % (", ".join(changed) or "none", ", ".join(world.get("probes_changed", [])) or "none"))

    print("[4/4] saving logs and traces")
    save_evidence(os.path.join(EVIDENCE, wid), since, start)
    print("%s is LIVE. Undo it with: world.py revert %s" % (wid, wid))
    return {"before": before, "live": live, "event_had_effect": effect}


def revert(world):
    """Undo a world. Returns the probes that fail afterwards."""
    wid = world["id"]
    spath = os.path.join(STATE, wid + ".json")
    if not os.path.isfile(spath):
        die("world %s is not applied (no state file)" % wid)
    state = read_json(spath)
    keep = os.path.join(BACKUP, wid)
    print("reverting %s" % wid)
    for c in reversed(world["changes"]):
        if "recipe" in c and os.path.isdir(keep) and os.listdir(keep):
            RECIPES[c["recipe"]][1](keep)
        elif "service" in c:
            say("undone on %s: %s" % (c["service"], service_exec(c["service"], c["revert"])[1][:200].replace("\n", " ")))
    for n, f in enumerate(state["files"]):            # put back the saved copies
        shutil.copy2(os.path.join(STATE, wid + ".files", str(n)), os.path.join(BASE, f))
        say("restored " + f)
    for path, digest in state["sha_before"].items():
        if sha(os.path.join(BASE, path)) != digest:
            die("%s is not identical to its state before the change. Check it by hand." % path)
    os.remove(spath)
    shutil.rmtree(os.path.join(STATE, wid + ".files"), ignore_errors=True)
    if os.path.isdir(keep):                           # the backup is kept, out of the way
        done = os.path.join(BACKUP, "_done", "%s-%d" % (wid, int(time.time())))
        os.makedirs(os.path.dirname(done), exist_ok=True)
        shutil.move(keep, done)
    restart(world.get("restart", []), world.get("settle_seconds", 15))
    print("probes after the revert")
    left = failing(run_probes(world.get("extra_probes", [])))
    say("failing after the revert: %s" % (", ".join(left) or "none"))
    return left


def measure(world):
    """Apply, revert, and write the result into worlds.json. This is how a draft becomes a measured world."""
    got = apply(world)
    after = revert(world)
    changed = sorted(set(got["live"]) ^ set(got["before"]))
    clean = after == got["before"]
    status = "event had no effect" if got["event_had_effect"] is False else "measured"
    update_world(world["id"], {"status": status, "probes_changed": changed, "measured_at": now_iso(), "clean_after_revert": clean})
    print("\nRESULT for %s: probes changed: %s   clean again after the revert: %s" % (world["id"], ", ".join(changed) or "none", clean))
    print("worlds.json updated. If the symptom is new, write the ticket from it, not from what was expected.")
    return clean


def main(argv):
    cmd = argv[1] if len(argv) > 1 else ""
    if cmd == "probe":
        print("\nfailing: " + (", ".join(failing(run_probes(argv[2:]))) or "none"))
    elif cmd == "status":
        print("applied worlds: " + (", ".join(applied_worlds()) or "none"))
    elif cmd in ("apply", "revert", "measure") and len(argv) == 3:
        {"apply": apply, "revert": revert, "measure": measure}[cmd](get_world(argv[2]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
