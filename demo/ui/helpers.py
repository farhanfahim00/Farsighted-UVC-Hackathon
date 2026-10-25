"""Helpers for the Farsighted demo viewer (no Streamlit rendering here, except read errors)."""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any

MAX_BYTES = 12_000_000

THEME_CSS = """
<style>
  :root {
    --f-navy: #18374b;
    --f-teal: #167d82;
    --f-teal-pale: #eaf4f4;
    --f-muted: #637481;
    --f-rule: #e4eaec;
  }
  #MainMenu, footer, header[data-testid="stHeader"] { visibility: hidden; }
  .block-container {max-width: 1180px; padding-top: 1.65rem; padding-bottom: 2rem;}
  h1, h2, h3 { color: var(--f-navy); letter-spacing: -.018em; }
  h1 { font-size: 2.22rem !important; font-weight: 680 !important; margin: .10rem 0 .28rem !important; }
  h2 { font-size: 1.35rem !important; font-weight: 650 !important; }
  h3 { font-size: 1.10rem !important; font-weight: 620 !important; }
  p { line-height: 1.52; }
  [data-testid="stMetric"] {border-top: 2px solid var(--f-teal); padding: .75rem 0 .2rem;}
  [data-testid="stMetricValue"] {color:var(--f-navy); font-size:1.65rem!important;}
  [data-testid="stMetricLabel"] {color:var(--f-muted); font-size:.82rem;}
  [data-testid="stDataFrame"] {border: 1px solid var(--f-rule); border-radius: 6px;}
  .f-kicker { color:var(--f-teal); font-size:.71rem; letter-spacing:.13em; font-weight:700; text-transform:uppercase; }
  .f-intro {color:#526774; font-size:1.02rem; line-height:1.55; max-width:760px; margin-bottom:.55rem;}
  .f-note { color:var(--f-muted); font-size:.83rem; line-height:1.48; }
  .f-meta {color:var(--f-muted); font-size:.81rem; border-top:1px solid var(--f-rule); padding-top:.7rem; margin-top:.35rem;}
  .f-decision {border-left:3px solid var(--f-teal); padding:.35rem 0 .35rem .8rem; margin:.9rem 0; color:var(--f-navy); font-weight:560;}
  .f-rule {border-top:1px solid var(--f-rule); margin:.65rem 0 1.2rem;}
  .f-stepper {display:flex; gap:.4rem; margin:.4rem 0 .2rem;}
  .f-step {flex:1; border-top:3px solid var(--f-rule); padding:.45rem .1rem 0; font-size:.84rem; color:var(--f-muted);}
  .f-step b {display:block; font-size:.70rem; letter-spacing:.12em; color:var(--f-muted);}
  .f-step.done {border-top-color:var(--f-teal);}
  .f-step.done b {color:var(--f-teal);}
  .f-step.current {border-top-color:var(--f-navy); color:var(--f-navy); font-weight:660;}
  .f-step.current b {color:var(--f-navy);}
  .f-empty {border:1px dashed var(--f-teal); background:var(--f-teal-pale); border-radius:6px; padding:1rem 1.2rem; color:var(--f-navy);}
  .f-empty code {font-size:.84rem;}
  @media (max-width:720px) {.block-container{padding-top:.65rem;} h1{font-size:1.8rem!important;} .f-step{font-size:.72rem;}}
</style>
"""


def default_repo_root() -> Path:
    """Root holding `build/` and `runs/`. FARSIGHTED_REPO_DIR overrides; otherwise the CWD."""
    env = os.environ.get("FARSIGHTED_REPO_DIR", "").strip()
    return Path(env).expanduser() if env else Path.cwd()


def main_glob_dir(root: Path) -> Path:
    return (root / "build" / "cases").resolve()


def sub_glob_dir(root: Path) -> Path:
    return (root / "runs").resolve()


def expected_main_pattern(root: Path) -> str:
    return str(main_glob_dir(root) / "<case_id>" / "submission*.json")


def expected_sub_pattern(root: Path) -> str:
    return str(sub_glob_dir(root) / "<run_id>" / "submission_subgoal.json")


def _newest(folder: Path, pattern: str) -> list[Path]:
    try:
        if not folder.is_dir():
            return []
        found = [p for p in folder.glob(pattern) if p.is_file()]
        return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)[:100]
    except (OSError, ValueError):
        return []


def generated_main_files(root: Path) -> list[Path]:
    return _newest(main_glob_dir(root), "*/submission*.json")


def generated_subgoal_files(root: Path) -> list[Path]:
    return _newest(sub_glob_dir(root), "*/submission_subgoal.json")


