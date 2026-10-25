"""Farsighted | read-only engineering review interface.

The interface reads organizer-format main/sub-goal JSON files and optional
product evidence. It NEVER runs extraction, calls an LLM, or submits a result.
Development example data is explicitly labeled and never mixed silently with
held-out product inputs.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any

import streamlit as st

HERE = Path(__file__).resolve().parent
EXAMPLE_ROOT = HERE / "examples"
EXAMPLE_SPEC = EXAMPLE_ROOT / "rear-lamp-spec.md"
EXAMPLE_IMAGE = EXAMPLE_ROOT / "exploded_view.png"
EXAMPLE_SUB = EXAMPLE_ROOT / "subgoal-v3-submission.json"
MAX_BYTES = 12_000_000


def load_json(data: bytes) -> dict[str, Any]:
    if len(data) > MAX_BYTES:
        raise ValueError("This viewer accepts JSON files up to 12 MB")
    val = json.loads(data.decode("utf-8-sig"))
    if not isinstance(val, dict):
        raise ValueError("The root of the JSON must be an object")
    return val


def generated_main_files(repo_root: str) -> list[Path]:
    try:
        root = Path(repo_root).expanduser().resolve()
        folder = root / "build" / "cases"
        if not folder.is_dir():
            return []
        results = [p for p in folder.glob("*/submission*.json") if p.is_file()]
        return sorted(results, key=lambda p: p.stat().st_mtime, reverse=True)[:100]
    except (OSError, ValueError):
        return []


def generated_subgoal_files(repo_root: str) -> list[Path]:
    try:
        folder = Path(repo_root).expanduser().resolve() / "runs"
        if not folder.is_dir():
            return []
        results = [p for p in folder.glob("*/submission_subgoal.json") if p.is_file()]
        return sorted(results, key=lambda p: p.stat().st_mtime, reverse=True)[:100]
    except (OSError, ValueError):
        return []


def read_submission(upload: Any, path: Path | None, which: str) -> tuple[dict[str, Any] | None, str]:
    if upload is None and path is None:
        return None, ""
    try:
        data = load_json(upload.getvalue() if upload is not None else path.read_bytes())
        key = "requirements" if which == "main" else "changes"
        if not isinstance(data.get(key), list):
            raise ValueError(f"Expected an organizer-format '{key}' array")
        for i, row in enumerate(data[key]):
            if not isinstance(row, dict):
                raise ValueError(f"Row {i + 1} is not an object")
        name = f"Uploaded: {upload.name}" if upload is not None else str(path)
        return data, name
    except (UnicodeError, OSError, json.JSONDecodeError, ValueError) as exc:
        st.error(f"Cannot read {which} submission: {exc}")
        return None, ""


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
    text = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
    return text


def duplicate_count(rows: list[dict[str, Any]], field: str) -> int:
    nonempty = [normalize_text(r.get(field)) for r in rows if isinstance(r, dict)]
    counts = Counter(x for x in nonempty if x)
    return sum(c - 1 for c in counts.values() if c > 1)


def structural_warnings(data: dict[str, Any], which: str) -> list[str]:
    key = "requirements" if which == "main" else "changes"
    items = data.get(key, [])
    alerts: list[str] = []
    for i, r in enumerate(items, 1):
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


st.set_page_config(page_title="Farsighted — Engineering requirements", page_icon="📄", layout="wide", initial_sidebar_state="expanded")
st.markdown("""
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
  [data-testid="stSidebar"] {border-right:1px solid var(--f-rule); background:#f7fafb;}
  [data-testid="stMetric"] {border-top: 2px solid var(--f-teal); padding: .75rem 0 .2rem;}
  [data-testid="stMetricValue"] {color:var(--f-navy); font-size:1.65rem!important;}
  [data-testid="stMetricLabel"] {color:var(--f-muted); font-size:.82rem;}
  [data-baseweb="tab-list"] {border-bottom: 1px solid var(--f-rule); gap:.5rem;}
  .stTabs [data-baseweb="tab"] {font-size:.87rem; font-weight:580;}
  .stTabs [aria-selected="true"] {color: var(--f-teal) !important;}
  [data-testid="stDataFrame"] {border: 1px solid var(--f-rule); border-radius: 6px;}
  .f-kicker { color:var(--f-teal); font-size:.71rem; letter-spacing:.13em; font-weight:700; text-transform:uppercase; }
  .f-intro {color:#526774; font-size:1.02rem; line-height:1.55; max-width:760px; margin-bottom:.55rem;}
  .f-journey {border-left:3px solid var(--f-teal); padding:.55rem 0 .65rem .9rem; color:#364f5f; margin:.45rem 0 1.25rem; font-size:.96rem;}
  .f-stage {border-bottom: 1px solid var(--f-rule); padding:.70rem 0; font-size:.92rem;}
  .f-number {display:inline-block; width:2.55rem; font-weight:700; color:var(--f-teal);}
  .f-stage-title {font-weight:660; color:var(--f-navy);}
  .f-note { color:var(--f-muted); font-size:.83rem; line-height:1.48; }
  .f-meta {color:var(--f-muted); font-size:.81rem; border-top:1px solid var(--f-rule); padding-top:.7rem; margin-top:.35rem;}
  .f-decision {border-left:3px solid var(--f-teal); padding:.35rem 0 .35rem .8rem; margin:.9rem 0; color:var(--f-navy); font-weight:560;}
  .f-rule {border-top:1px solid var(--f-rule); margin:.65rem 0 1.2rem;}
  @media (max-width:720px) {.block-container{padding-top:.65rem;} h1{font-size:1.8rem!important;}}
</style>
""", unsafe_allow_html=True)

with st.sidebar:
    st.markdown("**FARSIGHTED**")
    st.caption("Read-only engineering review | local files")
    repository = st.text_input("Repository folder", value=os.environ.get("FARSIGHTED_REPO_DIR", r"C:\UVC2026\farsighted" if os.name == "nt" else ""))
    st.markdown("#### 01 · Main requirements")
    main_local = generated_main_files(repository) if repository else []
    chosen_main = st.selectbox(
        "Generated main JSON",
        [None] + main_local,
        format_func=lambda p: "Select a file" if p is None else f"{p.parent.name} / {p.name}",
    ) if main_local else None
    upload_main = st.file_uploader("Or upload main submission", type=["json"], key="main_upload")

    st.markdown("#### 02 · Regulation changes")
    sub_local = generated_subgoal_files(repository) if repository else []
    chosen_sub = st.selectbox(
        "Generated change JSON",
        [None] + sub_local,
        format_func=lambda p: "Select a file" if p is None else f"{p.parent.name} / {p.name}",
    ) if sub_local else None
    upload_sub = st.file_uploader("Or upload change submission", type=["json"], key="sub_upload")
    st.markdown("#### 03 · Product inputs")
    upload_spec = st.file_uploader("Specification (optional)", type=["md", "txt"], key="spec_upload")
    upload_image = st.file_uploader("Drawing (optional)", type=["png", "jpg", "jpeg", "webp"], key="drawing_upload")
    st.markdown('<div class="f-rule"></div><div class="f-note">This interface is a read-only viewer. It neither reruns the model nor submits to Certivity.</div>', unsafe_allow_html=True)

main, main_path = read_submission(upload_main, chosen_main, "main")
sub, sub_path = read_submission(upload_sub, chosen_sub, "subgoal")
main_case = (main or {}).get("case_id", "")
sub_case = (sub or {}).get("case_id", "")
main_rows = (main or {}).get("requirements", [])
sub_rows = (sub or {}).get("changes", [])

# Bundled example is a transparent fallback for demo rehearsal only.
sub_is_example = False
if sub is None and upload_sub is None and chosen_sub is None and EXAMPLE_SUB.exists() and (not main_case or case_family(main_case) == "Development"):
    sub, sub_path = read_submission(None, EXAMPLE_SUB, "subgoal")
    sub_case = (sub or {}).get("case_id", "")
    sub_rows = (sub or {}).get("changes", [])
    sub_is_example = bool(sub)

main_reg_count = len({str((r.get("source") or {}).get("regulation")) for r in main_rows if (r.get("source") or {}).get("regulation")})
reg_names = sorted({str((r.get("source") or {}).get("regulation")) for r in main_rows if (r.get("source") or {}).get("regulation")})
change_kinds = Counter(str(c.get("change")) for c in sub_rows)
heldout_mode = case_family(main_case) == "Held-out" or case_family(sub_case) == "Held-out"

st.markdown('<div class="f-kicker">Farsighted / Industrial regulatory intelligence</div>', unsafe_allow_html=True)
st.title("From a component to traceable requirements")
st.markdown('<div class="f-intro">An engineer supplies a product specification and drawing. Farsighted proposes applicable requirements, connects them to their regulatory sources and identifies what needs review when a regulation changes.</div>', unsafe_allow_html=True)
st.markdown('<div class="f-journey"><b>Product evidence</b> &nbsp;→&nbsp; <b>Regulation screening</b> &nbsp;→&nbsp; <b>Cited obligations</b> &nbsp;→&nbsp; <b>Engineering handoff</b></div>', unsafe_allow_html=True)

if main and sub and case_family(main_case) != case_family(sub_case):
    st.warning("The main and change submissions come from different case families. They are separate demonstrations, not one combined evaluation.")
if heldout_mode and not (main and sub):
    st.info("Held-out demonstration: load the appropriate case outputs. Development examples are not silently substituted for a missing held-out file.")

pages = st.tabs(["01  Story", "02  Product input", "03  Requirements", "04  Changes", "05  Handoff"])

with pages[0]:
    st.markdown("### The job to be done")
    st.write("A component engineer needs to translate a drawing and product specification into technical obligations. Searching regulation PDFs is slow; identifying the applicable duty, keeping its conditions and locating its legal source are the critical steps.")

    left, middle, right = st.columns(3)
    left.metric("Requirement proposals", str(len(main_rows)) if main else "—")
    middle.metric("Regulations referenced", str(main_reg_count) if main else "—")
    right.metric("Change proposals", str(len(sub_rows)) if sub else "—")
    st.caption("Counts describe loaded outputs only. They are not judge precision, recall or proof of compliance.")
    st.markdown("### How Farsighted gets there")
    for no, title, desc in [
        ("01", "Understand the component", "Use specification and drawing evidence. Preserve unknown facts instead of inventing them."),
        ("02", "Locate relevant regulatory text", "Index source PDFs, screen candidate regulations and retain the context around selected sections."),
        ("03", "Propose atomic engineering requirements", "Extract each distinct obligation with regulation, version, page and paragraph for inspection."),
        ("04", "Review instead of blindly trusting", "Apply configurable quality checks, conservative deduplication and structured-output validation."),
        ("05", "Keep requirements current", "Compare existing requirements against changed regulation text and flag proposed additions, modifications and removals."),
    ]:
        st.markdown(f'<div class="f-stage"><span class="f-number">{no}</span><span class="f-stage-title">{title}</span><div class="f-note" style="padding-left:2.55rem">{desc}</div></div>', unsafe_allow_html=True)
    st.markdown('<div class="f-decision">The result is a reviewable engineering handoff — not an automatic compliance certificate.</div>', unsafe_allow_html=True)
    data_info = []
    if main:
        data_info.append(f"Main: {main_case or 'unspecified'} ({case_family(main_case)})")
    if sub:
        data_info.append(f"Change: {sub_case or 'unspecified'} ({case_family(sub_case)})" + (" — bundled V3 example" if sub_is_example else ""))
    if data_info:
        st.markdown('<div class="f-meta">Loaded data — ' + ' · '.join(data_info) + '</div>', unsafe_allow_html=True)
    else:
        st.info("No pipeline output loaded. Load an actual submission JSON in the sidebar to demonstrate results.")

with pages[1]:
    st.markdown("### Start with real product evidence")
    st.caption("An engineer supplies the input; the pipeline does not infer facts that are not supported.")
    if upload_spec is not None:
        spec_bytes = upload_spec.getvalue()
        spec_name = upload_spec.name
    elif not heldout_mode and EXAMPLE_SPEC.exists():
        spec_bytes = EXAMPLE_SPEC.read_bytes()
        spec_name = "Development-case example: rear-lamp-spec.md"
    else:
        spec_bytes = b""
        spec_name = ""

    if upload_image is not None:
        image_bytes = upload_image.getvalue()
        image_name = upload_image.name
    elif not heldout_mode and EXAMPLE_IMAGE.exists():
        image_bytes = EXAMPLE_IMAGE.read_bytes()
        image_name = "Development-case example: exploded_view.png"
    else:
        image_bytes = None
        image_name = ""

    a, b = st.columns([1.15, 1], gap="large")
    with a:
        st.markdown("**Specification**")
        if spec_bytes:
            st.caption(spec_name)
            content = spec_bytes.decode("utf-8-sig", errors="replace")
            with st.container(height=435):
                st.markdown(content)
        else:
            st.info("Upload the product specification in the sidebar.")
    with b:
        st.markdown("**Drawing / exploded view**")
        if image_bytes:
            st.caption(image_name)
            st.image(image_bytes, use_container_width=True)
        else:
            st.info("Upload the product drawing in the sidebar.")
    st.caption("The model interprets supplied engineering evidence; it must not treat omitted information as a confirmed absence.")

with pages[2]:
    st.markdown("### From regulatory text to a reviewable requirement")
    st.caption("Select a requirement to inspect its full wording and the exact regulation/version/page/paragraph citation. These are proposed duties, not verified compliance findings.")
    if not main:
        st.info("Load Farhan's real organizer-format `submission.json` from the sidebar to populate this page.")
    else:
        s1, s2 = st.columns([1, 2])
        reg = s1.selectbox("Regulation", ["All"] + reg_names)
        search = s2.text_input("Search", placeholder="Search text or paragraph", key="req_search")
        visible = [r for r in main_rows if (reg == "All" or (r.get("source") or {}).get("regulation") == reg)
                   and search.casefold() in (str(r.get("text", "")) + " " + str(r.get("source", ""))).casefold()]
        st.caption(f"{len(visible)} shown · {len(main_rows)} total in the selected submission")
        grid_rows = [{
            "ID": r.get("id", ""),
            "Regulation": (r.get("source") or {}).get("regulation", ""),
            "Version": (r.get("source") or {}).get("version", ""),
            "Page": (r.get("source") or {}).get("page", ""),
            "Paragraph": (r.get("source") or {}).get("paragraph", ""),
            "Requirement": r.get("text", ""),
        } for r in visible]
        st.dataframe(grid_rows, hide_index=True, use_container_width=True, height=300)
        if visible:
            chosen = st.selectbox("Inspect evidence", range(len(visible)), format_func=lambda i: f"{visible[i].get('id','')}  ·  {trim(visible[i].get('text'), 85)}")
            item = visible[chosen]
            st.markdown("**Engineering obligation**")
            st.write(item.get("text", ""))
            st.markdown("**Source**")
            st.write(source_label(item.get("source")))
            if item.get("test_case"):
                with st.expander("Proposed test case"):
                    st.write(item["test_case"])
        st.download_button("Export displayed source JSON", json.dumps(main, ensure_ascii=False, indent=2), file_name=f"{main_case or 'main'}_submission.json", mime="application/json")

with pages[3]:
    st.markdown("### When regulations change, obligations may change too")
    st.caption("The optional pipeline identifies proposed additions, modifications or removals using existing requirements and two regulation versions.")
    if not sub:
        st.info("Load the generated sub-goal JSON in the sidebar.")
    else:
        c1, c2, c3 = st.columns(3)
        c1.metric("Added", change_kinds.get("added", 0))
        c2.metric("Modified", change_kinds.get("modified", 0))
        c3.metric("Removed", change_kinds.get("removed", 0))
        st.caption(f"Case: `{sub_case}` · {'Bundled V3 development example' if sub_is_example else 'Loaded result'} · proposals requiring review")
        q1, q2 = st.columns([1, 2])
        kind = q1.selectbox("Change type", ["All", "added", "modified", "removed"])
        query = q2.text_input("Search", placeholder="Search requirement, reason or citation", key="changes_search")
        selected = [c for c in sub_rows if (kind == "All" or c.get("change") == kind)
                    and query.casefold() in (str(c.get("new_text", "")) + " " + str(c.get("reason", "")) + " " + str(c.get("source", ""))).casefold()]
        st.caption(f"{len(selected)} shown · {len(sub_rows)} total in the selected submission")
        st.dataframe([{
            "Change": c.get("change", ""),
            "Existing ID": c.get("requirement_id") or "New obligation",
            "Source": source_label(c.get("source"), include_reg=False),
            "Resulting requirement": c.get("new_text") or "— removed —",
        } for c in selected], hide_index=True, use_container_width=True, height=275)
        if selected:
            i = st.selectbox("Inspect change", range(len(selected)), format_func=lambda n:
                             f"{selected[n].get('change', '').capitalize()}  ·  {selected[n].get('requirement_id') or 'new'}  ·  {source_label(selected[n].get('source'), False)}")
            record = selected[i]
            st.markdown("**Change decision**")
            st.write(str(record.get("change", "")).upper() + ("  ·  " + str(record["requirement_id"]) if record.get("requirement_id") else ""))
            st.markdown("**Proposed requirement**")
            st.write(record.get("new_text") or "Old obligation removed; confirm absence from the updated regulation.")
            st.markdown("**Why this changed**")
            st.write(record.get("reason") or "No reason provided")
            st.caption(source_label(record.get("source"), False))
        st.download_button("Export displayed change JSON", json.dumps(sub, ensure_ascii=False, indent=2), file_name=f"{sub_case or 'subgoal'}_submission.json", mime="application/json")

with pages[4]:
    st.markdown("### The engineering handoff")
    st.markdown('<div class="f-decision">Inspect the source. Review the obligation. Export structured JSON for engineering work.</div>', unsafe_allow_html=True)
    st.markdown("### Implementation & safeguards")
    st.write("Python handles document preparation, evidence indexing and schema validation. Model calls are used for product interpretation, applicability screening, requirement extraction and optional review. The exported result is structured evidence for engineering review.")
    stages = [
        ("Product understanding", "Specification + drawing → evidence-grounded product profile", "src/product_profile.py"),
        ("Regulatory index", "PDF pages, sections, paragraph boundaries, tables, source locations", "src/indexer.py · src/structure.py · src/tables.py"),
        ("Applicability", "Regulation screening, overlap analysis, section selection", "src/screen_regulations.py · src/overlap.py · src/select_sections.py"),
        ("Requirement extraction", "Contextual packets → individual, cited engineering duties", "src/extract.py · src/llm.py"),
        ("Review and export", "Preset-dependent review, conservative deduplication and organizer-format export", "src/review_medium.py · src/faithfulness.py (optional) · src/export.py"),
        ("Regulation version changes", "Old requirement + old/new text → changed-obligation proposals", "tools/subgoal_pipeline.py (team branch)"),
    ]
    for no, (title, explanation, paths) in enumerate(stages, 1):
        cols = st.columns([0.08, 0.92])
        cols[0].write(f"{no:02}")
        with cols[1]:
            st.markdown(f"**{title}**")
            st.write(explanation)
            st.caption(paths)
        st.divider()
    st.caption("Module references reflect the reviewed source snapshot. Farhan may update the pipeline preset or implementation without changing this viewer.")
    st.markdown("#### Quality controls and limits")
    st.markdown("""
- **Evidence discipline:** distinguish stated product facts from unknown properties. Don't manufacture compatibility or certification claims.
- **Applicable, not merely relevant text:** requirements are proposals subject to product-scope checks and review.
- **Conservative deduplication:** repeated text may be eliminated, but different functions or legal conditions must remain distinct.
- **Traceability:** output retains regulation/version/page/paragraph so a reviewer can locate the source.
- **Format checks:** JSON shape and required fields can be validated independently from model reasoning.
""")
    if main or sub:
        st.markdown("### Export for review")
        d1, d2 = st.columns(2)
        with d1:
            if main:
                st.download_button("Main requirements JSON", json.dumps(main, ensure_ascii=False, indent=2), file_name=f"{main_case or 'main'}_submission.json", mime="application/json", key="handoff_main")
        with d2:
            if sub:
                st.download_button("Regulation changes JSON", json.dumps(sub, ensure_ascii=False, indent=2), file_name=f"{sub_case or 'change'}_submission.json", mime="application/json", key="handoff_sub")
    else:
        st.info("Load the pipeline output to inspect and export the engineering handoff.")

    with st.expander("Quality checks on loaded files (not a judge score)"):
        for label, data, kind, col in [("Main", main, "main", "text"), ("Version change", sub, "subgoal", "new_text")]:
            if not data:
                continue
            warns = structural_warnings(data, kind)
            duplicates = duplicate_count(data.get("requirements" if kind == "main" else "changes", []), col)
            st.write(f"**{label}:** {len(warns)} structural warning(s), {duplicates} exactly repeated text(s).")
            if warns:
                st.code("\n".join(warns[:12]))
    st.caption("Only organizer-format validation and source review can establish whether the data is ready for submission. This interface does not calculate competition F2 or recall.")

st.markdown('<div class="f-rule"></div><div class="f-note">FARSIGHTED &nbsp; / &nbsp; Engineering review viewer &nbsp; / &nbsp; Source-linked proposals, not a certification or submission portal</div>', unsafe_allow_html=True)
