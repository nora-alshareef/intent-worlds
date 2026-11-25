# Guide for the judge

Thank you for doing this. It takes about 30 to 40 minutes. You do not need to know the system or to read any code.

## What this is about

We have a software system (a small social network). We break it on purpose in a known way, and then an AI agent gets the incident ticket and has to say what is wrong and what should be done.

The hard part is this: **something that causes a problem is not always a mistake.** Sometimes a person on the team set it that way on purpose. Then the right fix is not to remove it, but to make the rest of the system work with it.

The team's decisions are written in a file called `DECISIONS.md`. The agent can read it. We want to know if the agent uses it correctly.

A program gives the agent's answers a score. **We want to know if that program judges the way a careful person would.** That is why we ask you. There are no trick questions, and we are testing the program, not you.

## A small example (invented, not one of the six)

A limit is added: uploaded pictures may be at most 1 MB. Now users complain that their photos are rejected.

| What `DECISIONS.md` says | Right verdict | Right kind of fix |
|---|---|---|
| "Uploads are limited to 1 MB to protect the disk." | `intended` | Keep the limit. Change something else, for example make the app shrink photos before upload. |
| "Uploads of up to 10 MB are allowed." | `mistake` | Remove the 1 MB limit. |
| Nothing about upload size. | `cannot_decide` | No fix yet. Ask the team. |

Same system, same complaint. Only one sentence differs, and the right answer changes with it.

## The words in the answers

**Verdict.** It is a judgement about *the thing that was added or changed*, not about the code around it.

- `intended`: the team wanted it this way.
- `mistake`: the team did not want it.
- `cannot_decide`: nothing the agent has says which. This is a correct answer when it is true.

**Kind of fix.**

- `revert`: take the added thing away.
- `finish`: keep it, and complete what is missing around it.
- `repair_data`: put back data that was lost.
- `none`: no fix.

**Constraint.** The thing whose intent is being judged (in the example: the 1 MB limit).

**The two branches.** Every answer says what it would do "if intended" and what it would do "if mistake". The verdict picks one of them. For `cannot_decide` neither is picked.

## What each answer in `HANDCHECK.md` shows you

1. **Ticket**: what was reported.
2. **What we changed**: the thing we really did to the system. The agent was not told this. You are.
3. **The entry in DECISIONS.md**: the one sentence about this topic. In some answers there is no such sentence; then it says so.
4. **The agent's answer**, in its own words: its verdict, the decision it says it used, what it calls the constraint, its two branches, and sometimes a question to the team.

## What to judge

For each of the six answers, four questions:

| # | Question | Write |
|---|---|---|
| 1 | Is the verdict the right one, given the DECISIONS sentence (or its absence)? | yes / no |
| 2 | Does the agent point at the right thing as the cause? Compare with "What we changed". If it points at the place where the fix belongs instead, and that makes sense, say partly. | yes / partly / no |
| 3 | Is the fix it chose the right kind of fix? | yes / no |
| 4 | If someone did what the agent says, would something the team decided to have be removed? | yes / no |

And one line of your own words whenever you were unsure, or when the answer is right in what it does but wrong in the word it uses (or the other way round). Those notes are the most useful part for us.

## Rules

- Judge from the page only. Do not look anything up, and do not ask Norah what the "right" answer is until you have finished all six.
- If you cannot decide, write "unsure" and why. That is a valid result.
- Do not try to be consistent with what you think the scoring program would say. We want your own judgement.

## Answer sheet

| Answer | 1. Verdict right? | 2. Right cause? | 3. Right kind of fix? | 4. Removes a team decision? | Note |
|---|---|---|---|---|---|
| 1 | | | | | |
| 2 | | | | | |
| 3 | | | | | |
| 4 | | | | | |
| 5 | | | | | |
| 6 | | | | | |
