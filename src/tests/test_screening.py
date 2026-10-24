"""Tests for the pure-Python parts of stages 2 and 3 (no API calls): profile validation, depends_only_on_uncertain,
UNKNOWN_POLICY, supersession, ordering, and the LLM cache key."""

from __future__ import annotations

import product_profile as profile_mod            # src/product_profile.py
import screen_regulations as sr
import llm


def reg(name, version="1.0"):
    return {"regulation": name, "title": f"Title of {name}", "version": version}


def raw(applies="yes", depends=(), only_unc=False, supersedes=(), superseded_by=(), note=""):
    return {"applies": applies, "confidence": "high", "level": "product_itself", "depends_on": list(depends),
            "depends_only_on_uncertain": only_unc, "supersession_note": note, "supersedes": list(supersedes),
            "superseded_by": list(superseded_by), "reason": "because"}


PROFILE = {"facts": [{"feature": "light source", "status": "representative"},
                     {"feature": "colour", "status": "stated"},
                     {"feature": "wiring", "status": "unknown"}]}
NAMES = {"R1", "R2", "R3"}


def test_profile_validation_downgrades_without_inventing():
    cleaned, warnings = profile_mod.validate_profile({
        "product_name": " Lamp ", "product_kind": "bogus", "vehicle_categories": ["M1", ""],
        "parts": [{"name": "", "function": "x", "evidence": "y"}, {"name": "lens", "function": "f", "evidence": "e"}],
        "facts": [{"feature": "a", "status": "stated", "evidence": "", "source": "s"},
                  {"feature": "b", "status": "explicitly_absent", "evidence": "no b", "source": "s"},
                  {"feature": "c", "status": "nonsense", "evidence": "e", "source": "s"},
                  {"feature": "b", "status": "explicitly_absent", "evidence": "no b", "source": "s"}],
        "drawing_annotations": [{"part": "lens", "note": "red"}, {"part": "x", "note": ""}]})
    assert cleaned["product_kind"] == "unknown" and cleaned["vehicle_categories"] == ["M1"]
    assert [p["name"] for p in cleaned["parts"]] == ["lens"]
    assert [(f["feature"], f["status"]) for f in cleaned["facts"]] == [("a", "unknown"), ("b", "explicitly_absent")]
    assert all(f["status"] != "explicitly_absent" for f in cleaned["facts"] if f["feature"] == "a")
    assert len(cleaned["drawing_annotations"]) == 1 and len(warnings) >= 4


def test_markdown_split_keeps_tables_and_preamble():
    parts = profile_mod.split_sections("intro\n# A\ntext\n## B\n| x | y |\n|---|---|\n| 1 | 2 |\n# C\nlast")
    assert [p for p, _ in parts] == ["", "A", "A > B", "C"]
    assert "| 1 | 2 |" in parts[2][1]


def test_expected_json_is_never_read(tmp_path):
    (tmp_path / "spec.md").write_text("# s")
    (tmp_path / "expected.json").write_text("{}")
    (tmp_path / "Expected_Answer.JSON").write_text("{}")
    assert [p.name for p in profile_mod.case_files(tmp_path)] == ["spec.md"]


def test_depends_only_on_uncertain_is_checked_against_the_profile():
    d = sr.clean_decision(reg("R1"), raw("no", ["light source", "wiring"], True), None, PROFILE, NAMES)
    assert d["depends_only_on_uncertain"] is True
    d = sr.clean_decision(reg("R1"), raw("no", ["light source", "colour"], True), None, PROFILE, NAMES)
    assert d["depends_only_on_uncertain"] is False and any("profile used" in n for n in d["notes"])
    d = sr.clean_decision(reg("R1"), raw("no", [], True), None, PROFILE, NAMES)
    assert d["depends_only_on_uncertain"] is False                        # nothing supports it


def test_unknown_policy_include_and_exclude():
    for policy, expected in (("include", True), ("exclude", False)):
        no = sr.clean_decision(reg("R1"), raw("no", ["wiring"], True), None, PROFILE, NAMES)
        yes = sr.clean_decision(reg("R2"), raw("yes", ["wiring"], True), None, PROFILE, NAMES)
        firm_no = sr.clean_decision(reg("R3"), raw("no", ["colour"], False), None, PROFILE, NAMES)
        sr.apply_unknown_policy([no, yes, firm_no], policy)
        assert no["applies"] is expected and yes["applies"] is expected and firm_no["applies"] is False
        assert (no["decision_source"] == "unknown_policy") == (policy == "include")


def test_failed_call_follows_policy():
    d = sr.clean_decision(reg("R1"), None, "stop_reason=refusal", PROFILE, NAMES)
    sr.apply_unknown_policy([d], "include")
    assert d["applies"] is True and d["decision_source"] == "policy_after_failure" and d["failure"]


def test_supersession_keeps_the_newer_when_both_apply():
    new = sr.clean_decision(reg("R2"), raw("yes", supersedes=["R1"], note="R2 replaces R1"), None, PROFILE, NAMES)
    old = sr.clean_decision(reg("R1"), raw("yes", superseded_by=["R2"]), None, PROFILE, NAMES)
    other = sr.clean_decision(reg("R3"), raw("yes"), None, PROFILE, NAMES)
    sr.apply_supersession([new, old, other])
    assert (new["applies"], old["applies"], other["applies"]) == (True, False, True)
    assert old["decision_source"] == "superseded" and "R2" in old["notes"][-1]


def test_supersession_needs_both_to_apply_and_ignores_mutual_claims():
    new = sr.clean_decision(reg("R2"), raw("yes", supersedes=["R1"]), None, PROFILE, NAMES)
    old = sr.clean_decision(reg("R1"), raw("no"), None, PROFILE, NAMES)
    sr.apply_supersession([new, old])
    assert old["applies"] is False and old["decision_source"] == "model"
    a = sr.clean_decision(reg("R1"), raw("yes", supersedes=["R2"]), None, PROFILE, NAMES)
    b = sr.clean_decision(reg("R2"), raw("yes", supersedes=["R1"]), None, PROFILE, NAMES)
    sr.apply_supersession([a, b])
    assert a["applies"] and b["applies"]


def test_unknown_names_in_supersession_are_ignored():
    d = sr.clean_decision(reg("R1"), raw("yes", supersedes=["R99", "R1"]), None, PROFILE, NAMES)
    assert d["supersedes"] == [] and d["notes"]


def test_order_puts_applying_first_then_confidence_then_number():
    ds = []
    for name, applies, conf in (("UN R10", False, "high"), ("UN R9", True, "low"), ("UN R2", True, "high"),
                                ("UN R100", True, "high")):
        d = sr.clean_decision(reg(name), raw("yes" if applies else "no"), None, PROFILE, NAMES | {name})
        d["applies"], d["confidence"] = applies, conf
        ds.append(d)
    assert [d["regulation"] for d in sr.order(ds)] == ["UN R2", "UN R100", "UN R9", "UN R10"]


def test_cache_key_depends_on_model_prompt_and_input():
    base = llm.cache_key("m", "prompt", [{"type": "text", "text": "x"}], {"type": "object"})
    assert base == llm.cache_key("m", "prompt", [{"type": "text", "text": "x"}], {"type": "object"})
    assert base != llm.cache_key("m2", "prompt", [{"type": "text", "text": "x"}], {"type": "object"})
    assert base != llm.cache_key("m", "prompt2", [{"type": "text", "text": "x"}], {"type": "object"})
    assert base != llm.cache_key("m", "prompt", [{"type": "text", "text": "y"}], {"type": "object"})
