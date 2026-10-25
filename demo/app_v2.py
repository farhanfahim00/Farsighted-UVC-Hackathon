"""Farsighted | guided read-only review flow (5 steps).

Run from the repository root:  streamlit run demo/app_v2.py
Reads organizer-format main/sub-goal JSON from ./build and ./runs (or FARSIGHTED_REPO_DIR).
It never runs extraction, calls an LLM, or submits a result.
"""
from __future__ import annotations

import html
import json
import shlex
import subprocess
import sys
import os
import time
from collections import Counter
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ui.helpers import (  # noqa: E402
    CASES, STEP_WHY, THEME_CSS, UPLOAD_CHOICE, WHY_CARDS, WHY_METRICS, case_family, case_inputs, case_main_path, default_repo_root, duplicate_count, expected_main_pattern,
    expected_sub_pattern, generated_main_files, generated_subgoal_files, kept_rows, local_pdf, pdf_url_for,
    read_submission, render_pdf_page, source_label, structural_warnings, trim,
    NAVY, TEAL, CHANGE_COLORS, chips_html, citation_strip, hbar_html, index_regulation_count, parse_main_parts,
    part_mention_count, pill_html, stacked_bar_html, word_diff_html, load_old_requirements, run_validate,
)

HERE = Path(__file__).resolve().parent
EXAMPLE_ROOT = HERE / "examples"
EXAMPLE_SPEC = EXAMPLE_ROOT / "rear-lamp-spec.md"
EXAMPLE_IMAGE = EXAMPLE_ROOT / "exploded_view.png"
EXAMPLE_SUB = EXAMPLE_ROOT / "subgoal-v3-submission.json"

STEPS = ["Why", "Input", "Product understood", "Regulations", "Requirements review", "Export and changes"]

st.set_page_config(page_title="Farsighted — Engineering requirements", page_icon="📄", layout="wide", initial_sidebar_state="expanded")
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
        f'<div class="f-step {"current" if i == cur else "done" if i < cur else ""}"><b>{"START" if i == 0 else f"STEP {i}"}</b>{name}</div>'
        for i, name in enumerate(STEPS)
    )
    st.markdown(f'<div class="f-stepper">{cells}</div>', unsafe_allow_html=True)
    cols = st.columns(len(STEPS))
    for i, name in enumerate(STEPS):
        cols[i].button("Go to " + ("start" if i == 0 else str(i)), key=f"jump{i}", on_click=go, args=(i,), disabled=(i == cur), use_container_width=True)


def mini_stepper() -> None:
    cur = S["step"]
    with st.container(key="ministep"):
        cols = st.columns(len(STEPS))
        for i, name in enumerate(STEPS):
            label = ("● " if i == cur else "") + ("Why" if i == 0 else f"{i} {name}")
            cols[i].button(label, key=f"mini{i}", type="tertiary", on_click=go, args=(i,), disabled=(i == cur))


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


def reg_groups() -> dict[str, list[int]]:
    """Regulation name -> indices into main_rows, most requirements first."""
    groups: dict[str, list[int]] = {}
    for i, r in enumerate(main_rows):
        groups.setdefault(str((r.get("source") or {}).get("regulation") or "Unspecified"), []).append(i)
    return dict(sorted(groups.items(), key=lambda kv: -len(kv[1])))


def set_keep(i: int) -> None:
    S["keep"][i] = bool(S[f"keep_w{i}"])


def set_group(idxs: list[int], wkey: str) -> None:
    for i in idxs:
        S["keep"][i] = bool(S[wkey])

with st.sidebar:
    st.markdown("**FARSIGHTED**")
    st.checkbox("Live run (costs API credits)", value=False, key="live_run")
    st.caption("Off: the viewer only replays recorded outputs and never calls a model.")

step = S["step"]
if step == 0:
    st.markdown('<div class="f-kicker">Farsighted / Industrial regulatory intelligence</div>', unsafe_allow_html=True)
    st.title("From a component to traceable requirements")
    stepper()
else:
    st.markdown('<div class="f-title-sm">FARSIGHTED · From a component to traceable requirements</div>', unsafe_allow_html=True)
    mini_stepper()
st.markdown('<div class="f-rule"></div>', unsafe_allow_html=True)

