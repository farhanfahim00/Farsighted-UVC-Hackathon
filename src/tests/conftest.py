"""Shared helpers: build small synthetic PDFs in the Certivity style (running header, Helvetica body at x=57)."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pymupdf
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

BODY, WRAP, PARA = 10.5, 15, 19   # body font size; distance to a wrapped line / to a new paragraph


def make_pdf(path: Path, pages: list[list], reg: str = "UN R999", ver: str = "1.0.0") -> Path:
    """pages: per page a list of rows; a row is "text" (body, new paragraph) or (text, {"bold", "size", "gap"}).
    gap is the distance from the previous row (default: a paragraph break)."""
    doc = pymupdf.open()
    for i, rows in enumerate(pages, start=1):
        page = doc.new_page(width=595, height=842)
        y = 60.0
        for row in rows:
            text, opt = (row, {}) if isinstance(row, str) else row
            y += opt.get("gap", PARA)
            page.insert_text((57, y), text, fontname="hebo" if opt.get("bold") else "helv",
                             fontsize=opt.get("size", BODY))
        page.insert_text((228, 815), f"{reg}, version {ver} | page {i} of {len(pages)}", fontname="helv", fontsize=8)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(path)
    return path


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_index(root: Path, entries: list[dict]) -> Path:
    """regulations/index.json for entries {"regulation", "versions": [(role, label, pdf path)]}."""
    data = []
    for e in entries:
        versions = [{"role": role, "version_label": label, "files": [{"name": pdf.name, "sha256": sha(pdf)}]}
                    for role, label, pdf in e["versions"]]
        data.append({"regulation": e["regulation"], "title": e.get("title", "Test lamps"), "versions": versions})
    path = root / "regulations" / "index.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def run_indexer(root: Path, *extra: str) -> int:
    import indexer
    return indexer.main(["--index", str(root / "regulations" / "index.json"), "--pdf-dir", str(root / "regulations" / "pdf"),
                         "--subgoal-root", str(root / "subgoal"), "--out", str(root / "build"), "--jobs", "1", *extra])


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def index_one(tmp_path: Path, pages: list[list]) -> dict:
    """Index one synthetic PDF in memory; returns {label: record} plus the raw result under "_result"."""
    import indexer
    pdf = make_pdf(tmp_path / "doc.pdf", pages)
    res = indexer.process_target({"regulation": "UN R999", "title": "Test", "version": "1.0.0", "role": "base",
                                  "file": "doc.pdf", "path": str(pdf), "sha256": sha(pdf), "subgoal_case": None,
                                  "verified": True})
    out = {r["paragraph"]: r for r in res["records"]}
    out["_result"] = res
    return out


LONG = "with enough words on this line to look like ordinary body text of the regulation"


@pytest.fixture
def long_text() -> str:
    return LONG
