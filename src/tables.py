"""Table cells via pymupdf's find_tables(), linked to the paragraph that owns each table.

find_tables() runs with use_layout=False: the default gates tables with pymupdf's neural layout model (onnxruntime,
its own worker processes, ~7x slower); the plain ruling-line detector is deterministic and needs no model. It finds
nothing on pages without vector drawings, so it only runs on pages that have a table caption, or a table-like paragraph
(small / indented cell lines) plus drawings.
Ownership is never guessed: a caption gives the owner of its table entry; otherwise the owner is the single paragraph
whose lines lie inside the table, and several such paragraphs make the ownership "ambiguous" (all candidates listed).
"""

from __future__ import annotations

import re

MINUS_RE = re.compile(r"^[-−–]$|^[-−–]\s+\d")
EDGE = 70   # pt from the top / bottom edge of the page: a table there may continue on the neighbouring page


def _center_inside(bbox, rect, pad: float = 1.0) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2
    return rect[0] - pad <= cx <= rect[2] + pad and rect[1] - pad <= cy <= rect[3] + pad


def _cell(v) -> str | None:
    return None if v is None else " ".join(str(v).split())


def extract(pdf, lines: list, paras: list, records: list[dict], para_ids: dict, doc: str, t: dict) -> list[dict]:
    """tables.jsonl rows for one document; also fills records[...]["table_ids"] of the owning paragraphs."""
    by_id = {r["id"]: r for r in records}
    by_page: dict[int, list] = {}
    for l in lines:
        by_page.setdefault(l.page, []).append(l)
    caption_pages = {l.page for l in lines if l.kind == "caption"}
    cell_pages = {pg for p in paras if p.has_table and p.kind != "table" for pg in range(p.page, p.page_end + 1)}
    pages = sorted(caption_pages | {pg for pg in cell_pages - caption_pages if len(pdf[pg - 1].get_cdrawings()) >= 4})

    def line_id(l) -> str:
        return f"{doc}:p{l.page}:l{l.n}"

    def owner_of(para):
        """The paragraph a line belongs to, seen through table entries (a table entry belongs to its owner)."""
        if para is None or id(para) not in para_ids:
            return None
        return para.owner if para.is_table and para.owner is not None and id(para.owner) in para_ids else para

    out = []
    for pg in pages:
        page = pdf[pg - 1]
        try:
            found = page.find_tables(strategy="lines", use_layout=False).tables
        except Exception as exc:   # keep the evidence that extraction failed on this page
            out.append({"id": f"{doc}|p{pg}|t0", "doc_id": doc, "regulation": t["regulation"],
                        "version": t["version"], "role": t["role"], "file": t["file"], "page": pg, "bbox": None,
                        "n_rows": 0, "n_cols": 0, "caption": None, "caption_line_id": None, "cells": [],
                        "owner_paragraph_id": None, "owner_status": "unassigned", "owner_candidates": [],
                        "table_entry_id": None, "source_line_ids": [],
                        "risk_flags": [f"extraction_failed: {type(exc).__name__}"]})
            continue
        captions = sorted((l for l in by_page.get(pg, []) if l.kind == "caption"), key=lambda l: l.bbox[1])
        used: set[int] = set()
        for k, tb in enumerate(found, start=1):
            bbox = tuple(round(v, 2) for v in tb.bbox)
            cells = [[_cell(v) for v in row] for row in tb.extract()]
            inside = [l for l in by_page.get(pg, []) if l.kind != "caption" and _center_inside(l.bbox, bbox)]
            # caption: the nearest unused caption line just above the table (or inside its frame)
            cap = None
            for l in captions:
                if id(l) not in used and (bbox[1] - 120 <= l.bbox[3] <= bbox[1] + 3 or _center_inside(l.bbox, bbox)):
                    cap = l
            if cap is not None:
                used.add(id(cap))

            flags = []
            if any(v is None for row in cells for v in row):
                flags.append("merged_or_missing_cells")
            if any(v == "" for row in cells for v in row):
                flags.append("empty_cells")
            if any(v and MINUS_RE.match(v) for row in cells for v in row):
                flags.append("detached_minus_sign")
            if tb.row_count < 2 or tb.col_count < 2:
                flags.append("degenerate_shape")
            if bbox[3] > page.rect.height - EDGE:
                flags.append("may_continue_on_next_page")
            if bbox[1] < EDGE:
                flags.append("may_continue_from_previous_page")
            if cap is None:
                flags.append("no_caption")

            owners = []
            for l in inside:
                o = owner_of(l.para)
                if o is not None and o not in owners:
                    owners.append(o)
            entry_id = owner_id = None
            candidates: list[str] = []
            if cap is not None and cap.para is not None and cap.para.is_table and id(cap.para) in para_ids:
                entry_id = para_ids[id(cap.para)]
                o = owner_of(cap.para)
                owner_id, status = (para_ids[id(o)], "caption") if o is not None else (None, "unassigned")
                if any(x is not o for x in owners):
                    flags.append("lines_from_other_paragraphs")
            elif len(owners) == 1:
                owner_id, status = para_ids[id(owners[0])], "lines_in_table"
            elif owners:
                status, candidates = "ambiguous", [para_ids[id(o)] for o in owners]
            else:
                status = "unassigned"

            tid = f"{doc}|p{pg}|t{k}"
            for rid in dict.fromkeys(x for x in (owner_id, entry_id) if x):
                by_id[rid]["table_ids"].append(tid)
            out.append({
                "id": tid, "doc_id": doc, "regulation": t["regulation"], "version": t["version"], "role": t["role"],
                "file": t["file"], "page": pg, "bbox": list(bbox), "n_rows": tb.row_count, "n_cols": tb.col_count,
                "caption": cap.text if cap is not None else None,
                "caption_line_id": line_id(cap) if cap is not None else None,
                "cells": cells,
                "owner_paragraph_id": owner_id, "owner_status": status, "owner_candidates": candidates,
                "table_entry_id": entry_id,
                "source_line_ids": [line_id(l) for l in inside],
                "risk_flags": flags,
            })
    return out