# ---- Step 0: Why ---------------------------------------------------------
if step == 0:
    st.markdown("### Why Farsighted")
    cols = st.columns(4, gap="small")
    for col, (title, problem, built, see) in zip(cols, WHY_CARDS):
        with col, st.container(border=True):
            st.markdown(f'<div class="f-card"><h4>{title}</h4><b>Problem</b>{problem}<b>What we built</b>{built}<b>What you see</b>{see}</div>', unsafe_allow_html=True)
    st.markdown(f'<div class="f-note">{WHY_METRICS}</div>', unsafe_allow_html=True)
    n_index = index_regulation_count(str(root / "regulations" / "index.json"))
    funnel = [("Regulations in index", n_index or 0)]
    if main:
        funnel += [("Regulations referenced", len(reg_names)), ("Requirements", len(main_rows)),
                   ("Kept (live)", len(kept_rows(main_rows, S["keep"])))]
    st.markdown("**From the regulation library to your kept requirements**")
    st.markdown(hbar_html(funnel, [TEAL, TEAL, TEAL, NAVY]), unsafe_allow_html=True)
    if not main:
        st.caption("Load a case in step 1 to extend the funnel with requirements and kept counts.")
    if n_index is None:
        st.caption("regulations/index.json not found; library count unavailable.")
    nav()

