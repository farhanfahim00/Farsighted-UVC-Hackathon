"""Farsighted | guided read-only review flow (5 steps).

Run from the repository root:  streamlit run demo/app_v2.py
Reads organizer-format main/sub-goal JSON from ./build and ./runs (or FARSIGHTED_REPO_DIR).
It never runs extraction, calls an LLM, or submits a result.
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui.helpers import (  # noqa: E402
    THEME_CSS, case_family, default_repo_root, duplicate_count, expected_main_pattern,
    expected_sub_pattern, generated_main_files, generated_subgoal_files, kept_rows, local_pdf, pdf_url_for,
    read_submission, render_pdf_page, source_label, structural_warnings, trim,
)

HERE = Path(__file__).resolve().parent
EXAMPLE_ROOT = HERE / "examples"
EXAMPLE_SPEC = EXAMPLE_ROOT / "rear-lamp-spec.md"
EXAMPLE_IMAGE = EXAMPLE_ROOT / "exploded_view.png"
EXAMPLE_SUB = EXAMPLE_ROOT / "subgoal-v3-submission.json"

STEPS = ["Input", "Product understood", "Regulations", "Requirements review", "Export and changes"]

st.set_page_config(page_title="Farsighted — Engineering requirements", page_icon="📄", layout="wide", initial_sidebar_state="collapsed")
st.markdown(THEME_CSS, unsafe_allow_html=True)

S = st.session_state
S.setdefault("step", 0)
S.setdefault("loaded", {})
S.setdefault("keep", {})  # requirement index -> kept (default True)
S.setdefault("source_view", None)


def go(step: int) -> None:
    S["step"] = max(0, min(len(STEPS) - 1, step))


def stepper() -> None:
    cur = S["step"]
    cells = "".join(
        f'<div class="f-step {"current" if i == cur else "done" if i < cur else ""}"><b>STEP {i + 1}</b>{name}</div>'
        for i, name in enumerate(STEPS)
    )
    st.markdown(f'<div class="f-stepper">{cells}</div>', unsafe_allow_html=True)
    cols = st.columns(len(STEPS))
    for i, name in enumerate(STEPS):
        cols[i].button("Go to " + str(i + 1), key=f"jump{i}", on_click=go, args=(i,), disabled=(i == cur), use_container_width=True)


def nav() -> None:
    st.markdown('<div class="f-rule"></div>', unsafe_allow_html=True)
    a, _, c = st.columns([1, 4, 1])
    cur = S["step"]
    if cur > 0:
        a.button("← Back", on_click=go, args=(cur - 1,), key="back")
    if cur < len(STEPS) - 1:
        c.button("Next →", on_click=go, args=(cur + 1,), type="primary", key="next")


def empty_state(kind: str, root: Path) -> None:
    if kind == "main":
        path, what = expected_main_pattern(root), "main requirements JSON"
    else:
        path, what = expected_sub_pattern(root), "regulation-change JSON"
    st.markdown(
        f'<div class="f-empty"><b>No {what} found.</b><br>Expected a file at:<br><code>{path}</code>'
        '<br><span class="f-note">Generate it with the pipeline, upload a file in step 1, or set '
        '<code>FARSIGHTED_REPO_DIR</code> to the folder that contains <code>build/</code> and <code>runs/</code>.</span></div>',
        unsafe_allow_html=True,
    )


L = S["loaded"]
main, sub = L.get("main"), L.get("sub")
main_rows = (main or {}).get("requirements", [])
sub_rows = (sub or {}).get("changes", [])
main_case = (main or {}).get("case_id", "")
sub_case = (sub or {}).get("case_id", "")
reg_names = sorted({str((r.get("source") or {}).get("regulation")) for r in main_rows if (r.get("source") or {}).get("regulation")})
heldout_mode = "Held-out" in (case_family(main_case), case_family(sub_case))
root = Path(L.get("root", default_repo_root()))

st.markdown('<div class="f-kicker">Farsighted / Industrial regulatory intelligence</div>', unsafe_allow_html=True)
st.title("From a component to traceable requirements")
stepper()
st.markdown('<div class="f-rule"></div>', unsafe_allow_html=True)
step = S["step"]

# ---- Step 1: Input -------------------------------------------------------
if step == 0:
    st.markdown("### 1 · Input")
    st.markdown('<div class="f-intro">Pick generated pipeline outputs or upload them, and optionally add the product specification and drawing.</div>', unsafe_allow_html=True)
    root_text = st.text_input("Repository folder (contains build/ and runs/)", value=str(L.get("root", default_repo_root())), key="root_input")
    root = Path(root_text).expanduser()
    main_local, sub_local = generated_main_files(root), generated_subgoal_files(root)

    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("**Main requirements**")
        chosen_main = st.selectbox(
            "Generated main JSON", [None] + main_local, key="main_pick",
            format_func=lambda p: "Select a file" if p is None else f"{p.parent.name} / {p.name}",
        ) if main_local else None
        up_main = st.file_uploader("Or upload main submission", type=["json"], key="main_upload")
        if not main_local and up_main is None:
            empty_state("main", root)
    with c2:
        st.markdown("**Regulation changes (optional)**")
        chosen_sub = st.selectbox(
            "Generated change JSON", [None] + sub_local, key="sub_pick",
            format_func=lambda p: "Select a file" if p is None else f"{p.parent.name} / {p.name}",
        ) if sub_local else None
        up_sub = st.file_uploader("Or upload change submission", type=["json"], key="sub_upload")
    up_spec = st.file_uploader("Specification (optional)", type=["md", "txt"], key="spec_upload")
    up_image = st.file_uploader("Drawing (optional)", type=["png", "jpg", "jpeg", "webp"], key="drawing_upload")

    # Auto-select the newest generated main file when nothing is picked.
    if up_main is None and chosen_main is None and main_local:
        chosen_main = main_local[0]
    new_main, main_label, err = read_submission(up_main, chosen_main, "main")
    if err:
        st.error(err)
    new_sub, sub_label, err = read_submission(up_sub, chosen_sub, "subgoal")
    if err:
        st.error(err)
    sub_is_example = False
    fam = case_family((new_main or {}).get("case_id", ""))
    if new_sub is None and up_sub is None and chosen_sub is None and EXAMPLE_SUB.exists() and (new_main is None or fam == "Development"):
        new_sub, sub_label, _ = read_submission(None, EXAMPLE_SUB, "subgoal")
        sub_is_example = bool(new_sub)
    held = "Held-out" in (fam, case_family((new_sub or {}).get("case_id", "")))

    spec_bytes, spec_name = b"", ""
    if up_spec is not None:
        spec_bytes, spec_name = up_spec.getvalue(), up_spec.name
    elif not held and EXAMPLE_SPEC.exists():
        spec_bytes, spec_name = EXAMPLE_SPEC.read_bytes(), "Development-case example: rear-lamp-spec.md"
    img_bytes, img_name = None, ""
    if up_image is not None:
        img_bytes, img_name = up_image.getvalue(), up_image.name
    elif not held and EXAMPLE_IMAGE.exists():
        img_bytes, img_name = EXAMPLE_IMAGE.read_bytes(), "Development-case example: exploded_view.png"

    if main_label != L.get("main_label"):
        S["keep"], S["source_view"] = {}, None
    S["loaded"] = dict(root=str(root), main=new_main, main_label=main_label, sub=new_sub, sub_label=sub_label,
                       sub_is_example=sub_is_example, spec=spec_bytes, spec_name=spec_name, image=img_bytes, image_name=img_name)
    if new_main:
        st.success(f"Loaded main: {main_label} ({len(new_main['requirements'])} requirements)")
    if new_sub:
        st.success(f"Loaded changes: {sub_label}" + (" — bundled V3 example" if sub_is_example else ""))
    if held and not (new_main and new_sub):
        st.info("Held-out demonstration: development examples are not silently substituted for a missing held-out file.")
    if new_main and new_sub and case_family(new_main.get("case_id", "")) != case_family(new_sub.get("case_id", "")):
        st.warning("The main and change submissions come from different case families. They are separate demonstrations, not one combined evaluation.")
    nav()

# ---- Step 2: Product understood ------------------------------------------
elif step == 1:
    st.markdown("### 2 · Product understood")
    st.caption("The evidence the pipeline reads. It must not treat omitted information as a confirmed absence.")
    a, b = st.columns([1.15, 1], gap="large")
    with a:
        st.markdown("**Specification**")
        if L.get("spec"):
            st.caption(L["spec_name"])
            with st.container(height=435):
                st.markdown(L["spec"].decode("utf-8-sig", errors="replace"))
        else:
            st.info("No specification supplied. Add one in step 1.")
    with b:
        st.markdown("**Drawing / exploded view**")
        if L.get("image"):
            st.caption(L["image_name"])
            st.image(L["image"], use_container_width=True)
        else:
            st.info("No drawing supplied. Add one in step 1.")
    nav()

# ---- Step 3: Regulations --------------------------------------------------
elif step == 2:
    st.markdown("### 3 · Regulations")
    if not main:
        empty_state("main", root)
    else:
        by_reg = Counter(str((r.get("source") or {}).get("regulation") or "Unspecified") for r in main_rows)
        versions = {}
        for r in main_rows:
            s = r.get("source") or {}
            versions.setdefault(str(s.get("regulation") or "Unspecified"), set()).add(str(s.get("version") or "—"))
        m1, m2 = st.columns(2)
        m1.metric("Regulations referenced", len(by_reg))
        m2.metric("Requirement proposals", len(main_rows))
        st.dataframe([{"Regulation": k, "Version(s)": ", ".join(sorted(versions[k])), "Requirements": v}
                      for k, v in by_reg.most_common()], hide_index=True, use_container_width=True)
        st.caption("Counts describe the loaded output only. They are not judge precision, recall or proof of compliance.")
    nav()

# ---- Step 4: Requirements review -----------------------------------------
elif step == 3:
    st.markdown("### 4 · Requirements review")
    st.caption("Keep or discard each proposal and inspect its cited page. These are proposed duties, not verified compliance findings.")
    if not main:
        empty_state("main", root)
    else:
        keep: dict[int, bool] = S["keep"]

        def set_keep(i: int) -> None:
            keep[i] = bool(S[f"keep_w{i}"])

        def set_group(idxs: list[int], wkey: str) -> None:
            for i in idxs:
                keep[i] = bool(S[wkey])

        def toggle_source(i: int) -> None:
            S["source_view"] = None if S["source_view"] == i else i

        total = len(main_rows)
        counter = st.empty()
        s1, s2 = st.columns([1, 2])
        reg = s1.selectbox("Regulation", ["All"] + reg_names)
        search = s2.text_input("Search", placeholder="Search text or paragraph", key="req_search")
        groups: dict[str, list[int]] = {}
        for i, r in enumerate(main_rows):
            src = r.get("source") or {}
            name = str(src.get("regulation") or "Unspecified")
            if reg != "All" and name != reg:
                continue
            if search.casefold() not in (str(r.get("text", "")) + " " + str(src)).casefold():
                continue
            groups.setdefault(name, []).append(i)

        for name, idxs in groups.items():
            h1, h2 = st.columns([4, 1])
            h1.markdown(f"#### {name}")
            gkey = f"keep_g_{name}"
            S[gkey] = all(keep.get(i, True) for i in idxs)
            h2.toggle(f"Keep all ({sum(keep.get(i, True) for i in idxs)}/{len(idxs)})", key=gkey, on_change=set_group, args=(idxs, gkey))
            for i in idxs:
                r = main_rows[i]
                src = r.get("source") or {}
                with st.container(border=True):
                    c1, c2, c3 = st.columns([6, 1.3, 1.3])
                    c1.markdown(f"**{r.get('id', '') or f'#{i + 1}'}**  \n{r.get('text', '')}")
                    c1.caption(source_label(src))
                    wkey = f"keep_w{i}"
                    S[wkey] = keep.get(i, True)
                    c2.toggle("Keep", key=wkey, on_change=set_keep, args=(i,))
                    c3.button("Hide source" if S["source_view"] == i else "View source", key=f"src{i}", on_click=toggle_source, args=(i,))
                    if r.get("test_case"):
                        with st.expander("Proposed test case"):
                            st.write(r["test_case"])
                    if S["source_view"] == i:
                        p1, p2 = st.columns([2, 1], gap="large")
                        try:
                            page = int(src.get("page"))
                        except (TypeError, ValueError):
                            page = 0
                        pdf = local_pdf(root, name)
                        url = pdf_url_for(root, name, str(src.get("version", "")))
                        with p2:
                            st.markdown("**Citation**")
                            st.write(f"Regulation: {name}")
                            st.write(f"Version: {src.get('version') or '—'}")
                            st.write(f"Page: {src.get('page') or '—'}")
                            st.write(f"Paragraph: {src.get('paragraph') or '—'}")
                        with p1:
                            img = render_pdf_page(str(pdf), page) if pdf and page else None
                            if img:
                                st.image(img, caption=f"{pdf.name} · page {page}", use_container_width=True)
                            elif url and page:
                                st.markdown(f"Local PDF not available. [Open the regulation PDF at page {page}]({url}#page={page})")
                            elif url:
                                st.markdown(f"No valid page cited. [Open the regulation PDF]({url})")
                            else:
                                st.info(f"No local PDF under regulations/pdf/{name.replace(' ', '_')}/base/ and no pdf_url in regulations/index.json for {name} version {src.get('version') or '—'}.")

        kept = sum(1 for i in range(total) if keep.get(i, True))
        S["kept_count"], S["total_count"] = kept, total
        counter.markdown(f'<div class="f-decision">Kept {kept} of {total} requirements</div>', unsafe_allow_html=True)
        if not groups:
            st.info("No requirements match the current filters.")
    nav()

# ---- Step 5: Export and changes ------------------------------------------
else:
    st.markdown("### 5 · Export and changes")
    if sub:
        kinds = Counter(str(c.get("change")) for c in sub_rows)
        c1, c2, c3 = st.columns(3)
        c1.metric("Added", kinds.get("added", 0))
        c2.metric("Modified", kinds.get("modified", 0))
        c3.metric("Removed", kinds.get("removed", 0))
        st.caption(f"Case: `{sub_case}` · {'Bundled V3 development example' if L.get('sub_is_example') else 'Loaded result'} · proposals requiring review")
        q1, q2 = st.columns([1, 2])
        kind = q1.selectbox("Change type", ["All", "added", "modified", "removed"])
        query = q2.text_input("Search", placeholder="Search requirement, reason or citation", key="changes_search")
        selected = [c for c in sub_rows if (kind == "All" or c.get("change") == kind)
                    and query.casefold() in (str(c.get("new_text", "")) + " " + str(c.get("reason", "")) + " " + str(c.get("source", ""))).casefold()]
        st.caption(f"{len(selected)} shown · {len(sub_rows)} total")
        st.dataframe([{
            "Change": c.get("change", ""),
            "Existing ID": c.get("requirement_id") or "New obligation",
            "Source": source_label(c.get("source"), include_reg=False),
            "Resulting requirement": c.get("new_text") or "— removed —",
        } for c in selected], hide_index=True, use_container_width=True, height=275)
        if selected:
            i = st.selectbox("Inspect change", range(len(selected)), format_func=lambda n:
                             f"{selected[n].get('change', '').capitalize()}  ·  {selected[n].get('requirement_id') or 'new'}  ·  {source_label(selected[n].get('source'), False)}")
            rec = selected[i]
            st.markdown("**Proposed requirement**")
            st.write(rec.get("new_text") or "Old obligation removed; confirm absence from the updated regulation.")
            st.markdown("**Why this changed**")
            st.write(rec.get("reason") or "No reason provided")
            st.caption(source_label(rec.get("source"), False))
    else:
        empty_state("sub", root)

    st.markdown("### Export")
    export_main = {**main, "requirements": kept_rows(main_rows, S["keep"])} if main else None
    if main:
        st.caption(f"Main export contains the {len(export_main['requirements'])} kept of {len(main_rows)} requirements.")
    st.markdown('<div class="f-decision">Inspect the source. Review the obligation. Export structured JSON for engineering work.</div>', unsafe_allow_html=True)
    d1, d2 = st.columns(2)
    if main:
        d1.download_button("Kept requirements JSON", json.dumps(export_main, ensure_ascii=False, indent=2),
                           file_name=f"{main_case or 'main'}_submission.json", mime="application/json", key="dl_main")
    if sub:
        d2.download_button("Regulation changes JSON", json.dumps(sub, ensure_ascii=False, indent=2),
                           file_name=f"{sub_case or 'change'}_submission.json", mime="application/json", key="dl_sub")
    with st.expander("Quality checks on loaded files (not a judge score)"):
        for label, data, which, col in [("Main (kept)", export_main, "main", "text"), ("Version change", sub, "subgoal", "new_text")]:
            if not data:
                continue
            warns = structural_warnings(data, which)
            dups = duplicate_count(data.get("requirements" if which == "main" else "changes", []), col)
            st.write(f"**{label}:** {len(warns)} structural warning(s), {dups} exactly repeated text(s).")
            if warns:
                st.code("\n".join(warns[:12]))
    st.caption("Only organizer-format validation and source review can establish readiness for submission. This interface does not calculate F2 or recall.")
    nav()

st.markdown('<div class="f-rule"></div><div class="f-note">FARSIGHTED &nbsp; / &nbsp; Engineering review viewer &nbsp; / &nbsp; Source-linked proposals, not a certification or submission portal</div>', unsafe_allow_html=True)
