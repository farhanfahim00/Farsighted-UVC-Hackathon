"""Helpers for the Farsighted demo viewer (no Streamlit rendering here, except read errors)."""
from __future__ import annotations

import difflib
import html
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
  .f-card {font-size:.84rem; line-height:1.42; color:#364f5f;}
  .f-card h4 {color:var(--f-navy); font-size:.98rem; margin:.05rem 0 .35rem;}
  .f-card b {color:var(--f-teal); font-size:.68rem; letter-spacing:.1em; text-transform:uppercase; display:block; margin-top:.4rem;}
  .f-bar-row {display:flex; align-items:center; gap:.6rem; font-size:.82rem; margin:.2rem 0; color:var(--f-navy);}
  .f-bar-label {width:34%; text-align:right; overflow:hidden; text-overflow:ellipsis; white-space:nowrap;}
  .f-bar-track {flex:1; background:#f0f4f5; height:.9rem; border-radius:3px;}
  .f-bar-fill {height:100%; border-radius:3px;}
  .f-bar-val {width:3.2rem; font-weight:660;}
  .f-scroll {max-height:340px; overflow-y:auto;}
  .f-chip {display:inline-block; border:1px solid var(--f-teal); border-radius:999px; padding:.18rem .7rem; margin:.2rem .25rem .2rem 0; font-size:.83rem; color:var(--f-navy); background:var(--f-teal-pale);}
  .f-chip i {font-style:normal; background:var(--f-teal); color:#fff; border-radius:999px; padding:0 .45rem; margin-left:.45rem; font-size:.75rem; font-weight:660;}
  .f-chip.zero {opacity:.45;} .f-chip.zero i {background:#8a9aa4;}
  .f-pill {display:inline-block; border-radius:999px; padding:.12rem .7rem; margin-right:.4rem; font-size:.78rem; font-weight:660; color:#fff;}
  .f-pill.added {background:#2e8b57;} .f-pill.modified {background:#d98e04;} .f-pill.removed {background:#c0392b;}
  .f-stack {display:flex; height:.7rem; border-radius:3px; overflow:hidden; margin:.35rem 0 .6rem;}
  .f-diff {font-size:.9rem; line-height:1.6; border:1px solid var(--f-rule); border-radius:6px; padding:.6rem .8rem;}
  .f-del {color:#b3261e; text-decoration:line-through; background:#fdecea;}
  .f-ins {color:#1b6e3c; background:#e6f4ea;}
  .f-strip {background:var(--f-navy); color:#fff; font-size:.82rem; padding:.4rem .8rem; border-radius:4px 4px 0 0; font-weight:600;}
  .f-req {font-size:.78rem; line-height:1.3; color:#26414f;}
  .f-req b {color:var(--f-navy);}
  .f-req small {display:block; color:var(--f-muted); font-size:.7rem; margin-top:.15rem;}
  .st-key-reqpane [data-testid="stVerticalBlock"] {gap:.3rem;}
  .st-key-reqpane [data-testid="stVerticalBlockBorderWrapper"] {padding:.3rem .55rem;}
  .st-key-reqpane button p, .st-key-reqpane label p {font-size:.74rem;}
  .f-title-sm {color:var(--f-navy); font-weight:660; font-size:1.05rem; letter-spacing:-.01em;}
  .st-key-ministep button {padding:.1rem .2rem; min-height:0;}
  .st-key-ministep button p {font-size:.74rem; white-space:nowrap;}
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
    return str(sub_glob_dir(root) / "<run-name>" / "submission_subgoal.json")


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
def render_pdf_page(path: str, page: int, zoom: float = 2.0) -> bytes | None:
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


# ---- Recorded cases -------------------------------------------------------

UPLOAD_CHOICE = "Upload my own"
CASES = {"Rear lamp (dev)": "dev-rear-lamp", "Front bumper (held-out)": "heldout-front-bumper"}


def case_main_path(root: Path, case_id: str) -> Path:
    """Recorded main JSON for a case: build/cases/<case-id>/submission.json."""
    return (root / "build" / "cases" / case_id / "submission.json").resolve()


def case_inputs(root: Path, case_id: str, fallback_spec: Path | None = None,
                fallback_image: Path | None = None) -> tuple[bytes, str, bytes | None, str]:
    """(spec bytes, spec name, drawing bytes, drawing name) from cases/<case-id>/, with optional fallbacks."""
    folder = root / "cases" / case_id
    spec_p = folder / "spec.md"
    img_p = folder / "exploded_view.png"
    if not spec_p.is_file() and fallback_spec and fallback_spec.is_file():
        spec_p = fallback_spec
    if not img_p.is_file() and fallback_image and fallback_image.is_file():
        img_p = fallback_image
    try:
        spec = (spec_p.read_bytes(), str(spec_p)) if spec_p.is_file() else (b"", "")
        img = (img_p.read_bytes(), str(img_p)) if img_p.is_file() else (None, "")
    except OSError:
        spec, img = (b"", ""), (None, "")
    return spec[0], spec[1], img[0], img[1]


# ---- Why step -------------------------------------------------------------

WHY_CARDS = [
    ("Citations",
     "A page number only means something with its version.",
     "Our indexer gives every paragraph and table an ID, version and page; the model cites by ID and a guard maps it back to a real page.",
     "Every requirement opens its true source page."),
    ("Product profile",
     "Which regulations apply depends on the product.",
     "We build the profile from spec and drawing; unsupported facts stay unknown and never exclude a regulation.",
     "The spec and drawing the profile is read from."),
    ("Recall first",
     "A missed requirement costs more than an extra one.",
     "Disputed candidates stay visible, and each requirement is one atomic obligation.",
     "The engineer prunes with Keep/Discard."),
    ("Change over time",
     "Regulations change between versions.",
     "Old and new regulation versions are compared paragraph by paragraph.",
     "Each affected requirement gets a before/after and a reason."),
]

WHY_METRICS = "Development case, team-reported: precision 39.8%, recall 85.3%, F2 69.4%. Tuned for recall-weighted F2."

# Index = step number (1-5); drawn from the four points above.
STEP_WHY = {
    1: "Why this step: the profile is built only from what you supply, so the input decides which regulations apply.",
    2: "Why this step: unsupported facts stay unknown; check the evidence the product profile is read from.",
    3: "Why this step: a page number only means something with its version, so each regulation is listed with its versions.",
    4: "Why this step: recall comes first, so the engineer prunes with Keep/Discard and opens each true source page.",
    5: "Why this step: export only what you kept, and review how affected requirements changed between versions.",
}


# ---- Visuals --------------------------------------------------------------

TEAL, NAVY = "#167d82", "#18374b"
CHANGE_COLORS = {"added": "#2e8b57", "modified": "#d98e04", "removed": "#c0392b"}


def hbar_html(items: list[tuple[str, int]], colors: list[str] | None = None, scroll: bool = False, top: int | None = None) -> str:
    """Horizontal bars in plain HTML; widths are relative to the largest value."""
    top = top or max((v for _, v in items), default=0) or 1
    rows = []
    for n, (label, val) in enumerate(items):
        color = (colors or [TEAL])[n % len(colors or [TEAL])]
        width = max(2.0, 100.0 * val / top) if val else 0
        rows.append(
            f'<div class="f-bar-row"><div class="f-bar-label" title="{html.escape(label)}">{html.escape(label)}</div>'
            f'<div class="f-bar-track"><div class="f-bar-fill" style="width:{width:.1f}%;background:{color}"></div></div>'
            f'<div class="f-bar-val">{val:,}</div></div>')
    return f'<div class="{"f-scroll" if scroll else ""}">' + "".join(rows) + "</div>"


@lru_cache(maxsize=4)
def index_regulation_count(path: str) -> int | None:
    """Number of regulations listed in regulations/index.json (count only)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        return len(data) if isinstance(data, list) else None
    except (OSError, ValueError):
        return None


def parse_main_parts(spec_text: str) -> list[str]:
    """Bullet items under the '## Main Parts' heading of a spec."""
    parts, inside = [], False
    for line in spec_text.splitlines():
        if line.lstrip().startswith("#"):
            inside = line.lstrip("# ").strip().casefold() == "main parts"
            continue
        m = re.match(r"\s*[-*]\s+(.+)", line)
        if inside and m:
            parts.append(m.group(1).strip())
    return parts


def _part_aliases(part: str) -> list[str]:
    """Part name, without parentheses, the parenthetical, and each '/' alternative (min 3 chars)."""
    base = re.sub(r"\(.*?\)", " ", part)
    alts = {part, base, *re.findall(r"\((.*?)\)", part), *re.split(r"/", base)}
    return [a.strip().casefold() for a in alts if len(a.strip()) >= 3]


def part_mention_count(rows: list[dict[str, Any]], part: str) -> int:
    aliases = _part_aliases(part)
    return sum(1 for r in rows if any(a in str(r.get("text", "")).casefold() for a in aliases))


def clean_part_name(part: str) -> str:
    """Drop stray leading brackets: '(Radiator) Grill' -> 'Radiator Grill'."""
    name = re.sub(r"^[(\[{]\s*([^)\]}]*?)\s*[)\]}]\s*", r"\1 ", part.strip())
    return name.lstrip("([{)]} ").strip() or part.strip()


def chips_html(parts_with_counts: list[tuple[str, int]]) -> str:
    return "".join(f'<span class="f-chip{" zero" if c == 0 else ""}">{html.escape(clean_part_name(p))}<i>{c}</i></span>'
                   for p, c in parts_with_counts)


def pill_html(kind: str, label: str | None = None) -> str:
    k = kind if kind in CHANGE_COLORS else "modified"
    return f'<span class="f-pill {k}">{html.escape(label or kind)}</span>'


def stacked_bar_html(counts: dict[str, int]) -> str:
    total = sum(counts.values()) or 1
    segs = "".join(f'<div title="{k}: {v}" style="width:{100 * v / total:.1f}%;background:{CHANGE_COLORS[k]}"></div>'
                   for k, v in counts.items() if v and k in CHANGE_COLORS)
    return f'<div class="f-stack">{segs}</div>'


def word_diff_html(old: str, new: str) -> str:
    """Word-level diff: deletions red struck-through, insertions green."""
    a, b = re.findall(r"\s+|\S+", old or ""), re.findall(r"\s+|\S+", new or "")
    out = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes():
        if op == "equal":
            out.append(html.escape("".join(a[i1:i2])))
        else:
            if i2 > i1:
                out.append(f'<span class="f-del">{html.escape("".join(a[i1:i2]))}</span>')
            if j2 > j1:
                out.append(f'<span class="f-ins">{html.escape("".join(b[j1:j2]))}</span>')
    return f'<div class="f-diff">{"".join(out)}</div>'


def citation_strip(regulation: Any, version: Any, page: Any, paragraph: Any) -> str:
    bits = [str(regulation or "—"), f"v{version}" if version else "v—", f"p. {page}" if page else "p. —", f"para {paragraph}" if paragraph else "para —"]
    return f'<div class="f-strip">{html.escape(" · ".join(bits))}</div>'


# ---- Before text for changes and submission validation ---------------------

def case_folder(case_id: str) -> str | None:
    """'dev' or 'heldout' from a case id such as 'subgoal-dev' or 'heldout-front-bumper'."""
    key = (case_id or "").lower()
    if "heldout" in key or "held-out" in key:
        return "heldout"
    if "dev" in key:
        return "dev"
    return None


def old_requirements_path(root: Path, case_id: str) -> Path | None:
    folder = case_folder(case_id)
    return (root / "subgoal" / folder / "requirements_old.json").resolve() if folder else None


def load_old_requirements(root: Path, case_id: str) -> tuple[dict[str, str], str]:
    """(requirement_id -> text, problem). problem is '' when the file was read."""
    path = old_requirements_path(root, case_id)
    if path is None:
        return {}, f"Cannot tell which case folder ('dev' or 'heldout') case_id '{case_id}' belongs to."
    try:
        rows = json.loads(path.read_text(encoding="utf-8-sig"))
        return {str(r["id"]): str(r.get("text", "")) for r in rows if isinstance(r, dict) and "id" in r}, ""
    except FileNotFoundError:
        return {}, f"Missing file: {path}"
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return {}, f"Cannot read {path}: {exc}"


def run_validate(root: Path, data: dict[str, Any]) -> tuple[str, list[str]]:
    """Run tools/validate.py on `data`. Returns (PASS|FAIL|NOT RUN, first output lines)."""
    import subprocess
    import sys
    import tempfile

    script = root / "tools" / "validate.py"
    if not script.is_file():
        return "NOT RUN", [f"Missing file: {script}"]
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "kept_submission.json"
        target.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, str(script), str(target)]
        index = root / "regulations" / "index.json"
        if index.is_file():
            cmd += ["--index", str(index)]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=90, cwd=root)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return "NOT RUN", [str(exc)]
    lines = [ln for ln in (proc.stdout + proc.stderr).splitlines() if ln.strip()]
    if proc.returncode == 0:
        return "PASS", lines[:10]
    return ("FAIL" if proc.returncode == 1 else "NOT RUN"), lines[:10]