def load_json(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_BYTES:
        raise ValueError("This viewer accepts JSON files up to 12 MB")
    val = json.loads(data.decode("utf-8-sig"))
    if not isinstance(val, dict):
        raise ValueError("The root of the JSON must be an object")
    return val


def read_submission(upload: Any, path: Path | None, which: str) -> tuple[dict[str, Any] | None, str, str]:
    """Return (data, label, error). Error is '' on success."""
    if upload is None and path is None:
        return None, "", ""
    try:
        data = load_json(upload.getvalue() if upload is not None else path.read_bytes())
        key = "requirements" if which == "main" else "changes"
        if not isinstance(data.get(key), list):
            raise ValueError(f"Expected an organizer-format '{key}' array")
        for i, row in enumerate(data[key]):
            if not isinstance(row, dict):
                raise ValueError(f"Row {i + 1} is not an object")
        name = f"Uploaded: {upload.name}" if upload is not None else str(path)
        return data, name, ""
    except (UnicodeError, OSError, json.JSONDecodeError, ValueError) as exc:
        return None, "", f"Cannot read {which} submission: {exc}"


def source_label(source: dict[str, Any] | None, include_reg: bool = True) -> str:
    s = source or {}
    parts = []
    if include_reg and s.get("regulation"):
        parts.append(str(s["regulation"]))
    if include_reg and s.get("version"):
        parts.append("version " + str(s["version"]))
    if s.get("page"):
        parts.append("p. " + str(s["page"]))
    if s.get("paragraph"):
        parts.append("§ " + str(s["paragraph"]))
    return " · ".join(parts) if parts else "Source not supplied"


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def duplicate_count(rows: list[dict[str, Any]], field: str) -> int:
    nonempty = [normalize_text(r.get(field)) for r in rows if isinstance(r, dict)]
    counts = Counter(x for x in nonempty if x)
    return sum(c - 1 for c in counts.values() if c > 1)


def structural_warnings(data: dict[str, Any], which: str) -> list[str]:
    key = "requirements" if which == "main" else "changes"
    alerts: list[str] = []
    for i, r in enumerate(data.get(key, []), 1):
        src = r.get("source") or {}
        if which == "main":
            if not isinstance(r.get("text"), str) or not r.get("text", "").strip():
                alerts.append(f"Row {i}: empty requirement")
            if not all(src.get(k) for k in ("regulation", "version", "page")):
                alerts.append(f"Row {i}: incomplete regulation/version/page citation")
            if len(r.get("text") or "") > 2000:
                alerts.append(f"Row {i}: text longer than 2,000 characters")
        else:
            change = r.get("change")
            if change not in {"added", "modified", "removed"}:
                alerts.append(f"Row {i}: invalid change type")
            if change in {"added", "modified"} and not (r.get("new_text") or "").strip():
                alerts.append(f"Row {i}: missing replacement text")
            if change != "removed" and not src.get("page"):
                alerts.append(f"Row {i}: missing page citation")
            if len(r.get("new_text") or "") > 2000:
                alerts.append(f"Row {i}: new_text longer than 2,000 characters")
    return alerts


def trim(value: Any, n: int = 115) -> str:
    val = re.sub(r"\s+", " ", str(value or "")).strip()
    return val[: n - 1] + "…" if len(val) > n else val


def case_family(case_id: str) -> str:
    """For labeling only. Never infer quality from the case identifier."""
    key = (case_id or "").lower()
    if "heldout" in key or "held-out" in key:
        return "Held-out"
    if "dev" in key:
        return "Development"
    return "Unspecified" if key else "No case loaded"


# ---- Regulation sources ---------------------------------------------------

@lru_cache(maxsize=4)
def load_reg_index(path: str) -> dict[tuple[str, str], str]:
    """Map (regulation, version_label) -> pdf_url from regulations/index.json. Empty if unreadable."""
    out: dict[tuple[str, str], str] = {}
    try:
        entries = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        for e in entries:
            for v in e.get("versions", []):
                files = v.get("files") or []
                if files and files[0].get("pdf_url"):
                    out[(str(e.get("regulation")), str(v.get("version_label")))] = files[0]["pdf_url"]
    except (OSError, ValueError, AttributeError, TypeError):
        pass
    return out


def pdf_url_for(root: Path, regulation: str, version: str) -> str | None:
    return load_reg_index(str(root / "regulations" / "index.json")).get((str(regulation), str(version)))


def local_pdf(root: Path, regulation: str) -> Path | None:
    """First regulations/pdf/<regulation>/base/*.pdf (also tries spaces replaced by underscores)."""
    for name in dict.fromkeys([regulation, regulation.replace(" ", "_")]):
        if not name or "/" in name or "\\" in name or ".." in name:
            continue
        found = sorted((root / "regulations" / "pdf" / name / "base").glob("*.pdf"))
        if found:
            return found[0]
    return None


@lru_cache(maxsize=32)
def render_pdf_page(path: str, page: int, zoom: float = 1.6) -> bytes | None:
    """PNG bytes of a 1-based page, or None if the page is out of range / unreadable."""
    import fitz  # pymupdf

    try:
        with fitz.open(path) as doc:
            if not 1 <= page <= doc.page_count:
                return None
            return doc[page - 1].get_pixmap(matrix=fitz.Matrix(zoom, zoom)).tobytes("png")
    except Exception:
        return None


def kept_rows(rows: list[dict[str, Any]], keep: dict[int, bool]) -> list[dict[str, Any]]:
    return [r for i, r in enumerate(rows) if keep.get(i, True)]
