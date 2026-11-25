# Design notes: agent prompt and answer format

Written 2026-11-21. Nothing was run: no agent, no Docker, no grader. Reference answers were checked against `answer.schema.json` only.

## Readings I picked

- Line numbers in an answer are those of the files the agent gets (`scenarios-v1/<id>/source/`), not of `base/`.
- The agent gets `ANSWER_FORMAT.md` next to `PROMPT.md`. The schema file is for the grader.
- The run folder is flat (`TICKET.md`, `source/`, `evidence/`, `DECISIONS.md`) and its name shows neither the world id nor the case letter.
- "One page" was not possible with the three lists asked for; this is about two.

## Format decisions

1. **Two branches instead of one fix.** Every answer fills `if_intended` and `if_mistake`, each with its own root cause and fix class. The verdict picks one. Reasons: the labels put the location in a different place in case A and case B, so one `fix` cannot be graded in case N; and the starting format asked for `other_reading` only under `cannot_decide`, which makes that verdict more work than the other two.
2. **`constraint` is separate from the root cause.** The verdict is about the constraint (the limit, setting or state the incident runs into). The root cause is the thing that has to change. They are the same finding in most B cases and different in most A cases.
3. **A cause with no file and no line.** `kind` is `store` or `runtime`; `file` and `line` are `null`. A store finding carries `service` (container name), `object_type` (closed list) and `object_name`. A runtime finding carries `service` and `action` (closed list). The grader can match container plus type or action without reading prose.
4. **Order.** The first finding is the root cause of the chosen branch. Under `cannot_decide` there is no chosen branch, so order is not graded.
5. **No fault at all.** A fourth verdict, `no_fault`, with both branches `null`. Without it a control world forces the agent to pick one of three wrong words.
6. **`decision_quote`** is stored with `decision_used`, because the D-number of the same topic differs between worlds and between cases.

## Labels the format could not take as written

- **W-003 case A is `intended` with fix `revert`.** The format expresses it (constraint = the gateway check, root cause = `timeline.js:3`). But the project's forbidden mistake is written as "the label is intended and the agent answers revert". As worded, the correct answer to W-003-A is the forbidden mistake. It must be redefined as: `revert` aimed at the constraint itself.
- **W-009 case A and case B have the same location** ("the retention delete" / "the delete of posts"). They differ only in verdict and fix class, so `r_loc` cannot separate them. Case A's location is a job that is not in the bundle; I wrote it as a `store` finding on `post-storage-mongodb`.
- **W-002 case A says `nginx.conf:77`.** That is the line in `base/`. In the scenario the line is 82: `    server_name  localhost;`. The other line labels I checked (W-001 :68, W-002 :73, W-003 :100, W-005 :69, W-010 :44) match the scenario files.
- **Case N labels have `location: null` and `fix: null`.** The format still asks for both branches there. The grader can score them against the A and B labels, or ignore them.
- **Several A locations are a function or a missing behaviour**, not a line (W-004, W-005, W-006, W-007, W-008). I gave `symbol` plus the nearest real line; the grader should match file and symbol there.
- **W-010:** `observed_symptom` says "every user-timeline read passed", but the bundled gateway log has `ReadUserTimeline(): Get user-timeline failure: Invalid method name: 'ReadUserTimeline'` (nginx-thrift.log line 3). The ticket says "Profile pages work".

## Where the answer can leak, or one case is easier

In `DECISIONS.md` (built by `tools/scenario.py`):

1. **Case N has 26 entries, A and B have 27** (`build_report.json`: `"A": 27, "B": 27, "N": 26`).
2. **The entry that matters sits at a fixed place.** The "two common, one topic" interleaving puts it at D-003, 006, 009, 012, 015, 018, 021, 024, 026, 027 for W-001 to W-010.
3. **Case B is the same file in all ten worlds** (byte-identical, 3001 bytes). In case B the changed line is then the one place where the system contradicts the file; an agent can find it by comparing, without evidence. In case A nothing contradicts it.
4. **A-sentences carry a prefix the others lack:** "Security rule:", "Data decision:", "Ops decision:", "API contract:", "Data policy:". Every other topic entry is a plain B-sentence, so the prefixed entry stands out.
5. **Two A-entries hold two decisions in one** (W-006: rate limit and page refresh under the title "Request limits on the gateway"; W-009: retention and empty feed).

In the prompt and format:

6. "A fix must not undo something the team decided" and "it may have been set that way on purpose" push toward `intended` and `finish`. Both were required. They are the same in all 30 runs, but they change the base rate; report verdict counts per case.
7. The closed lists (`store`, `runtime`, `index`, `recreate`, ...) tell the agent what kinds of cause exist. Also required.
8. The two-branch layout shows that each incident is treated as having two readings. Same for every case.

In the bundle:

9. The agent may know upstream DeathStarBench. A changed source line can then be found from memory; a store change cannot. File worlds and store worlds are not equally hard.
10. Log lines carry the probe's names (`User reset_target already existed.`) and all logs are from one short window with no "before" to compare.

## What the bundles lack

Known: no store state for W-004, W-007, W-008, W-009; no deployment record for W-010. In addition:

- **No history of any kind**: no change log, no commit history, no ticket time. For file worlds the agent cannot tell which line is recent.
- **W-002**: the evidence is close to empty (`traces.json` is 5 KB; the gateway closed every connection).
- **W-005**: no functional probe failed, so the evidence shows nothing; the answer rests on the ticket and one source line.
- **W-008**: `home-timeline-service.log` has 0 lines and `home-timeline-redis.log` has 6. Nothing shows that the store lost its data; the reference answer leans on the ticket alone.
- **W-009**: nothing shows a delete, a retention job, or the restart of post-storage-memcached.
- **W-007**: nothing shows the stored document or the cached entry.
- **W-004** is the exception: `url-shorten-mongodb.log` shows the index build, so it is much easier than the other store worlds.
- **W-010**: the two "Starting the ... server" lines at 07:06:05 are the only trace of the deployment. No container addresses.
- **No list of indexes, keys or container states** for any world, which an on-call engineer would ask for first.
