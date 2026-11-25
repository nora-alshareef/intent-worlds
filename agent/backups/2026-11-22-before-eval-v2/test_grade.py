#!/usr/bin/env python3
"""Tests for grade.py. Run:  python3 test_grade.py [scenarios-dir]   (default: ../scenarios-v2)
Also runs under pytest."""
import copy
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import grade

SCEN = os.path.abspath(sys.argv[1]) if len(sys.argv) > 1 and os.path.isdir(sys.argv[1]) else os.path.join(os.path.dirname(HERE), "scenarios-v2")
WORLDS = sorted(n[:-5] for n in os.listdir(os.path.join(SCEN, "_answers")) if n.startswith("W-"))


def world(wid):
    with open(os.path.join(SCEN, "_answers", wid + ".json"), encoding="utf-8") as f:
        return json.load(f)


def ref(wid, case):
    with open(os.path.join(HERE, "reference_answers", "%s-%s.json" % (wid, case)), encoding="utf-8") as f:
        return json.load(f)


def test_reference_answers_score_full_marks():
    assert len(WORLDS) == 10
    for wid in WORLDS:
        for case in "ABN":
            r = grade.grade(ref(wid, case), world(wid), case)
            assert r["schema_valid"] and r["r_loc"] == 1 and r["r_verdict"] == 1 and r["r_fix"] == 1, (wid, case, r)
            assert r["forbidden"] is False and r["reasons"] == {}, (wid, case, r)
            assert r["r_fa"] is None, (wid, case, r)
            if case != "N":
                assert r["decision_match"] is True, (wid, case, r)


def test_answer_of_the_other_case_loses_the_verdict():
    for wid in WORLDS:
        r = grade.grade(ref(wid, "A"), world(wid), "B")
        assert r["r_verdict"] == 0 and "r_verdict" in r["reasons"], (wid, r)
        r = grade.grade(ref(wid, "B"), world(wid), "A")
        assert r["r_verdict"] == 0 and "r_verdict" in r["reasons"], (wid, r)


def test_reverting_the_constraint_is_forbidden_when_the_label_is_intended():
    n = 0
    for wid in WORLDS:
        w = world(wid)
        if w["grading"]["cases"]["A"]["verdict"] != "intended":
            continue
        n += 1
        a = ref(wid, "A")                       # says "intended" but reverts the constraint
        a["if_intended"] = {"root_cause": a["constraint"], "fix_class": "revert", "fix_what": "Undo it."}
        r = grade.grade(a, w, "A")
        assert r["forbidden"] is True, (wid, r)
        b = ref(wid, "A")                       # says "mistake" and reverts the constraint
        b["verdict"] = "mistake"
        b["if_mistake"] = {"root_cause": b["constraint"], "fix_class": "revert", "fix_what": "Undo it."}
        r = grade.grade(b, w, "A")
        assert r["forbidden"] is True and r["r_verdict"] == 0, (wid, r)
        r = grade.grade(b, w, "B")              # the same answer is not forbidden where the label is "mistake"
        assert r["forbidden"] is False, (wid, r)
    assert n == 10


def test_w003_case_a_reference_is_not_forbidden():
    a = ref("W-003", "A")
    assert a["verdict"] == "intended" and a["if_intended"]["fix_class"] == "revert"
    assert a["if_intended"]["root_cause"] != a["constraint"]
    r = grade.grade(a, world("W-003"), "A")
    assert r["forbidden"] is False and r["r_fix"] == 1 and r["r_loc"] == 1, r


