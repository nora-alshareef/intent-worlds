# Bug or intention

An evaluation for a diagnosis agent on a microservice system (DeathStarBench socialNetwork).
The question it asks: when something breaks, can the agent tell a mistake from something the
team decided to have?

Part of the 6.S978 project "LLM Agents for Distributed Application Debugging". This is the
"last mile" track. This branch (`slim`) is the whole pipeline in 8 Python files; the `main`
branch holds the history and the earlier, larger code.

## The idea in one example

Ticket: "Some posts disappear. All of them contain a link."
Root cause: a unique index on the long URL in the url-shorten database.
Whether that index is a mistake depends on what the team decided, and only on that:

| Case | The decisions file says | Right verdict |
|---|---|---|
| A | "The url-shorten store keeps one row for each distinct long URL." | `keep` |
| B | "The store keeps one row for every link in every post." | `need_fix` |
| N | nothing about this store | `needs_review` |

Ticket, code, logs and data are identical in the three cases. One entry of `DECISIONS.md`
differs. An agent that answers the same in all three is not reading the team's intent.

There are also control worlds: a harmless event, a ticket, and nothing wrong. The right
verdict there is `no_fault`.

## Files

| File | What it is |
|---|---|
| `worlds.json` | The 10 measured worlds and 2 controls: the change, the ticket, the two decision sentences, the labels |
| `decisions.json` | The 17 team decisions that are the same in every scenario. Each measured world adds one, so `DECISIONS.md` has 27 entries |
| `ideas.csv` | 100 fault ideas. 10 are measured worlds; 90 are ideas only, never injected |
| `world.py` | Applies one world to the running system, measures it with probes, undoes it |
| `scenario.py` | Builds the folder the agent gets, and the key the grader uses |
| `store.py` | Read-only, limited queries on the data stores, for the agent |
| `agent.py` | The baseline agent. Its prompt and the answer format are text inside this file |
| `grade.py` | The grader: `equals` and `contains` only, no model |
| `run.py` | Apply a world, run the agent on each case, grade, save, undo |
| `report.py` | The result table |
| `test_all.py` | 22 tests. No Docker and no model needed |
| `.env.example` | The template for `.env`: your own API key and the model name. `.env` is never committed |
| `EVAL_DESIGN.md` | What is measured, and what a result does and does not show |

## What has been checked, and what has not

| | Status |
|---|---|
| The 10 worlds and 2 controls | Measured on the running system, 2026-11-21, on the `main` branch |
| `test_all.py` | 22 of 22 pass |
| Decisions files and source trees | Identical to the ones the first baseline ran on (compared file by file) |
| The whole pipeline with a stand-in agent, no Docker | Runs end to end |
| **This branch on the running system** | **Not run yet.** `world.py` is the earlier tested code, reorganised. Run `python3 world.py measure W-000` first |
| **The agent with this prompt and format** | **Not run yet.** The numbers on `main` are from an older answer format and are not comparable |

## Setup

1. Docker with at least 16 GB of memory.
2. Put a clone of DeathStarBench in `base/`, at commit `6ecb09706140f8730b5385c08f1386c654c3c526`,
   with the team's deployment patch (`0001-eval-deployment.patch`) applied. Or set `INTENT_BASE`
   to the `socialNetwork` folder of an existing clone.
3. In `base/socialNetwork`: `docker compose up -d`. The `media-frontend` container may fail to
   start; nothing here needs it.
4. Seed the data: `python3 scripts/init_social_graph.py --graph=socfb-Reed98` (962 users).
5. `python3 world.py probe` should report no failing probe.
6. The agent runs through the `claude` program (Claude Code). Copy `.env.example` to `.env` and fill in your
   own API key and the model name. `.env` stays on your machine. With no key in it, the Claude login of
   the machine is used. Every result line records the model and which of the two paid for it.

## Use

```
python3 test_all.py                      # the tests
python3 scenario.py decisions W-004 A    # the decisions file of one case
python3 scenario.py build W-004 A out/   # what the agent gets, built without Docker
python3 world.py measure W-000           # apply, measure, undo one world
python3 run.py W-004 --tag try1          # one world, cases A B N, one run each
python3 run.py all --runs 3 --tag v3     # the full series: 32 scenarios, three times
python3 report.py v3 --lines             # the table
```

A world stays applied only while `run.py` or `world.py apply` is working on it, and is always
reverted, also after a failure. `python3 world.py status` says whether one is applied.
Worlds W-004, W-007, W-008 and W-009 change data in the stores. Their backups are kept in
`.work/backup/`. Run them while someone is watching.

## From an idea to a world

1. Pick a row of `ideas.csv` that needs no image rebuild.
2. Add an entry to `worlds.json` with `"status": "draft"`: the exact change, and the two
   decision sentences. No ticket yet.
3. `python3 world.py measure <id>`. It writes the probes that changed into `worlds.json`.
4. Write the ticket from what was measured, in a user's words, with no hint of the cause.
   If the measurement differs from what the idea expected, the ticket follows the measurement.
5. Add the labels (`root_cause`) and set the status to `measured`.

Every measured world adds an entry to `DECISIONS.md` of every scenario. Results from before
and after a world is added are therefore not comparable.

## Keys and answers

No key is in this repository: each person keeps theirs in `.env`, which git ignores.

The answers are a different matter. `worlds.json` holds the labels of the evaluation. While the
repository is public, a model or an agent can find them, so results on these 10 worlds are
development results only. Keep it private if you want them to mean more.