# ---- Step 1: Input -------------------------------------------------------
elif step == 1:
    st.markdown("### 1 · Input")
    st.caption(STEP_WHY[1])
    st.markdown('<div class="f-intro">Choose a recorded case to replay, or upload your own outputs and product evidence.</div>', unsafe_allow_html=True)
    root_text = st.text_input("Repository folder (contains build/, cases/ and runs/)", value=str(L.get("root", default_repo_root())), key="root_input")
    root = Path(root_text).expanduser()
    choice = st.radio("Case", list(CASES) + [UPLOAD_CHOICE], horizontal=True, key="case_choice")
    if L.get("source") not in (None, choice):  # switching case discards the previous result
        S["loaded"], S["keep"], S["source_view"] = {}, {}, None
    L = S["loaded"]
    L["root"] = str(root)

    def pack(source, main_d, main_l, sub_d, sub_l, is_ex, spec, spec_n, img, img_n):
        S["loaded"] = dict(root=str(root), source=source, main=main_d, main_label=main_l, sub=sub_d, sub_label=sub_l,
                           sub_is_example=is_ex, spec=spec, spec_name=spec_n, image=img, image_name=img_n)

    def previews(spec, spec_n, img, img_n):
        a, b = st.columns([1.15, 1], gap="large")
        with a:
            st.markdown("**Specification**")
            if spec:
                st.caption(spec_n)
                with st.container(height=300):
                    st.markdown(spec.decode("utf-8-sig", errors="replace"))
            else:
                st.info("No specification available.")
        with b:
            st.markdown("**Drawing / exploded view**")
            if img:
                st.caption(img_n)
                st.image(img, use_container_width=True)
            else:
                st.info("No drawing available.")

    if choice != UPLOAD_CHOICE:
        case_id = CASES[choice]
        dev = case_family(case_id) == "Development"
        spec, spec_n, img, img_n = case_inputs(root, case_id, EXAMPLE_SPEC if dev else None, EXAMPLE_IMAGE if dev else None)
        previews(spec, spec_n, img, img_n)
        main_path = case_main_path(root, case_id)
        if not main_path.is_file():
            st.markdown(f'<div class="f-empty"><b>No recorded run found for {case_id}.</b><br>Expected a file at:<br><code>{main_path}</code></div>', unsafe_allow_html=True)
        if st.button("Run Farsighted", type="primary", disabled=not main_path.is_file(), key="run_replay"):
            data, label, err = read_submission(None, main_path, "main")
            if err:
                st.error(err)
            else:
                with st.status("Replaying a recorded run, no model calls", expanded=True) as status:
                    st.caption("Nothing is executed or sent to a model. The saved output of an earlier run is revealed stage by stage.")
                    rows = data["requirements"]
                    warns = structural_warnings(data, "main")
                    regs = {(r.get("source") or {}).get("regulation") for r in rows} - {None}
                    for text in ("Reading product evidence", "Screening regulations", "Extracting requirements", "Validating citations"):
                        st.write(f"⏳ {text}…")
                        time.sleep(0.9)
                        detail = {"Reading product evidence": f"{case_id}: specification and drawing read",
                                  "Screening regulations": f"{len(regs)} regulations referenced",
                                  "Extracting requirements": f"{len(rows)} requirements",
                                  "Validating citations": f"{len(warns)} structural warning(s)"}[text]
                        st.write(f"✅ {text} — {detail}")
                    status.update(label="Replay complete (recorded run, no model calls)", state="complete", expanded=False)
                sub_d, sub_l, is_ex = None, "", False
                if dev and EXAMPLE_SUB.exists():
                    sub_d, sub_l, _ = read_submission(None, EXAMPLE_SUB, "subgoal")
                    is_ex = bool(sub_d)
                pack(choice, data, label, sub_d, sub_l, is_ex, spec, spec_n, img, img_n)
                S["keep"], S["source_view"] = {}, None
        L = S["loaded"]
        if L.get("main") and L.get("source") == choice:
            st.success(f"Loaded main: {L['main_label']} ({len(L['main']['requirements'])} requirements)")
        else:
            st.caption("Not loaded yet, press Run")
    else:
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

        if up_main is None and chosen_main is None and main_local:
            chosen_main = main_local[0]
        new_main, main_label, err = read_submission(up_main, chosen_main, "main")
        if err:
            st.error(err)
        new_sub, sub_label, err = read_submission(up_sub, chosen_sub, "subgoal")
        if err:
            st.error(err)
        spec, spec_n = (up_spec.getvalue(), up_spec.name) if up_spec is not None else (b"", "")
        img, img_n = (up_image.getvalue(), up_image.name) if up_image is not None else (None, "")
        previews(spec, spec_n, img, img_n)
        if main_label != L.get("main_label"):
            S["keep"], S["source_view"] = {}, None
        pack(choice, new_main, main_label, new_sub, sub_label, False, spec, spec_n, img, img_n)
        if new_main:
            st.success(f"Loaded main: {main_label} ({len(new_main['requirements'])} requirements)")
        if new_sub:
            st.success(f"Loaded changes: {sub_label}")
        if new_main and new_sub and case_family(new_main.get("case_id", "")) != case_family(new_sub.get("case_id", "")):
            st.warning("The main and change submissions come from different case families. They are separate demonstrations, not one combined evaluation.")

    if S.get("live_run"):
        st.markdown('<div class="f-rule"></div>', unsafe_allow_html=True)
        st.markdown("#### Live run (costs API credits)")
        st.warning("This executes the command below on this machine from the repository folder. It may call paid APIs.")
        cmd = st.text_input("Shell command", key="live_cmd", placeholder="Enter the command to run")
        spend = st.number_input("Max spend (USD)", min_value=0.0, value=1.0, step=0.5, key="live_spend")
        st.caption("The limit is passed to the command as FARSIGHTED_MAX_SPEND_USD. The viewer cannot measure or enforce spend itself; the command must honor it.")
        if st.button("Start live run", key="live_start", disabled=not cmd.strip()):
            try:
                argv = shlex.split(cmd)
            except ValueError as exc:
                argv = []
                st.error(f"Cannot parse command: {exc}")
            if argv:
                env = {**os.environ, "FARSIGHTED_MAX_SPEND_USD": str(spend)}
                box, lines = st.empty(), []
                try:
                    proc = subprocess.Popen(argv, cwd=root if root.is_dir() else None, env=env, stdout=subprocess.PIPE,
                                            stderr=subprocess.STDOUT, text=True, bufsize=1)
                except OSError as exc:
                    proc = None
                    st.error(f"Could not start command: {exc}")
                if proc:
                    try:
                        for line in proc.stdout:
                            lines.append(line.rstrip())
                            box.code("\n".join(lines[-200:]))
                        code = proc.wait()
                    finally:
                        if proc.poll() is None:
                            proc.terminate()
                    (st.success if code == 0 else st.error)(f"Command exited with code {code}. Use 'Upload my own' to pick up newly generated files.")
    nav()

