"""Tests on small synthetic PDFs: segmentation, context, structure fields, ids and input integrity."""

from __future__ import annotations

import json
import shutil

from conftest import LONG, WRAP, index_one, make_pdf, read_jsonl, run_indexer, write_index

import indexer


def main_body(*rows) -> list:
    """A first page with a scope paragraph (gives the document its body style), followed by `rows`."""
    return ["1. Scope", (f"1.1. This Regulation applies to test lamps {LONG}.", {"gap": 19}), *rows]


def test_wrapped_paragraph_text_is_joined(tmp_path):
    recs = index_one(tmp_path, [main_body(
        "2. The lamp shall be fitted on the vehicle so that its reference axis is parallel to the",
        ("ground and directed rearwards at all times, " + LONG + ".", {"gap": WRAP}),
        "3. The device shall be retro-" + "reflective in daylight and at night " + LONG,
        ("reflective surfaces included.", {"gap": WRAP}),
    )])
    two = recs["2"]
    assert two["text"].startswith("The lamp shall be fitted on the vehicle so that its reference axis is parallel to "
                                  "the ground and directed rearwards")
    assert len(two["source_line_ids"]) == 2
    assert two["obligation_signals"]["shall"] == 1 and two["has_obligation"]


def test_reference_wrapped_onto_new_line_is_not_a_paragraph(tmp_path):
    recs = index_one(tmp_path, [main_body(
        "5. General requirements",
        "5.1. The lamp shall comply with the photometric requirements given in paragraph",
        ("5.1.3.2.2. of this Regulation, " + LONG + ".", {"gap": WRAP}),
        "5.2. The lamp shall be marked with the approval mark " + LONG + ".",
    )])
    assert "5.1.3.2.2" not in recs
    assert {"5", "5.1", "5.2"} <= set(recs)
    assert "paragraph 5.1.3.2.2. of this Regulation" in recs["5.1"]["text"]
    ref = next(x for x in recs["5.1"]["cross_refs"] if x["kind"] == "paragraph")
    assert ref["ref"] == "5.1.3.2.2" and ref["qualifier"] == "of this Regulation" and ref["status"] == "unresolved"


def test_annex_and_part_context(tmp_path):
    recs = index_one(tmp_path, [
        main_body("2. The lamp shall pass the tests of Annex 6 " + LONG + "."),
        [("Annex 6 Environmental testing", {"bold": True, "size": 13}),
         ("Part 4 - Resistance to corrosion", {"bold": True}),
         "1. The samples shall be exposed to salt mist for 24 hours " + LONG + ".",
         "1.1. The temperature shall be kept at 35 °C during the whole test period " + LONG + "."],
    ])
    one, sub = recs["Annex 6 Part 4 1"], recs["Annex 6 Part 4 1.1"]
    assert (one["annex"], one["part"], one["page"]) == ("6", "4", 2)
    assert sub["breadcrumb"] == ["Annex 6 Environmental testing", "Part 4 Resistance to corrosion", "1"]
    assert sub["parent_id"] == one["id"] and sub["id"] in one["children_ids"]
    assert one["parent_id"] == recs["Annex 6 Part 4"]["id"]                     # the part's title entry
    assert recs["Annex 6 Part 4"]["parent_id"] == recs["Annex 6"]["id"]         # ... inside the annex
    assert recs["Annex 6"]["title"] == "Environmental testing"
    assert sub["parent_chain"][-1] == recs["Annex 6"]["id"]
    ref = next(x for x in recs["2"]["cross_refs"] if x["kind"] == "annex")
    assert ref["status"] == "resolved" and ref["target_id"] == recs["Annex 6"]["id"]


