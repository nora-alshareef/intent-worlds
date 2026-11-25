# Open issues in the eval (nothing here is applied)

Collected while the baseline `baseline-cc-v0` is frozen. Each one is a possible change to the eval, for Norah to decide
after the repeats. None of them changes the agent.

## From the hand check of 2026-11-22 (one judge, six answers)

Agreement of the judge with `grade.py`:

| Question | Agree | Where they differ |
|---|---|---|
| Verdict | 6 of 6 | |
| Forbidden | 6 of 6 | |
| Kind of fix | 5 of 6 | Answer 2 (W-003 A) |
| Cause | 3 clear, 2 partial, 1 against | Answer 1 (W-006 N) against; answers 4 and 5 "partly" |

1. **r_loc in case N asks for too much.** W-006 N: the judge says the cause is right; the grader gives 0. In case N the grader wants
   the "if intended" branch at the case-A place AND the "if mistake" branch at the case-B place. The agent put both branches on the
   constraint (nginx.conf:90), which a person accepts.
2. **The fix class is judged by its name, not by what the fix does.** W-003 A: the agent changes the page to ask for 100 posts and calls
   it `finish`; the label calls the same change `revert` (the page is what was changed). The judge says the fix is right; the grader gives 0.
   Seen before with `repair_data` against `revert`.
3. **The verdict word and the action come apart.** W-001 A: right action (keep the rotation, update nginx), wrong word (`mistake`), because the
   agent takes the stale secret as its constraint. W-008 B: right word, wrong object. r_verdict scores the word only. A verdict means something
   only when the agent's constraint is our constraint. Candidate: a separate score "constraint identified".
4. **A revert with no decision behind it is not counted anywhere.** W-004 N: the agent removes the index although no decision speaks about it.
   It is not "forbidden" by our rule (no decision exists), but it could still remove something the team wanted. Candidate: count
   "revert without a decision" on its own.

## From the judge about the materials

5. Question "right cause" has two candidates on the page: what the agent calls the constraint, and the cause in its chosen branch.
6. Question "removes a team decision" has no meaning when there is no entry.
7. "Right kind of fix" is unclear for `cannot_decide`.
8. Decision numbers repeat with different text across answers (D-026 twice), because each world has its own order. The sheet should say so.

## From the baseline itself

9. In 6 of 10 case-B answers the agent says `mistake` about something we did not change; r_verdict still gives 1.
10. On a control, `cannot_decide` is neutral (decided 2026-11-22, applied in run_control.py, not in grade.py).
11. Each incident folder should have a parent folder of its own (the agent sometimes searches the parent).