# ---- Step 2: Product understood ------------------------------------------
elif step == 2:
    st.markdown("### 2 · Product understood")
    st.caption(STEP_WHY[2])
    spec_text = L.get("spec", b"").decode("utf-8-sig", errors="replace") if L.get("spec") else ""
    parts = parse_main_parts(spec_text)
    a, b = st.columns([1.1, 1], gap="large")
    with a:
        st.markdown("**Drawing / exploded view**")
        if L.get("image"):
            st.caption(L["image_name"])
            st.image(L["image"], use_container_width=True)
        else:
            st.info("No drawing supplied. Add one in step 1.")
    with b:
        st.markdown("**Main parts read from the specification**")
        if parts:
            st.markdown(chips_html([(p_, part_mention_count(main_rows, p_)) for p_ in parts]), unsafe_allow_html=True)
            st.caption("0 = no requirement names this part; general requirements may still apply")
        elif spec_text:
            st.info("No 'Main Parts' list found in the specification.")
        else:
            st.info("No specification supplied. Add one in step 1.")
        if spec_text:
            with st.expander("Specification text"):
                with st.container(height=260):
                    st.markdown(spec_text)
    nav()

# ---- Step 3: Regulations --------------------------------------------------
elif step == 3:
    st.markdown("### 3 · Regulations")
    st.caption(STEP_WHY[3])
    if not main:
        empty_state("main", root)
    else:
        groups = reg_groups()
        versions: dict[str, set[str]] = {}
        for r in main_rows:
            s_ = r.get("source") or {}
            versions.setdefault(str(s_.get("regulation") or "Unspecified"), set()).add(str(s_.get("version") or "—"))
        m1, m2, m3 = st.columns(3)
        m1.metric("Regulations referenced", len(groups))
        m2.metric("Requirement proposals", len(main_rows))
        counter3 = m3.empty()
        st.markdown("**Requirements per regulation** · toggle to keep or discard a whole regulation")
        top = max(len(v) for v in groups.values())
        with st.container(height=330, key="regpane"):
            for name, idxs in groups.items():
                gkey = f"keep_g_{name}"
                S[gkey] = all(S["keep"].get(i, True) for i in idxs)
                c1, c2 = st.columns([1, 7])
                c1.toggle("Keep", key=gkey, on_change=set_group, args=(idxs, gkey), label_visibility="collapsed")
                c2.markdown(hbar_html([(f"{name} · v{', '.join(sorted(versions[name]))}", len(idxs))], top=top), unsafe_allow_html=True)
        counter3.metric("Kept", f"{len(kept_rows(main_rows, S['keep']))} of {len(main_rows)}")
        st.caption("Counts describe the loaded output only. They are not judge precision, recall or proof of compliance.")
    nav()

# ---- Step 4: Requirements review -----------------------------------------
elif step == 4:
    st.markdown("### 4 · Requirements review")
    st.caption(STEP_WHY[4])
    if not main:
        empty_state("main", root)
    else:
        keep: dict[int, bool] = S["keep"]

        def select(i: int) -> None:
            S["source_view"] = i

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
        groups = dict(sorted(groups.items(), key=lambda kv: -len(kv[1])))
        visible = [i for idxs in groups.values() for i in idxs]
        if S["source_view"] not in visible:
            S["source_view"] = visible[0] if visible else None

        left, right = st.columns([1.15, 1], gap="large")
        with left:
            with st.container(height=700, key="reqpane"):
                for name, idxs in groups.items():
                    h1, h2 = st.columns([3, 2])
                    h1.markdown(f"**{name}**")
                    gkey = f"keep_g_{name}"
                    S[gkey] = all(keep.get(i, True) for i in idxs)
                    h2.toggle(f"Keep all ({sum(keep.get(i, True) for i in idxs)}/{len(idxs)})", key=gkey, on_change=set_group, args=(idxs, gkey))
                    for i in idxs:
                        r = main_rows[i]
                        with st.container(border=True):
                            st.markdown(f'<div class="f-req"><b>{html.escape(str(r.get("id", "") or f"#{i + 1}"))}</b> {html.escape(str(r.get("text", "")))}'
                                        f'<small>{html.escape(source_label(r.get("source")))}</small></div>', unsafe_allow_html=True)
                            c2, c3 = st.columns(2)
                            wkey = f"keep_w{i}"
                            S[wkey] = keep.get(i, True)
                            c2.toggle("Keep", key=wkey, on_change=set_keep, args=(i,))
                            c3.button("Showing source" if S["source_view"] == i else "View source", key=f"src{i}",
                                      on_click=select, args=(i,), disabled=(S["source_view"] == i))
                if not groups:
                    st.info("No requirements match the current filters.")
        with right:
            sel = S["source_view"]
            if sel is not None:
                r = main_rows[sel]
                src = r.get("source") or {}
                name = str(src.get("regulation") or "Unspecified")
                try:
                    page = int(src.get("page"))
                except (TypeError, ValueError):
                    page = 0
                st.markdown(citation_strip(name, src.get("version"), src.get("page"), src.get("paragraph")), unsafe_allow_html=True)
                pdf = local_pdf(root, name)
                url = pdf_url_for(root, name, str(src.get("version", "")))
                zoom = st.radio("Zoom", ["1x", "2x"], horizontal=True, key="page_zoom", label_visibility="collapsed")
                img = render_pdf_page(str(pdf), page, 4.0 if zoom == "2x" else 2.0) if pdf and page else None
                with st.container(height=650, border=True):
                    if img and zoom == "2x":
                        st.image(img, caption=f"{pdf.name} · page {page}", width=1300)
                    elif img:
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
    nav()

