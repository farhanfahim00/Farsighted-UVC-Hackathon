# Farsighted optional challenge + demo (source-only overlay)

This update is deliberately independent of **both** main extraction pipelines. It adds a regulation-version change solver, developer-only coverage checks, a manual recovery validator, and a static HTML demo. It does not change `src/pipeline.py`, `src/selection.py`, `src/packets.py`, the main requirements schema, or Git history.

## Current verified state

- Uploaded organizer PDF ZIP checksums match `subgoal/dev/files.json`:
  - old.pdf: `c0bdbf009ca2818a30bdc12b40b882fd525cb25690b321a335de87fbb2294ae2`
  - new.pdf: `063165ac04d3d94f62c4fbbe267073396484daf57b2f2fbf68987886a6bb4e7b`
- Stage 1 (no model): 194 old clauses, 228 new clauses; 48 potentially changed provisions in **8 batches**. It identifies text changes, **not** 48 engineering requirement changes.
- Separate development-only audit finds **15/15 distinct source paragraphs** underlying the 24 reference decisions and **7/7 old requirement IDs** in candidate context. 17 reference additions arise from **9** source paragraphs; the development reference has 6 modifications and 1 removal.
- Python and mock-provider tests pass; **no real Claude API calls were made here**. No scored sub-goal output exists yet.
- The production solver never loads `subgoal/dev/expected.json`. Only `tools/audit_subgoal_sources.py` and `tools/score_subgoal_offline.py` read the development reference when explicitly called.

## 1. Install in PowerShell

```powershell
Set-Location C:\UVC2026\farsighted
$Py = "C:\Users\haqs\AppData\Local\Python\pythoncore-3.14-64\python.exe"
$Zip = "$env:USERPROFILE\Downloads\farsighted-subgoal-demo-update.zip"
Expand-Archive -LiteralPath $Zip -DestinationPath . -Force

& $Py -m pytest -q tests/test_subgoal_pipeline.py tests/test_demo_builder.py
```

The ZIP contains only Python source, tests, and this guide. Keep your locally present `subgoal/dev/old.pdf` and `subgoal/dev/new.pdf`. **Do not commit those licensed PDFs, your API key, generated run folders, or any reference answers.**

## 2. FREE: Stage 1 PDF alignment and word diffs

```powershell
& $Py -m tools.subgoal_pipeline --out .\runs\subgoal-dev-v1
& $Py -m tools.audit_subgoal_sources --evidence-packets .\runs\subgoal-dev-v1\evidence-packets.json
```

Inspect `runs/subgoal-dev-v1/preflight.json` and `evidence-packets.json`. Expected on the exact development PDFs: **48 changed-text candidates, 8 review batches, 15/15 reference-source locations, 7/7 affected old IDs**. The second command is **DEV EVALUATION ONLY**. It must not be called by production inference on the held-out case.

This step makes **zero API calls** and **does not create `submission_subgoal.json`**.

## 3. Model review: only after checking team credits

Confirm that your teammate is not simultaneously using the shared API reserve. The following command explicitly permits paid Claude API calls; it does not repeat the main challenge extraction or run the $45 reviewer.

```powershell
& $Py -m tools.subgoal_pipeline `
    --out .\runs\subgoal-dev-v1 `
    --online `
    --page-images `
    --max-attempts 18 `
    --max-usd 4
```

The 8 section-family packets include old requirements, full old/new source passages, exact word-deltas, and, where detected, compressed page images of changed tables. The model proposes only *added*, *modified*, or *removed* duties. Renumbering alone is not a modification. Citations must resolve to a real clause/page in the correct PDF.

**Important:** `--max-usd` is the local client's *estimate/reservation* cap, **not an enforceable provider billing guarantee**. No secrets or original PDFs belong in Git. The model caches identical requests under `runs/subgoal-dev-v1/request-cache`. Re-running unchanged inputs reuses cached responses. No API calls occur unless `--online` is explicitly set.

If a batch fails, the workflow writes `incomplete.json` and **no final submission**. If citations, old IDs, or the organizer schema do not validate, it writes `needs-review.json` with all accepted proposals and **no final submission**. `model-batch-status.json` identifies the affected groups. To recover proposals from an earlier failed run with cached responses, rerun with `--replay --page-images` and the same inputs and output folder; replay cannot make paid API calls. Review and correct any oversized `new_text` before finalizing. Never cut text at the character limit, since that can remove legal conditions.

## 4. Validate the result before any upload

When a `submission_subgoal.json` exists:

```powershell
& $Py tools\validate.py .\runs\subgoal-dev-v1\submission_subgoal.json `
    --requirements-old .\subgoal\dev\requirements_old.json

& $Py -m tools.score_subgoal_offline .\runs\subgoal-dev-v1\submission_subgoal.json `
    --expected .\subgoal\dev\expected.json
