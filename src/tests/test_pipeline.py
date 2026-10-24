"""Tests for the pure-Python parts of stages 4-6 (no API calls): units and outline, batching, answer handling and
failure splitting, export (sources from the index, duplicates, clipping, exclusion)."""

from __future__ import annotations

import extract
import export
import select_sections as ss
from llm import LLMFailure


def rec(label, **kw):
    base = {"id": f"UN R1|1|base|f.pdf|{label}#1", "regulation": "UN R1", "version": "1", "role": "base", "page": 3,
            "paragraph": label, "number": label, "kind": "paragraph", "section": None, "annex": None, "appendix": None,
            "part": None, "title": None, "text": f"text of {label}", "has_obligation": True, "annex_kind": None,
            "breadcrumb": [], "parent_id": None, "children_ids": [], "table_ids": [], "obligation_signals": {"shall": 1},
            "condition_signals": [], "heading_value_item": False, "inherits_obligation": False,
            "inherits_obligation_reason": None}
    base.update(kw)
    return base


def test_unit_of_main_body_section_and_annex():
    assert ss.unit_of(rec("5.1.2")) == "5"
    assert ss.unit_of(rec("I 5.1", number="5.1", section="I")) == "I 5"
    assert ss.unit_of(rec("Annex 6 Part 4 1", number="1", annex="6", part="4")) == "Annex 6"


def test_outline_counts_titles_and_skips_forms():
    records = [rec("5", title="General", text="General"), rec("5.1", title="Marking"), rec("5.1.1", has_obligation=False),
               rec("Annex 1", number="", kind="title", annex="1", title="Communication", annex_kind="communication_form"),
               rec("Annex 1 1", number="1", annex="1", annex_kind="communication_form"),
               rec("Annex 2", number="", kind="title", annex="2", title="Test method", annex_kind="content"),
               rec("Annex 2 1", number="1", annex="2", annex_kind="content")]
    outline, forms = ss.build_outline(records)
    assert forms == ["Annex 1"]
    by = {u["id"]: u for u in outline}
    assert by["5"]["title"] == "General" and by["5"]["paragraphs"] == 3 and by["5"]["with_obligation"] == 2
    assert by["5"]["sub_sections"] == ["5.1: Marking"]
    assert by["Annex 2"]["title"] == "Test method" and by["Annex 2"]["annex_kind"] == "content"


def test_candidates_are_paragraphs_table_entries_and_titles_with_text():
    assert ss.is_candidate(rec("5.1"))
    assert ss.is_candidate(rec("5.1. Table 3", kind="table"))          # labelled as in the index, carries the cells
    assert not ss.is_candidate(rec("Annex 1", kind="title", title="Comm", text="Annex 1 Comm"))
    assert ss.is_candidate(rec("Annex 2", kind="title", title="Test", text="Annex 2 Test " + "lead-in text " * 10))


def test_batches_keep_order_never_split_and_stay_in_size_range():
    items = [{"id": str(i), "text": "x" * 50} for i in range(23)]
    assert [len(b) for b in extract.make_batches(items)] == [23]        # one batch of about 20 is better than 12 + 11
    for n in (1, 14, 20, 24, 40, 47, 120):
        items = [{"id": str(i), "text": "x" * 50} for i in range(n)]
        batches = extract.make_batches(items)
        assert [it["id"] for b in batches for it in b] == [it["id"] for it in items]
        assert all(len(b) <= extract.BATCH_MAX for b in batches)
        if n >= 15:
            assert all(len(b) >= 10 for b in batches)
    big = [{"id": str(i), "text": "x" * 20_000} for i in range(4)]
    assert all(len(b) <= 1 for b in extract.make_batches(big))          # a long paragraph is its own batch


class FakeRun(extract.Run):
    """Run with a scripted `ask`: fails for batches larger than `limit`, skips keys listed in `skip`."""

    def __init__(self, limit=99, skip=(), unknown=False):
        super().__init__({}, {}, {})
        self.limit, self.skip, self.unknown, self.calls = limit, set(skip), unknown, []

    def ask(self, batch):
        self.calls.append([c["id"] for c in batch])
        if len(batch) > self.limit:
            raise LLMFailure("stop_reason=max_tokens")
        if self.unknown:
            self.log["unknown_keys_dropped"] += 1
        return {c["id"]: {"applies": True, "reason": "r", "requirements": [f"req {c['id']}"]}
                for c in batch if c["id"] not in self.skip}


def batch_of(n):
    return [{"id": f"c{i}"} for i in range(n)]


def test_a_failing_batch_is_split_and_nothing_is_dropped():
    run = FakeRun(limit=5)
    out = run.run_batch(batch_of(16))
    assert set(out) == {f"c{i}" for i in range(16)} and all("requirements" in v for v in out.values())
    # oversized attempts fail and are split; the batches that succeeded (<= limit) cover every paragraph exactly once
    assert run.log["splits"] >= 3 and sum(len(c) for c in run.calls if len(c) <= 5) == 16


def test_a_single_paragraph_that_keeps_failing_is_recorded_as_failed():
    run = FakeRun(limit=0)
    out = run.run_batch(batch_of(3))
    assert {v["status"] for v in out.values()} == {"failed"} and run.log["paragraphs_failed"] == 3


def test_skipped_paragraphs_are_asked_again_once_then_marked_no_answer():
    run = FakeRun(skip={"c2"})
    out = run.run_batch(batch_of(5))
    assert out["c2"]["status"] == "no_answer" and "requirements" in out["c1"]
    assert run.calls.count(["c2"]) == 1 and run.log["no_answer"] == 1