def test_titles_heading_values_and_inherited_obligations(tmp_path):
    recs = index_one(tmp_path, [main_body(
        "2. The lamps shall be installed so that they remain visible in normal use,",
        ("including when the doors are open, " + LONG + ",", {"gap": WRAP}),
        ("and when the vehicle is loaded " + LONG + ",", {"gap": WRAP}),
        ("or towing a trailer " + LONG + ".", {"gap": WRAP}),
        "6. Individual specifications",
        "6.1. Rear position lamp",
        "6.1.1. Number",
        "Two, of an approved type, " + LONG + ".",
        "6.1.2. In height: not less than 350 mm above the ground " + LONG + ".",
        "6.2. The lamps shall be:",
        "6.2.1. red, if fitted at the rear " + LONG + ";",
    )])
    assert recs["6.1"]["title"] == "Rear position lamp"
    assert recs["6.1.1"]["title"] == "Number" and recs["6.1.1"]["heading_value_item"]
    assert recs["6.1.2"]["title"] == "In height" and recs["6.1.2"]["heading_value_item"]
    assert recs["6.1.1"]["breadcrumb"] == ["6 Individual specifications", "6.1 Rear position lamp"]
    item = recs["6.2.1"]
    assert not item["has_obligation"] and item["inherits_obligation"]
    assert item["inherits_obligation_from"] == recs["6.2"]["id"]
    assert [c["signal"] for c in item["condition_signals"]] == ["if_fitted"]


def test_pages_keep_every_line_with_flags(tmp_path):
    recs = index_one(tmp_path, [main_body("2. The lamp shall be red " + LONG + ".")])
    page = recs["_result"]["pages"][0]
    header = [ln for ln in page["lines"] if ln["running_header"]]
    assert len(header) == 1 and not header[0]["assigned_to_paragraph"]
    by_id = {ln["id"]: ln for ln in page["lines"]}
    for lid in recs["2"]["source_line_ids"]:
        assert by_id[lid]["paragraph_id"] == recs["2"]["id"]


def _corpus(root):
    """Same PDF as base and latest of one regulation, plus a sub-goal pair."""
    rows = [main_body("2. The lamp shall be red " + LONG + ".")]
    base = make_pdf(root / "regulations" / "pdf" / "UN_R999" / "base" / "UN_R999.pdf", rows)
    latest = root / "regulations" / "pdf" / "UN_R999" / "latest" / "UN_R999.pdf"
    latest.parent.mkdir(parents=True)
    shutil.copyfile(base, latest)
    write_index(root, [{"regulation": "UN R999", "versions": [("base", "1.0.0", base), ("latest", "1.0.0", latest)]}])
    case = root / "subgoal" / "case1"
    files = [make_pdf(case / name, rows, reg="UN R998", ver=ver) for name, ver in (("old.pdf", "0.1"), ("new.pdf", "0.2"))]
    (case / "files.json").write_text(json.dumps([{"name": f.name, "sha256": __import__("conftest").sha(f)}
                                                 for f in files]))
    return base


def test_unique_ids_across_roles(tmp_path):
    _corpus(tmp_path)
    assert run_indexer(tmp_path) == 0
    recs = read_jsonl(tmp_path / "build" / "paragraphs.jsonl")
    assert {r["role"] for r in recs} == {"base", "latest", "subgoal_old", "subgoal_new"}
    assert len({r["id"] for r in recs}) == len(recs)
    by_role = {role: {r["paragraph"] for r in recs if r["role"] == role} for role in ("base", "latest")}
    assert by_role["base"] == by_role["latest"]                       # same labels, different ids
    assert "UN R999|1.0.0|base|UN_R999.pdf|2#1" in {r["id"] for r in recs}
    assert any(r["file"] == "subgoal/case1/old.pdf" for r in recs)
    assert indexer.duplicate_ids([{"id": "a"}, {"id": "b"}, {"id": "a"}]) == ["a"]


def test_missing_pdf_fails_loudly(tmp_path, capsys):
    base = _corpus(tmp_path)
    base.unlink()
    assert run_indexer(tmp_path) == 1
    assert "MISSING" in capsys.readouterr().err
    assert not (tmp_path / "build" / "paragraphs.jsonl").exists()      # nothing written
    assert run_indexer(tmp_path, "--allow-missing") == 0                 # debugging escape hatch
    manifest = json.loads((tmp_path / "build" / "manifest.json").read_text())
    assert len(manifest["skipped_files"]) == 1


def test_checksum_mismatch_fails(tmp_path, capsys):
    base = _corpus(tmp_path)
    base.write_bytes(base.read_bytes() + b"\n% tampered\n")
    assert run_indexer(tmp_path) == 1
    assert "CHECKSUM" in capsys.readouterr().err


def test_slug_matches_fetch_regulations():
    assert indexer.slug("UN R13-H") == "UN_R13-H"
    assert indexer.slug("UN GTR 9 / Amend 1") == "UN_GTR_9_Amend_1"
