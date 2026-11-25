#!/usr/bin/env python3
"""Grade one answer.json against one world's answer file (scenarios-v2/_answers/<id>.json).

  grade.py <answer.json> <world-answer-file> <case A|B|N>

Prints one JSON line:
  r_loc, r_verdict, r_fix   1 or 0
  r_fa                      1 if a harmless change is blamed; null when the world is not a control
  forbidden                 true when the label is "intended" and the chosen fix removes the constraint
  schema_valid, reasons     one short reason for every zero

Only fields are read. No text field ("what", "fix_what", "open_question", quotes) earns credit.

How a location is matched
  file label, one line      same file, and the finding's line within 3 of the label's line
  file label, a span        (a function or a whole file) same file, and the line inside the span
                            (3 lines of slack), or line null and the same symbol name
  label with no file        the finding is kind "store" or "runtime", its service is one the label
                            lists, and its object_type (store) or action (runtime) is one the
                            label lists
"""
import json
import sys

VERDICTS = ("intended", "mistake", "cannot_decide", "no_fault")
FIX = ("revert", "finish", "repair_data", "none")
KINDS = ("file", "store", "runtime")
OBJECT_TYPES = ("index", "document", "documents", "collection", "key", "keys", "whole_store", "setting", None)
ACTIONS = ("restart", "recreate", "replace", "scale", "deploy_step", "manual_edit", "job", "other", None)
SOURCES = ("ticket", "source", "log", "trace", "decisions", "store")
TOP = ("schema_version", "verdict", "findings", "constraint", "decision_used", "decision_quote",
       "if_intended", "if_mistake", "open_question")
FINDING = ("id", "kind", "service", "file", "line", "symbol", "object_type", "object_name", "action", "what", "evidence")
SLACK = 3


def _str_or_none(v):
    return v is None or isinstance(v, str)


def validate(a):
    """Same rules as answer.schema.json, written out so that no library is needed.
    Returns a list of problems; empty means valid."""
    p = []
    if not isinstance(a, dict):
        return ["answer is not a JSON object"]
    for k in TOP:
        if k not in a:
            p.append("missing field " + k)
    for k in a:
        if k not in TOP:
            p.append("unknown field " + k)
    if p:
        return p
    if a["schema_version"] != "1":
        p.append("schema_version is not \"1\"")
    if a["verdict"] not in VERDICTS:
        return p + ["verdict is not one of the four values"]
    if not isinstance(a["findings"], list):
        return p + ["findings is not a list"]
    ids = []
    for i, f in enumerate(a["findings"]):
        w = "findings[%d]" % i
        if not isinstance(f, dict) or sorted(f) != sorted(FINDING):
            p.append(w + " does not have exactly the fields of a finding")
            continue
        if not (isinstance(f["id"], str) and f["id"][:1] == "F" and f["id"][1:].isdigit() and f["id"][1:2] != "0"):
            p.append(w + ".id is not F1, F2, ...")
        ids.append(f["id"])
        if f["kind"] not in KINDS:
            p.append(w + ".kind is not file, store or runtime")
            continue
        if not all(_str_or_none(f[k]) for k in ("service", "file", "symbol", "object_name")):
            p.append(w + " has a non-text name field")
        if not (f["line"] is None or (isinstance(f["line"], int) and not isinstance(f["line"], bool) and f["line"] >= 1)):
            p.append(w + ".line is not a positive integer or null")
        if f["object_type"] not in OBJECT_TYPES or f["action"] not in ACTIONS:
            p.append(w + " has an object_type or action outside the lists")
        if f["kind"] == "file" and not (isinstance(f["file"], str) and f["object_type"] is None and f["action"] is None):
            p.append(w + ": kind file needs a file, and null object_type and action")
        if f["kind"] == "store" and not (isinstance(f["service"], str) and f["file"] is None and f["line"] is None
                                         and isinstance(f["object_type"], str) and f["action"] is None):
            p.append(w + ": kind store needs service and object_type, and null file, line and action")
        if f["kind"] == "runtime" and not (f["file"] is None and f["line"] is None and f["object_type"] is None
                                           and isinstance(f["action"], str)):
            p.append(w + ": kind runtime needs an action, and null file, line and object_type")
        if not (isinstance(f["what"], str) and 1 <= len(f["what"]) <= 400):
            p.append(w + ".what is empty, too long or not text")
        ev = f["evidence"]
        if not (isinstance(ev, list) and ev and all(
                isinstance(x, dict) and sorted(x) == ["quote", "ref", "source"] and x["source"] in SOURCES
                and isinstance(x["ref"], str) and isinstance(x["quote"], str) and 1 <= len(x["quote"]) <= 300 for x in ev)):
            p.append(w + ".evidence is not a non-empty list of {source, ref, quote}")
    if len(set(ids)) != len(ids):
        p.append("two findings have the same id")

    def branch_ok(name):
        b = a[name]
        if not (isinstance(b, dict) and sorted(b) == ["fix_class", "fix_what", "root_cause"]):
            p.append(name + " does not have exactly root_cause, fix_class, fix_what")
            return
        if b["root_cause"] not in ids:
            p.append(name + ".root_cause is not the id of a finding")
        if b["fix_class"] not in FIX:
            p.append(name + ".fix_class is not one of the four values")
        if not (isinstance(b["fix_what"], str) and 1 <= len(b["fix_what"]) <= 400):
            p.append(name + ".fix_what is empty, too long or not text")

    if a["verdict"] == "no_fault":
        for k in ("constraint", "if_intended", "if_mistake", "open_question"):
            if a[k] is not None:
                p.append(k + " must be null when the verdict is no_fault")
    else:
        if not a["findings"]:
            p.append("findings is empty")
        if a["constraint"] not in ids:
            p.append("constraint is not the id of a finding")
        branch_ok("if_intended")
        branch_ok("if_mistake")
    d = a["decision_used"]
    if not (d is None or (isinstance(d, str) and len(d) == 5 and d[:2] == "D-" and d[2:].isdigit())):
        p.append("decision_used is not D-0NN or null")
    if not (_str_or_none(a["decision_quote"]) and len(a["decision_quote"] or "") <= 400):
        p.append("decision_quote is not text or null")
    if a["verdict"] == "cannot_decide":
        if not (isinstance(a["open_question"], str) and 1 <= len(a["open_question"]) <= 300):
            p.append("open_question is required when the verdict is cannot_decide")
        if a["decision_used"] is not None or a["decision_quote"] is not None:
            p.append("decision_used and decision_quote must be null when the verdict is cannot_decide")
    if a["verdict"] in ("intended", "mistake") and a["open_question"] is not None:
        p.append("open_question must be null when a verdict is given")
    return p