def test_payload_includes_parent_table_and_hints():
    parent = rec("5.1", text="The lamp shall be:", id="P")
    child = rec("5.1.1", parent_id="P", table_ids=["T"], inherits_obligation=True,
                inherits_obligation_reason="parent_introduces_list", text="red.")
    p = extract.payload(child, "p1", {"P": parent}, {"T": {"caption": "Table 1", "cells": [["a", "b"]]}})
    assert p["parent_text"] == "The lamp shall be:" and "Table 1" in p["table"]
    assert any("inherits_obligation" in h for h in p["hints"]) and any("introduces" in h for h in p["hints"])


ROWS = [
    {"id": "A", "regulation": "UN R1", "reason": "applies", "requirements": ["One.", "One.", "Two."]},
    {"id": "B", "regulation": "UN R2", "reason": "applies", "requirements": ["Three."]},
    {"id": "C", "regulation": "UN R1", "reason": "no", "requirements": []},
    {"id": "D", "regulation": "UN R1", "reason": "applies", "requirements": ["One."]},     # same text, other paragraph
]
RECORDS = {k: rec(k, id=k, regulation=reg, page=pg, version="9")
           for k, reg, pg in (("A", "UN R1", 4), ("B", "UN R2", 7), ("C", "UN R1", 5), ("D", "UN R1", 6))}


def test_export_takes_sources_from_the_index_and_removes_exact_duplicates_in_a_paragraph():
    sub, stats = export.build_submission(ROWS, RECORDS, "case-x")
    assert sub["team_id"] == "team-07" and sub["case_id"] == "case-x"
    assert [r["id"] for r in sub["requirements"]] == ["R-0001", "R-0002", "R-0003", "R-0004"]
    assert [r["text"] for r in sub["requirements"]] == ["One.", "Two.", "Three.", "One."]
    first = sub["requirements"][0]
    assert first["source"] == {"regulation": "UN R1", "version": "9", "page": 4, "paragraph": "A"}
    assert first["evidence_id"] == "A" and stats["duplicates_removed"] == 1


def test_export_exclude_renumbers_and_clips_long_text():
    sub, _ = export.build_submission(ROWS, RECORDS, "case-x", {"UN R1"})
    assert [(r["id"], r["source"]["regulation"]) for r in sub["requirements"]] == [("R-0001", "UN R2")]
    long_rows = [{"id": "A", "regulation": "UN R1", "reason": "", "requirements": ["word " * 600]}]
    sub, stats = export.build_submission(long_rows, RECORDS, "case-x")
    assert len(sub["requirements"][0]["text"]) <= export.MAX_TEXT and stats["truncated"] == 1


def test_table_entry_gets_the_cells_and_its_owner_does_not():
    table = rec("5.1. Table 3", id="T", kind="table", table_ids=["TB"], parent_id="P", text="Table 3 Values")
    owner = rec("5.1", id="P", table_ids=["TB"], children_ids=["T"], text="The lamp shall meet Table 3.")
    cells = {"TB": {"caption": "Table 3", "cells": [["a", "1"]]}}
    by_id = {"P": owner, "T": table}
    t, o = extract.payload(table, "p2", by_id, cells), extract.payload(owner, "p1", by_id, cells)
    assert "table" in t and "Table 3" in t["table"] and t["parent_text"].startswith("The lamp shall")
    assert "table" not in o and any("separate table paragraph" in h for h in o["hints"])


def test_export_filters_by_confidence_category_and_kept_regulations():
    rows = [{"id": "A", "regulation": "UN R1", "reason": "", "requirements": ["a."], "confidence": "high", "category": "device_requirement"},
            {"id": "B", "regulation": "UN R1", "reason": "", "requirements": ["b."], "confidence": "medium", "category": "conformity_of_production"},
            {"id": "C", "regulation": "UN R1", "reason": "", "requirements": ["c."], "confidence": "low", "category": "device_requirement"},
            {"id": "D", "regulation": "UN R2", "reason": "", "requirements": ["d."], "confidence": "high", "category": "device_requirement"},
            {"id": "E", "regulation": "UN R1", "reason": "", "requirements": ["e."]}]            # older row: never filtered
    recs = {k: rec(k, id=k, regulation="UN R2" if k == "D" else "UN R1") for k in "ABCDE"}
    texts = lambda **kw: [r["text"] for r in export.build_submission(rows, recs, "c", **kw)[0]["requirements"]]
    assert texts() == ["a.", "b.", "c.", "d.", "e."]
    assert texts(min_confidence="medium") == ["a.", "b.", "d.", "e."]
    assert texts(min_confidence="high") == ["a.", "d.", "e."]
    assert texts(drop_categories={"conformity_of_production"}) == ["a.", "c.", "d.", "e."]
    assert texts(keep={"UN R2"}) == ["d."]


def test_export_drop_unless_high_keeps_only_high_confidence_paragraphs_of_that_category():
    rows = [{"id": "A", "regulation": "UN R1", "reason": "", "requirements": ["a."], "confidence": "high", "category": "conformity_of_production"},
            {"id": "B", "regulation": "UN R1", "reason": "", "requirements": ["b."], "confidence": "medium", "category": "conformity_of_production"},
            {"id": "C", "regulation": "UN R1", "reason": "", "requirements": ["c."], "confidence": "medium", "category": "device_requirement"}]
    recs = {k: rec(k, id=k) for k in "ABC"}
    sub, stats = export.build_submission(rows, recs, "c", drop_unless_high={"conformity_of_production"})
    assert [r["text"] for r in sub["requirements"]] == ["a.", "c."] and stats["left_out_by_category"] == 1