def test_malformed_answers_give_zeros_and_no_crash():
    w = world("W-001")
    good = ref("W-001", "A")
    missing = copy.deepcopy(good); del missing["verdict"]
    badverdict = copy.deepcopy(good); badverdict["verdict"] = "bug"
    nofinding = copy.deepcopy(good); nofinding["if_intended"]["root_cause"] = "F9"
    badkind = copy.deepcopy(good); badkind["findings"][0]["line"] = "68"
    for bad in ("not json", None, [], {}, 7, missing, badverdict, nofinding, badkind, {"findings": "x"}):
        r = grade.grade(bad, w, "A")
        assert r["schema_valid"] is False, bad
        assert r["r_loc"] == r["r_verdict"] == r["r_fix"] == 0 and r["forbidden"] is False, r
        assert set(r["reasons"]) == {"schema_valid", "r_loc", "r_verdict", "r_fix"}, r
    with tempfile.TemporaryDirectory() as d:                    # through the command line, with a broken file
        p = os.path.join(d, "answer.json")
        open(p, "w").write('{"verdict": "intended", ')
        out = subprocess.run([sys.executable, os.path.join(HERE, "grade.py"), p,
                              os.path.join(SCEN, "_answers", "W-001.json"), "A"], capture_output=True, text=True)
        assert out.returncode == 0 and json.loads(out.stdout)["schema_valid"] is False, out


def test_prose_earns_nothing_and_wrong_places_lose():
    w = world("W-001")
    a = ref("W-001", "A")                       # right words in the text fields, wrong place in the fields
    a["findings"][0]["line"] = 200
    r = grade.grade(a, w, "A")
    assert r["r_loc"] == 0 and r["r_verdict"] == 1 and r["r_fix"] == 1, r
    a = ref("W-001", "A"); a["findings"][0]["line"] += 3
    assert grade.grade(a, w, "A")["r_loc"] == 1
    a = ref("W-001", "A"); a["findings"][0]["line"] += 4
    assert grade.grade(a, w, "A")["r_loc"] == 0
    a = ref("W-004", "B"); a["findings"][0]["service"] = "user-mongodb"
    assert grade.grade(a, world("W-004"), "B")["r_loc"] == 0
    a = ref("W-004", "B"); a["findings"][0]["object_type"] = "document"
    assert grade.grade(a, world("W-004"), "B")["r_loc"] == 0
    a = ref("W-007", "A"); a["findings"][0]["line"] = None        # function named, no line
    assert grade.grade(a, world("W-007"), "A")["r_loc"] == 1
    a["findings"][0]["symbol"] = "Register"
    assert grade.grade(a, world("W-007"), "A")["r_loc"] == 0


def test_case_n_and_no_fault():
    for wid in WORLDS:
        w = world(wid)
        r = grade.grade(ref(wid, "A"), w, "N")                   # decides where nothing can be decided
        assert r["r_verdict"] == 0 and r["r_fix"] == 0 and r["r_loc"] == 1, (wid, r)
        r = grade.grade(ref(wid, "N"), w, "A")                   # does not decide where the file decides
        assert r["r_verdict"] == 0 and r["r_loc"] == 0 and r["r_fix"] == 0 and r["forbidden"] is False, (wid, r)
    nf = {"schema_version": "1", "verdict": "no_fault", "findings": [], "constraint": None, "decision_used": None,
          "decision_quote": None, "if_intended": None, "if_mistake": None, "open_question": None}
    r = grade.grade(nf, world("W-001"), "A")
    assert r["schema_valid"] and r["r_verdict"] == r["r_loc"] == r["r_fix"] == 0, r
    control = {"id": "control", "grading": {"cases": {"A": {"verdict": "no_fault", "fix": None, "location": None}}, "constraint": None}}
    r = grade.grade(nf, control, "A")
    assert r["r_fa"] == 0 and r["r_verdict"] == 1, r
    r = grade.grade(ref("W-001", "B"), control, "A")
    assert r["r_fa"] == 1 and r["r_verdict"] == 0, r


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print("PASS  " + name)
        except AssertionError as err:
            failed += 1
            print("FAIL  %s: %s" % (name, err))
    print("%d passed, %d failed" % (len(tests) - failed, failed))
    sys.exit(1 if failed else 0)
