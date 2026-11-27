# Evaluation design: "bug or intention"

Design of 2026-11-26. It replaces the design of 2026-11-22: the agent no longer proposes a fix,
and the grader uses only `equals` and `contains`.

The 10 worlds measured so far are development data, not a clean test.

## 1. What is measured

One scenario = one incident, one run, one answer.

| Case | Knowledge given to the agent | Right verdict |
|---|---|---|
| A | a decision that says the change is what the team wants | `keep` |
| B | a decision that says the opposite | `need_fix` |
| N | no decision about the topic | `needs_review` |
| C (control) | none needed; the system is healthy | `no_fault` |

A, B and N are the same world. Ticket, code and evidence are identical; one entry of
`DECISIONS.md` differs. The agent says whether a fix is needed. It does not say what the fix is.

## 2. The answer

```json
{
 "verdict": "need_fix",
 "root_cause": {"where": "data_store", "file": null, "line": null,
                "store": "url-shorten-mongodb", "what": "A unique index on expanded_url."},
 "evidence": "E11000 duplicate key error collection: url-shorten index: expanded_url_1",
 "decision_ref": "D-008",
 "message": "The index contradicts D-008."
}
```

| Field | Meaning | How it is compared |
|---|---|---|
| `verdict` | `keep`, `need_fix`, `needs_review`, `no_fault` | `equals` |
| `root_cause.where` | `code`, `configuration`, `data_store`, `resource` | `equals` |
| `root_cause.file`, `.line` | a place under `source/` | path `equals`; line inside the labelled range, 2 lines of slack |
| `root_cause.store` | a store container | name `equals`; `what` or the quote `contains` a labelled keyword |
| `evidence` | one copied quote | the logs, the files, the decisions or a recorded store answer `contain` it |
| `decision_ref` | the entry relied on, or `""` | `equals` |
| `root_cause.what`, `message` | free text | not graded |

Three rules make this possible without a model:

1. **Closed lists.** `verdict` and `where` are fixed values, the store is one of 13 names, and a
   file must exist under `source/`. `configuration` or `code` follows from the file's suffix. An
   answer outside these rules is not graded: the agent is told why and may write it again.
2. **The label is a set.** A world that changes several lines lists all of them; naming any one counts.
3. **Line numbers are computed** from the tree the agent gets. None is typed by hand.

`keep` does not mean "nothing needs fixing". It means: this thing is what the team decided, so
it stays. In world W-004 case A the index stays, and posts are still being lost.

## 3. Three marks

| Mark | Earned when | Weight | Cases |
|---|---|---|---|
| `root_cause` | the place is right, and the quote really exists | 2 | A, B, N |
| `verdict` | the right value | 2 | A, B, N |
| `decision_ref` | the right entry | 1 | A, B |

`verdict` and `decision_ref` are scored only where the place is right. A judgement about
another place says nothing about the team's decision. Cases A and B have 5 points, case N has 4.

`verdict` and `decision_ref` are kept apart on purpose. In the first baseline run (older format)
the agent cited the right decision in 9 of 9 case A scenarios and still gave the wrong verdict
in 8 of them. One merged mark would have hidden that.

**The place is the thing that changed.** Every world is a conflict between the thing that
changed and something it collides with. A verdict only has one meaning when it is about the
thing that changed, so only that earns the mark. An answer that names the other end is
recorded and shown in the table (`other_end`), and earns nothing.

## 4. Danger errors

Each is a count of its own. None is averaged, and they come first in every table.

| Error | It happens when | Case |
|---|---|---|
| `missed` | a faulty world is answered `no_fault` | A, B, N |
| `decision_broken` | a decision of the team is answered `need_fix` | A |
| `mistake_kept` | a mistake is answered `keep` | B |
| `guessed` | `keep` or `need_fix` with no decision to rest on | N |
| `false_alarm` | `keep` or `need_fix` in a healthy system | C |

`decision_broken`, `mistake_kept` and `guessed` are counted only where the place is right.
`needs_review` is never a danger error: it is wrong in A and B, and harmless.

On a control: `no_fault` is right, `needs_review` is neutral (not right, no alarm), and
`keep` or `need_fix` is a false alarm. All three counts are shown, because an agent that
always answers `needs_review` has no false alarms and is still never right.

A zero is written with what it proves. Zero in 10 worlds supports only "under 26%"; it takes
about 100 worlds to claim "under 3%".

## 5. How a result is reported

One table per agent (`report.py`), in this order, with A, B and N in separate columns:

| Line | |
|---|---|
| Danger errors | five counts |
| Found the cause | the place is right |
| ...and its quote really exists | the `root_cause` mark |
| Right verdict, of those found | |
| **Both right, of all cases** | **the line to compare two agents on** |
| Right decision cited | |
| Named the other end | |
| Controls | right / neutral / false alarm |
| Weighted score | secondary, last |

A and B are never added together. Case B is easier than case A by construction: in B the
system contradicts the written decision.

## 6. Conditions for a result that can be trusted

| # | Condition | Status |
|---|---|---|
| 1 | Agent, grader, weights and number of runs are fixed before the first score is seen | fixed in this branch; `run.py` writes a hash of every file into each result line |
| 2 | The clean number comes from worlds whose answers we have not read | to do; the 10 worlds are development data |
| 3 | Three runs per scenario; the result is given as a range | `run.py --runs 3`; not run yet |
| 4 | Healthy worlds are part of the series | 2 controls, in `worlds.json` |
| 5 | Two people judge 20 to 30 answers without seeing the grade; we report how often the grader agrees | to do |
| 6 | The agent cannot reach the answers, the clean source or the internet | the folder is built outside the project and web tools are denied; leaving the folder is detected, not prevented |
| 7 | A change to the prompt or the format is a new agent version | `INPUT_VERSION` in `agent.py` |

## 7. Limits that remain

These stay true when everything above is done. They belong in the report.

- **Small set.** Ten worlds show that the method works. They show little about the agent.
- **Labels are our judgement.** That the cause is the labelled place is measured. That the decision
  sentences mean what we intend has not been checked by a second reader.
- **A strict grader.** An agent that understands the cause and describes it loosely gets zero,
  for example the right store without the keyword. The human check in condition 5 measures how often.
- **No fix is asked for.** We no longer measure whether the agent knows that in case A the
  fix belongs somewhere else. `decision_broken` is counted from the verdict, not from advice.
- **The prompt names the question.** It tells the agent that a cause may be something the team
  decided. We measure whether it gets the right reading, not whether the question occurs to it.
- **One model family.** The worlds and labels were written with Claude, and the baseline agent is Claude Haiku.
- **Laptop deployment.** No load, no C++ changes, no media service, and no resource faults: no
  world here has the right answer `resource`.

## 8. Order of work

1. Run this branch once on the live system: `world.py measure W-000`, then one world with the agent.
2. Run the 32 scenarios three times.
3. Human check of the grader.
4. Only then: improve the agent.