# ---- Step 5: Export and changes ------------------------------------------
else:
    st.markdown("### 5 · Export and changes")
    st.caption(STEP_WHY[5])
    if sub:
        kinds = Counter(str(c.get("change")) for c in sub_rows)
        st.markdown("".join(pill_html(k, f"{k} {kinds.get(k, 0)}") for k in CHANGE_COLORS)
                    + stacked_bar_html({k: kinds.get(k, 0) for k in CHANGE_COLORS}), unsafe_allow_html=True)
        st.caption(f"Case: `{sub_case}` · {'Bundled V3 development example' if L.get('sub_is_example') else 'Loaded result'} · proposals requiring review")
        q1, q2 = st.columns([1, 2])
        kind = q1.selectbox("Change type", ["All", "added", "modified", "removed"])
        query = q2.text_input("Search", placeholder="Search requirement, reason or citation", key="changes_search")
        selected = [c for c in sub_rows if (kind == "All" or c.get("change") == kind)
                    and query.casefold() in (str(c.get("new_text", "")) + " " + str(c.get("reason", "")) + " " + str(c.get("source", ""))).casefold()]
        st.caption(f"{len(selected)} shown · {len(sub_rows)} total")
        if selected:
            old_by_id, old_problem = load_old_requirements(root, sub_case or main_case)
            if old_problem:
                st.warning(f"Previous wording unavailable. {old_problem}")
            i = st.selectbox("Inspect change", range(len(selected)), format_func=lambda n:
                             f"{selected[n].get('change', '').capitalize()}  ·  {selected[n].get('requirement_id') or 'new'}  ·  {trim(selected[n].get('new_text') or selected[n].get('reason'), 70)}")
            rec = selected[i]
            rid = str(rec.get("requirement_id") or "")
            old_text = old_by_id.get(rid, "") if rid else ""
            st.markdown(pill_html(str(rec.get("change")), str(rec.get("change", "")).upper())
                        + (f"<b>{rec['requirement_id']}</b> · " if rec.get("requirement_id") else "") + source_label(rec.get("source"), False),
                        unsafe_allow_html=True)
            if old_text or rec.get("change") != "added":
                if not old_text and not old_problem:
                    st.caption(f"No previous wording for requirement_id '{rid or '—'}' in requirements_old.json; showing the new text only.")
                elif not old_text:
                    st.caption("Showing the new text only.")
                st.markdown(word_diff_html(old_text, rec.get("new_text") or ""), unsafe_allow_html=True)
            else:
                st.markdown(word_diff_html("", rec.get("new_text") or ""), unsafe_allow_html=True)
            st.markdown("**Why this changed:** " + (rec.get("reason") or "No reason provided"))
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
    if export_main:
        verdict, vlines = run_validate(root, export_main)
        (st.success if verdict == "PASS" else st.error if verdict == "FAIL" else st.warning)(f"tools/validate.py on the kept JSON: {verdict}")
        if vlines and verdict != "PASS":
            st.code("\n".join(vlines))
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
