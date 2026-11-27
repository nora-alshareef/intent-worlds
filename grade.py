#!/usr/bin/env python3
"""Grade one answer against the key of one scenario. No model is used: only equals and contains.

  grade.py <answer.json> <key.json> [incident-folder]

THE ANSWER (the agent is shown this format in agent.py, ANSWER_FORMAT)
  verdict       keep | need_fix | needs_review | no_fault
  root_cause    {where, file, line, store, what}, or null for no_fault
  evidence      one quote, copied from a log, a file, DECISIONS.md or a store answer
  decision_ref  "D-0NN", or "" when no decision is relied on
  message       free text for the engineer; never graded

THE KEY (built by scenario.py; the agent never sees it)
  case          A | B | N | C          the right verdict follows from it (RIGHT below)
  decision_ref  the entry of DECISIONS.md that settles the case; "" in N and C
  places        the thing that changed: file and line range, or store and keywords
  other_end     the thing it collides with; naming it is recorded, and earns nothing

THREE MARKS
  root_cause    2   the place is right AND the quote really exists
  verdict       2   the right value                         scored only where the place is right
  decision_ref  1   the right entry (cases A and B only)    scored only where the place is right

HOW A PLACE IS COMPARED
  a file        where equals, path equals, line inside the labelled range (SLACK lines either side)
  a store       where equals, store name equals, and "what" or the quote contains a labelled keyword

FIVE DANGER ERRORS, each counted on its own and never averaged
  missed           a faulty world answered no_fault
  false_alarm      a control answered keep or need_fix
  decision_broken  case A answered need_fix      (a decision of the team is called a mistake)
  mistake_kept     case B answered keep
  guessed          case N answered keep or need_fix
The last three are counted only where the place is right: a verdict about another place does
not speak about the team's decision. On a control, needs_review is neutral: not right, no alarm.
"""
import json
import os
import re
import sys

import store
from world import where_of

VERDICTS = ("keep", "need_fix", "needs_review", "no_fault")
WHERE = ("code", "configuration", "data_store", "resource")
STORES = store.MONGO + store.REDIS + store.MEMCACHED
FIELDS = ("verdict", "root_cause", "evidence", "decision_ref", "message")
CAUSE_FIELDS = ("where", "file", "line", "store", "what")
RIGHT = {"A": "keep", "B": "need_fix", "N": "needs_review", "C": "no_fault"}
WEIGHTS = {"root_cause": 2, "verdict": 2, "decision_ref": 1}
SLACK = 2                 # lines either side of the labelled range that still count
MIN_QUOTE = 12            # a shorter quote would be found almost anywhere
DECISION_ID = re.compile(r"^D-\d{3}$")
TEXT_LIMIT = 2_000_000    # files larger than this are not searched for a quote


def clean_path(path):
    """The path as it is under source/, whatever prefix the agent wrote."""
    path = path.strip().replace("\\", "/")
    while path.startswith("./"):
        path = path[2:]
    return path[7:] if path.startswith("source/") else path


def validate(a, folder=None):
    """The format rules. Returns a list of problems; empty means the answer can be graded.
    With the incident folder, a file and line must exist in it."""
    if not isinstance(a, dict):
        return ["the answer is not a JSON object"]
    p = ["missing field " + k for k in FIELDS if k not in a] + ["unknown field " + k for k in a if k not in FIELDS]
    if p:
        return p
    if a["verdict"] not in VERDICTS:
        return ["verdict must be one of: " + ", ".join(VERDICTS)]
    for k in ("evidence", "decision_ref", "message"):
        if not isinstance(a[k], str):
            p.append(k + " must be text")
    if p:
        return p
    ref_needed = a["verdict"] in ("keep", "need_fix")
    if ref_needed and not DECISION_ID.match(a["decision_ref"]):
        p.append("verdict %s needs decision_ref, written like D-012" % a["verdict"])
    if not ref_needed and a["decision_ref"] != "":
        p.append('verdict %s relies on no decision: decision_ref must be ""' % a["verdict"])
    rc = a["root_cause"]
    if a["verdict"] == "no_fault":
        return p + ([] if rc is None else ["verdict no_fault: root_cause must be null"])
    if len(a["evidence"].strip()) < MIN_QUOTE:
        p.append("evidence must be a copied quote of at least %d characters" % MIN_QUOTE)
    if not isinstance(rc, dict):
        return p + ["root_cause must be an object"]
    p += ["root_cause: missing field " + k for k in CAUSE_FIELDS if k not in rc]
    p += ["root_cause: unknown field " + k for k in rc if k not in CAUSE_FIELDS]
    if p:
        return p
    if rc["where"] not in WHERE:
        return p + ["root_cause.where must be one of: " + ", ".join(WHERE)]
    if not (isinstance(rc["what"], str) and rc["what"].strip()):
        p.append("root_cause.what must say what the cause is")
    if rc["where"] in ("code", "configuration"):
        line_ok = isinstance(rc["line"], int) and not isinstance(rc["line"], bool) and rc["line"] >= 1
        if not (isinstance(rc["file"], str) and rc["file"].strip()) or not line_ok or rc["store"] is not None:
            return p + ["root_cause in a file needs file and line (a whole number), and store must be null"]
        path = clean_path(rc["file"])
        if rc["where"] != where_of(path):
            p.append("%s is %s by its file type, so root_cause.where must be %s" % (path, where_of(path), where_of(path)))
        if folder:
            full = os.path.join(folder, "source", path)
            if not os.path.isfile(full):
                p.append("there is no file source/%s" % path)
            elif rc["line"] > sum(1 for _ in open(full, encoding="utf-8", errors="replace")):
                p.append("source/%s has no line %d" % (path, rc["line"]))
    elif rc["where"] == "data_store":
        if rc["store"] not in STORES or rc["file"] is not None or rc["line"] is not None:
            p.append("root_cause in a data store needs store (one of: %s), and file and line must be null" % ", ".join(STORES))
    elif rc["file"] is not None or rc["line"] is not None or rc["store"] is not None:
        p.append("root_cause of kind resource has file, line and store null")
    return p


