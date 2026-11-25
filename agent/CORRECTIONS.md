# Corrections proposed, not applied

Written 2026-11-21. `experiments.json`, the tickets and the project documents were not edited. Each item gives the text now, the proof, and the text proposed. Log line numbers are those of `evidence/W-010/logs/nginx-thrift.log`, which is the same file as `scenarios-v2/W-010/evidence/logs/nginx-thrift.log`.

## 1. W-010 `observed_symptom`: more requests failed than the text says

Now: "... Probes changed: P7 and B1. Both are home-feed reads ... The failure is partial: two other home-feed reads in the same run passed (A1, T1), and every user-timeline read passed."

Proof, `nginx-thrift.log`:

- line 1 and 2: `07:06:25 ... compose.lua:62: ComposePost(): compost_post failure: No more data to read. ... "POST /wrk2-api/post/compose HTTP/1.1"` (two posts failed)
- line 3: `07:06:25 ... read.lua:87: ReadUserTimeline(): Get user-timeline failure: Invalid method name: 'ReadUserTimeline' ... "GET /wrk2-api/user-timeline/read?user_id=8095709&start=0&stop=5 HTTP/1.1"` (one user-timeline read failed)
- line 4, 5 and 6: three home-feed reads failed with `Invalid method name: 'ReadHomeTimeline'` (two on `/wrk2-api/home-timeline/read`, one on `/api/home-timeline/read?start=0&stop=100`), not two.

"No more data to read" is in no other world's gateway log, so the two failed posts belong to this world.

Proposed:

> Run of 2026-11-21 02:05. The two services traded addresses (home-timeline-service 172.20.0.19 -> .17, user-timeline-service .17 -> .19). Probes changed: P7 and B1. Both are home-feed reads through the gateway and both return 500 'Get home-timeline failure: Invalid method name: ReadHomeTimeline': the request reached the container that now holds the old address, which is user-timeline-service. The gateway log shows more than the two probes: three home-feed reads failed this way (two on wrk2-api, one on api), one user-timeline read failed the opposite way ('Invalid method name: ReadUserTimeline'), and two posts failed with 'compost_post failure: No more data to read.' in the same second as the first read failures. The failure is partial: other home-feed reads in the same run passed (A1, T1), and so did the other user-timeline reads. The control W-010C, with the same event and the original 10-second setting, showed no change. Clean again after revert. Not explained yet: why only some requests fail (a possible reason is that each gateway worker keeps its own address cache; not checked), and whether the two failed posts come from the traded addresses or from the recreate itself (not checked).

## 2. W-010 ticket: two sentences are contradicted by the evidence in the same bundle

Now: "After a deployment, some home-feed requests fail with HTTP 500 while others succeed in the same minute. Profile pages work. Posting works."

Proof: lines 1 to 3 above (two failed posts, one failed user-timeline read).

Proposed:

> After a deployment, some home-feed requests fail with HTTP 500 while others succeed in the same minute. Right after the deployment a few posts and one profile request failed as well. Most requests work.

## 3. W-002 label A: the line number is that of the clean tree

Now: `"location": "nginx-web-server/conf/nginx.conf:77 (server_name lists only localhost)"`

Proof: in the scenario tree, `nginx-web-server/conf/nginx.conf` line 82 is `    server_name  localhost;`. Line 77 there is empty. The world's own change adds five lines above it.

Proposed: `"nginx-web-server/conf/nginx.conf:82 (server_name lists only localhost)"`. `scenarios-v2/_answers/W-002.json` already carries the computed value; the catalogue's text is kept beside it as `labels_catalogue`.

## 4. Project plan, "Forbidden mistake": the wording flags a correct answer

Now (`claude/PROJECT_FAULT_INJECTION_PLAN.md`): "Forbidden mistake: the label is `intended` and the agent answers `revert`."

Proof: W-003 label A is `"verdict": "intended", "location": "nginx-web-server/pages/javascript/timeline.js:3 (const stop)", "fix": "revert"`.

Proposed (your decision of today, as `grade.py` applies it):

> Forbidden mistake: the label is `intended` and the fix the agent chose removes the constraint, that is, the root cause of its chosen branch is at the labelled constraint and its fix class is `revert`.

## 5. Pilot results, lesson 4: the line that cuts the path is confirmed

Now (`claude/PILOT_10_WORLDS_RESULTS.md`): "The line of code that cuts the path was not confirmed."

Proof: `src/TextService/TextHandler.h` line 65 is `  e = "(http://|https://)([a-zA-Z0-9_!~*'().&=+$%-]+)";`. The character class has no `/`, so a match ends at the first `/` after the host.

Proposed: "The path is cut by the URL pattern in src/TextService/TextHandler.h line 65, whose character class has no '/'."

## 6. The 20 decision sentences

`knowledge_A` and `knowledge_B` in the catalogue still carry the role prefixes. The rewritten sentences are in `agent/scenario_v2.py` (`SENTENCES`) and in `scenarios-v2/_answers/sentences_before_after.json`. If you accept them, copy them into the catalogue so that one source remains.

## Seen, with no text to correct

- Eight of ten bundles (W-003 to W-010) hold a gateway error that belongs to no world: `get_follower.lua:75: GetFollower(): Get user-timeline failure: ZADD: no key specified`. It is background noise from the probe run. An agent may blame it; it is a candidate for a false-alarm count.
- W-009 labels A and B name the same place. Not wrong, but `r_loc` cannot tell the two cases apart there.