```

The local offline scorer checks only structural `(change, old ID, new paragraph)` agreement, **NOT** official semantic F2. Review each generated `new_text`, `reason`, condition, number and cited page against the source, particularly test tables and split/moved clauses. Upload only a validated, reviewed file.

If model proposals are valid in principle but need manual correction, edit the `proposals` array in `needs-review.json` (or save the corrected rows in a new JSON list) and finalize **without another API call**:

```powershell
& $Py -m tools.finalize_subgoal .\runs\subgoal-dev-v1\needs-review.json `
    --out .\runs\subgoal-dev-v1\submission_subgoal.json
```

The finalizer rechecks the old IDs, actual PDF paragraph/page references, and the organizer schema. It never bypasses validation.

## 5. Demo viewer — no dependency on which main pipeline wins

Preview Stage 1 immediately, without waiting for Claude:

```powershell
& $Py -m tools.build_demo `
    --evidence-packets .\runs\subgoal-dev-v1\evidence-packets.json `
    --out .\runs\demo\index.html
Start-Process .\runs\demo\index.html
```

After the team chooses its main JSON and the sub-goal is validated, generate the combined presentation from the same tool:

```powershell
& $Py -m tools.build_demo `
    --main "C:\PATH\TO\FINAL\submission.json" `
    --subgoal .\runs\subgoal-dev-v1\submission_subgoal.json `
    --evidence-packets .\runs\subgoal-dev-v1\evidence-packets.json `
    --out .\runs\demo\index.html
Start-Process .\runs\demo\index.html
```

Three tabs: **Requirements** (search/filter and PDF page link), **Requirement changes** (added/modified/removed, old/new word comparison), **Regulation paragraph diff** (Stage 1 candidate changes). A demo can use either teammate's or your main `submission.json`; the schema is the interface.

The HTML is self-contained and needs no app server. It opens organizer-provided PDF URLs, or links to local PDFs where present. PDF **page linking is supported**. Exact text highlighting is **not claimed** unless a separate verified geometry mapping is added.

## 6. Sunday / new case

Replace the inputs and case ID, using a **new** output folder. If you have the new case's file manifest, provide it too:

```powershell
& $Py -m tools.subgoal_pipeline `
    --old-pdf "C:\PATH\TO\CASE\old.pdf" `
    --new-pdf "C:\PATH\TO\CASE\new.pdf" `
    --requirements-old "C:\PATH\TO\CASE\requirements_old.json" `
    --files-index "C:\PATH\TO\CASE\files.json" `
    --case-id "ACTUAL-CASE-ID" `
    --out .\runs\subgoal-heldout `
    --online --page-images --max-attempts 18 --max-usd 4
```

Use the exact organizer `case_id`. The code has no R175 allowlist and no development-gold paragraph references. It requires selectable-text PDFs (for scans, text extraction needs separate preprocessing). If the unseen old/new files differ substantially in format, verify the Stage 1 evidence before allowing any paid model call.

## Practical stop criteria

Do not sacrifice the measured main pipeline to integrate this solver. Finish the optional submission first. If the preflight has missing or obviously garbled clauses on the held-out PDFs, **stop**, inspect the page text and treat any generated change proposal as untrusted. If Claude produces an incomplete result, do not upload it as complete. Preserve the original PDFs, cached responses, validated JSON, and an offline demo you can reliably open.