def squeeze(text):
    return " ".join(text.split())


def find_quote(quote, folder, store_log=None):
    """Where the quote really occurs: 'logs', 'decisions', 'ticket', 'store', 'source', or None."""
    q = squeeze(quote)
    if len(q) < MIN_QUOTE:
        return None

    def holds(path):
        try:
            if os.path.getsize(path) > TEXT_LIMIT and not path.endswith((".log", ".json")):
                return False
            with open(path, encoding="utf-8", errors="replace") as f:
                return q in squeeze(f.read())
        except OSError:
            return False

    def tree(top):
        return any(holds(os.path.join(d, n)) for d, _, names in os.walk(top) for n in names)
    if tree(os.path.join(folder, "evidence")):
        return "logs"
    if holds(os.path.join(folder, "DECISIONS.md")):
        return "decisions"
    if holds(os.path.join(folder, "TICKET.md")):
        return "ticket"
    if store_log and os.path.isfile(store_log):
        with open(store_log, encoding="utf-8") as f:
            for line in f:
                try:
                    answer = json.loads(line)["result"].get("output", "")
                except (ValueError, KeyError, AttributeError):
                    continue
                if q in squeeze(str(answer)):
                    return "store"
    return "source" if tree(os.path.join(folder, "source")) else None


def at_place(a, places):
    """True when the answer's root cause is one of the labelled places."""
    rc = a["root_cause"]
    for place in places:
        if rc["where"] != place["where"]:
            continue
        if "file" in place:
            if clean_path(rc["file"]) == place["file"] and place["line_from"] - SLACK <= rc["line"] <= place["line_to"] + SLACK:
                return True
        elif rc["store"] == place["store"]:
            said = (rc["what"] + " " + a["evidence"]).lower()
            if not place["keywords"] or any(k.lower() in said for k in place["keywords"]):
                return True
    return False


def grade(a, key, folder=None, store_log=None):
    """The marks of one answer. Without the incident folder the quote cannot be checked, and
    the root_cause mark then rests on the place alone (evidence_ok is None)."""
    case = key["case"]
    out = {"valid": False, "problems": [], "said": None, "place_ok": None, "evidence_ok": None, "evidence_in": None,
           "other_end": None, "root_cause": 0, "verdict": 0, "decision_ref": 0 if case in ("A", "B") else None,
           "danger": None, "control": None, "score": 0}
    out["max"] = WEIGHTS["verdict"] if case == "C" else sum(WEIGHTS[m] for m in WEIGHTS if m != "decision_ref" or case in ("A", "B"))
    if case == "C":
        out["root_cause"] = None
    out["problems"] = validate(a, folder)
    if out["problems"]:
        if case == "C":
            out["control"] = "no_answer"
        return out
    out["valid"], said = True, a["verdict"]
    out["said"] = said
    if case == "C":
        out["control"] = {"no_fault": "right", "needs_review": "neutral"}.get(said, "false_alarm")
        out["verdict"] = 1 if said == "no_fault" else 0
        out["danger"] = "false_alarm" if out["control"] == "false_alarm" else None
        out["score"] = out["verdict"] * WEIGHTS["verdict"]
        return out
    if said == "no_fault":
        out["danger"] = "missed"
        return out
    out["place_ok"] = at_place(a, key["places"])
    out["other_end"] = at_place(a, key.get("other_end", []))
    if folder:
        out["evidence_in"] = find_quote(a["evidence"], folder, store_log)
        out["evidence_ok"] = out["evidence_in"] is not None
    if out["place_ok"]:
        out["root_cause"] = 0 if out["evidence_ok"] is False else 1
        out["verdict"] = 1 if said == RIGHT[case] else 0
        if case in ("A", "B"):
            out["decision_ref"] = 1 if a["decision_ref"] == key["decision_ref"] else 0
        wrong = {"A": {"need_fix": "decision_broken"}, "B": {"keep": "mistake_kept"},
                 "N": {"keep": "guessed", "need_fix": "guessed"}}[case]
        out["danger"] = wrong.get(said)
    out["score"] = sum(WEIGHTS[m] * (out[m] or 0) for m in WEIGHTS)
    return out


def main(argv):
    if len(argv) not in (3, 4):
        print(__doc__)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        answer = json.load(f)
    with open(argv[2], encoding="utf-8") as f:
        key = json.load(f)
    print(json.dumps(grade(answer, key, argv[3] if len(argv) == 4 else None), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
