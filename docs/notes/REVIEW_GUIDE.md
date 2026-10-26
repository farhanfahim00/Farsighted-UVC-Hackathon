# Farsighted: recall-safe selection + evidence-reviewed export (24 Oct 2026)

This is a **targeted overlay** for the existing `C:\UVC2026\farsighted` repository. It does **not** include or replace the regulation PDFs, shared evidence index, cached Claude responses, run history, runtime config, Git history, product inputs or any `expected.json`.

## Why these changes

The first substantial development run generated 1,118 predictions (190 reference obligations in the *offline* development evaluator), was structurally valid, and reported 43 incomplete packets. The high output volume is a precision warning, **not** a measured semantic F2. We also observed evidence-bearing targets that did not produce a requirement. A deterministic document index cannot guarantee semantic entailment.

Your teammate's implementation suggests (1) explicit treatment of consolidated regulatory families, (2) section-focused extraction, and (3) a confidence/category review stage. These ideas are integrated **selectively**, without replacing our evidence index or encoding development-gold rules into inference.

## Improvements delivered

1. **Primary/review selection tiers:** In `src/selection.py`, model-selected sections and their descendant clauses are prioritized. The global modal/table safety net is retained in a separate review tier, not deleted. A regulation explicitly included with no sections still keeps its safety targets in primary. `selection_tier_counts` reports the size of each tier. This should protect extraction capacity, but is not proof of higher F2.
2. **No duplicate tier extraction:** `EvidenceIndex.chunks(..., expand_descendants=False)` prevents a review-tier parent from silently reintroducing primary-tier descendants, which would cause duplicate model work. Explicit `--targets` diagnostic behavior is unchanged.
3. **Independent second-pass review:** `tools/refine_submission.py` operates on the *already-generated* `submission.json` and full `ledger.json` with cited lines, saved product profile and regulation scope. It uses Claude structured JSON and checks that every input ID has exactly one answer. It performs a second independent check of proposed high-confidence deletions, keeps all uncertain requirements, and never mistakes missing review data for a valid deletion. Model reviewers are not regulatory experts or ground truth.
4. **Separate export hypotheses:** Four variants are produced **only after both review passes complete**, each independently validated by the organizer script:
   - `submission-reviewed-conservative.json`: confirmed wrong-scope, administrative-only or explicit contradiction removed; no regime assumptions.
   - `submission-reviewed-evidence_filtered.json`: additionally permits two-pass high-confidence "unsupported by cited source" removal (higher risk to recall).
   - `submission-reviewed-consolidated_safe.json`: conservative review plus optional consolidated approval regime.
   - `submission-reviewed-consolidated_evidence.json`: evidence-filtered review plus optional consolidated approval regime.

   The consolidation relationship comes from UNECE R148 (contains provisions of R4/R6/R7/R23/R38/R50/R77/R87/R91) and R150 (contains provisions of R3/R27/R69/R70/R104). It is **not** a blanket declaration that older approvals are invalid. Treat consolidated variants as a specific optional approval-regime hypothesis, not a gold-derived regulation allowlist.
5. **Unemitted obligation audit:** `tools/audit_target_coverage.py` reports high-risk selected clauses that produced no directly cited obligation, including on packets marked `completed`. This is recall triage, not proof of a missed obligation and never reads gold.

## Commands on your FCF Windows laptop (PowerShell)

Set the same `py` alias that passed your earlier tests; for example:

```powershell
Set-Location C:\UVC2026\farsighted
$Py = "C:\Users\haqs\AppData\Local\Python\pythoncore-3.14-64\python.exe"

# Check the new code before any paid calls
& $Py -m pytest -q tests
& $Py -m pytest -q src/tests
& $Py -m pytest -q experiments

# No Claude calls; see how many requirements and review batches exist
& $Py -m tools.refine_submission `
    --run .\runs\dev-full-updated-v1 `
    --out .\runs\dev-reviewed-v2 `
    --dry-run

# No Claude calls; find target clauses that need targeted recall inspection
& $Py -m tools.audit_target_coverage `
    --run .\runs\dev-full-updated-v1 `
    --out .\runs\dev-reviewed-v2\source-coverage-audit.json

# Claude reviewer only; reuses the saved 1,118 extractions and the global
# exact-request cache. Explicit spending caps are local estimates, not
# guaranteed billing limits. Use the same environment API key as previously.
& $Py -m tools.refine_submission `
    --run .\runs\dev-full-updated-v1 `
    --out .\runs\dev-reviewed-v2 `
    --config .\config\runtime.json `
    --online --max-attempts 400 --max-usd 45
```

The reviewer is purposely separate from `src.pipeline run`: **do not repeat the full $30 extraction** before reviewing the saved output. A completed pass writes `review-status.json`, `review-summary.json`, review decisions and four candidate JSONs. An incomplete pass writes `partial_no_submission_written` and **no candidate submission**. It is safe to rerun with a renewed budget; completed exact requests use the response cache when available. There is no automatic leaderboard upload.

## Important limitations

- No live Claude request or full index/PDF extraction was performed in the isolated review environment. Tests exercise simulated model responses and validation on the available reference schema; they do not establish requirement-level F2 or front-bumper generalization.
- The original source snapshots had no local `runs/shared-index`, complete PDF corpus or real `ledger.json`, so actual review decisions need execution on your laptop.
- The `prefer_consolidated` alternatives in the 1,118-row diagnostic would remove 338 legacy-family predictions *before* model review (780 left). This is a **hypothetical output-volume reduction only**. It cannot by itself prove improved precision and would still have a perfect-recall F2 ceiling of about 61.7% on the 190-reference development case.
- A complete run with a sizeable review queue may still produce many false positives. If the reviewed variants remain too large or their citation/condition samples are poor, investigate individual high-priority errors before another extraction.
- These scripts deliberately never read `expected.json`. If you compare versions against the development reference, use it **only in the separate offline evaluator**; no production thresholds, hard-coded gold regulation lists or source-location allowlists.
- `src/refinement.py` uses original cited lines, not the entire normative PDF. A model cannot prove that absent source text proves no obligation; this is why unsupported-by-source deletion is treated as a distinct, more aggressive variant.

## Before pushing

Keep the raw prior run and original submission unchanged, and inspect the overlay diff. `git status --short` should list the changed/new files from this patch. Run the three test directories separately to avoid two same-named `test_indexer.py` modules colliding during global pytest discovery. Only commit the targeted code changes and the guide, **not** `runs/`, PDFs, `expected.json`, API credentials or generated submissions. Use your pre-existing temporary Git authentication method if normal push is blocked on the FCF laptop.

## Primary-source references

- UN R148 consolidation introduction: https://eur-lex.europa.eu/eli/reg/2021/1719/oj
- UN R150 consolidation introduction: https://eur-lex.europa.eu/eli/reg/2021/1721/oj
- Claude structured JSON API: https://platform.claude.com/docs/en/build-with-claude/structured-outputs