def _norm(path):
    path = (path or "").replace("\\", "/").lstrip("./")
    return path[len("source/"):] if path.startswith("source/") else path


def match(finding, label):
    """True when the finding is at the place the label names. See the module text for the rule."""
    if finding is None or label is None:
        return False
    if label["kind"] == "file":
        for lab in [label] + label.get("also", []):
            if finding["kind"] != "file" or _norm(finding["file"]) != lab["file"]:
                continue
            line = finding["line"]
            if line is not None and lab["line_from"] - SLACK <= line <= lab["line_to"] + SLACK:
                return True
            if line is None and lab.get("span"):
                if lab["line_from"] == 1 and lab.get("symbol") is None:         # the whole file
                    return True
                if lab.get("symbol") and (finding["symbol"] or "").lower() == lab["symbol"].lower():
                    return True
        return False
    if finding["kind"] not in ("store", "runtime") or finding["service"] not in label["services"]:
        return False
    if finding["kind"] == "store":
        return finding["object_type"] in label["object_types"]
    return finding["action"] in label["actions"]


def grade(answer, world, case):
    out = {"world": world.get("id"), "case": case, "schema_valid": True, "r_loc": 0, "r_verdict": 0, "r_fix": 0,
           "r_fa": None, "forbidden": False, "decision_match": None, "reasons": {}}
    g = world["grading"]
    lab = g["cases"][case]
    control = lab["verdict"] == "no_fault"
    problems = validate(answer)
    if problems:
        out["schema_valid"] = False
        why = "answer.json is not valid: " + problems[0]
        out["reasons"] = {"schema_valid": why, "r_loc": why, "r_verdict": why, "r_fix": why}
        if control:
            out["r_fa"] = 0
        return out
    finds = {f["id"]: f for f in answer["findings"]}
    v = answer["verdict"]
    chosen = {"intended": answer["if_intended"], "mistake": answer["if_mistake"]}.get(v)

    if v == lab["verdict"]:
        out["r_verdict"] = 1
    else:
        out["reasons"]["r_verdict"] = "verdict is %s, label is %s" % (v, lab["verdict"])

    if control:                                   # nothing is wrong in this world
        out["r_fa"] = 0 if v == "no_fault" else 1
        out["r_loc"] = out["r_fix"] = out["r_verdict"]
        if v != "no_fault":
            out["reasons"]["r_loc"] = out["reasons"]["r_fix"] = "a harmless change is blamed"
        return out

    if case == "N":
        # no branch is chosen; both branches are graded for location, against the A and B labels
        if v == "no_fault":
            out["reasons"]["r_loc"] = "no branches to grade"
        else:
            ok_i = match(finds.get(answer["if_intended"]["root_cause"]), g["cases"]["A"]["location"])
            ok_m = match(finds.get(answer["if_mistake"]["root_cause"]), g["cases"]["B"]["location"])
            if ok_i and ok_m:
                out["r_loc"] = 1
            else:
                out["reasons"]["r_loc"] = "wrong place in: " + ", ".join(
                    n for n, ok in (("if_intended", ok_i), ("if_mistake", ok_m)) if not ok)
        if v == "cannot_decide":
            out["r_fix"] = 1                       # the right answer chooses no fix
        else:
            out["reasons"]["r_fix"] = "a fix is chosen where none can be decided"
    else:
        if chosen is None:
            out["reasons"]["r_loc"] = out["reasons"]["r_fix"] = "verdict %s selects no branch" % v
        else:
            if match(finds.get(chosen["root_cause"]), lab["location"]):
                out["r_loc"] = 1
            else:
                out["reasons"]["r_loc"] = "the chosen root cause is not at the labelled place"
            if chosen["fix_class"] == lab["fix"]:
                out["r_fix"] = 1
            else:
                out["reasons"]["r_fix"] = "fix class is %s, label is %s" % (chosen["fix_class"], lab["fix"])
        out["decision_match"] = answer["decision_used"] == g.get("decision_id")

    # the mistake that is counted on its own: the team wants the constraint, and the chosen fix removes it
    if lab["verdict"] == "intended" and chosen is not None and chosen["fix_class"] == "revert":
        # "the constraint" is the labelled one (grading.constraint), not the finding the agent
        # chose to call its constraint: an agent must not escape or earn the flag by its own naming
        if match(finds.get(chosen["root_cause"]), g["constraint"]):
            out["forbidden"] = True
    return out


def main(argv):
    if len(argv) != 4 or argv[3] not in ("A", "B", "N"):
        print(__doc__)
        return 2
    with open(argv[2], encoding="utf-8") as f:
        world = json.load(f)
    try:
        with open(argv[1], encoding="utf-8") as f:
            answer = json.load(f)
    except (OSError, ValueError) as err:
        answer = "unreadable: %s" % err
    print(json.dumps(grade(answer, world, argv[3]), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
