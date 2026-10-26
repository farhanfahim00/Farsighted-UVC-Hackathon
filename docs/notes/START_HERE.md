# Farsighted | Story-first Streamlit demo

This is a read-only viewer for **real** Farsighted pipeline outputs. It does not run the LLM, grade performance, inspect source PDF text live, or submit files to the organizer. Color scheme: navy headings, muted teal accents, white background.

## Install on the Windows laptop

Download `Farsighted-Streamlit-Story.zip`. In **PowerShell**:

```powershell
$Py = "C:\Users\haqs\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$Demo = "C:\UVC2026\Farsighted-Demo"
New-Item -ItemType Directory -Path $Demo -Force | Out-Null
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\Farsighted-Streamlit-Story.zip" -DestinationPath $Demo -Force
Set-Location $Demo
& $Py -m streamlit --version
```

If Streamlit is not installed:

```powershell
& $Py -m pip install --user streamlit
```

Then launch:

```powershell
& $Py -m streamlit run .\streamlit_app.py --server.port 8501
```

Open `http://localhost:8501`. The Streamlit server stays open while the terminal is running; stop with `Ctrl+C`.

The ZIP contains `streamlit_app.py`, `.streamlit/config.toml`, a genuine **development V3** sub-goal JSON, and the example specification/drawing. No fake main-goal submission is packaged.

## Load Farhan's current main output

The left sidebar normally finds files under:

`C:\UVC2026\farsighted\build\cases\<case-id>\submission*.json`

Select the newest appropriate file under **Generated main JSON**. If Farhan's results are on his laptop instead, have him send the generated submission JSON and use **Or upload main submission**. The app accepts the organizer's `team_id/case_id/requirements` format; it is independent of the review preset or extraction code details.

The optional change output should be `team_id/case_id/changes` and can be selected under `runs\...\submission_subgoal.json` or uploaded. The bundled V3 file is visibly labeled **development**. If you load held-out main data, the app will not silently pair it with bundled development changes.

The product drawing and specification in the ZIP are development examples. If you use held-out data, upload corresponding source inputs; otherwise the app will not silently show the development drawing as the held-out product.

## Live video story, not a benchmark slideshow

1. **01 Story:** Why engineering teams need requirements they can trace to the source.
2. **02 Product input:** Actual drawing and specification; state unknowns stay unknown.
3. **03 Requirements:** Load one genuine generated row. Show the obligation and its regulation, version, page and paragraph. This is the central product result.
4. **04 Changes:** Show how old versus new regulation analysis proposes a specific obligation update.
5. **05 Handoff:** Close with source-linked JSON, conservative review, and a clear human verification boundary.

### Numbers to use

The three counts on the first tab are *calculated live from loaded JSON*: requirement proposals, unique regulations referenced, and change proposals. They are not precision, F2, verified legal compliance or the number of regulations screened. You can mention the counts while showing the page, but **do not hardcode a score in the video script**. Performance can be discussed separately if the jury asks.

### Video data source

The organizer did not explicitly require held-out examples in the video. Use held-out input/output only if ready, permitted to be shown publicly and accurately presented. A genuine development run is a valid fallback demonstration; identify it clearly. Never present `expected.json` or a manually gold-edited candidate as an automatically generated result.

### Final deliverables (Sunday 13:00 CET deadline)

1. Certivity platform: validated **main held-out JSON** for `heldout-front-bumper`.
2. Certivity platform: validated **optional sub-goal held-out JSON** for `subgoal-heldout` if participating.
3. Hack and More form: **GitHub repository link** with executable source and a short run explanation.
4. Hack and More form: **strictly 2-minute demo video link** (YouTube or Luma); aim 1:50-1:55.
5. Hack and More form: **short technical explanation**.

The UI is not a separately mandated organizer upload, but it can accompany your repo as `demo/streamlit_app.py` and be shown in the video. Before 13:00, verify BOTH platforms accepted their submissions. On held-out, **the last valid upload counts**; keep safe copies.

No code or backend changes are needed if Farhan adjusts prompts, thresholds or number of extracted requirements.
