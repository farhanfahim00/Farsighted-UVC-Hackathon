# Farsighted optional challenge: cached Stage-2 citation repair

## Exactly what was wrong

`src.subgoal.parse_pdf()` indexes numbered clauses such as `6.6.2.2`, not their
lettered items `6.6.2.2(a)`, `(b)`, etc. Claude correctly cited substeps, but
`tools.subgoal_pipeline` required literal parent-label equality and rejected them.
Thus 16 legitimate *source-citation formats* were rejected before semantic
assessment. This patch normalizes those citations to their verified parent PDF
clause, while preserving one independent row per substep. Pages must still
fall within that parent's actual PDF page range. It does not change Claude's
prompt, payload, model, schema or cache identifiers.

**Important:** Correct citation formatting does not establish whether a change
is genuinely added/modified/removed. The previous draft had additional
semantic classification problems: R-090 not marked removed, R-088 treated as
modified, and seven separately added details from an already-existing clause.
Do NOT assume the replayed candidate beats the earlier official 54.2% score.

## Install (Windows PowerShell, from repository root)

```powershell
Set-Location C:\UVC2026\farsighted
$Py = 'C:\Users\haqs\AppData\Local\Python\pythoncore-3.14-64\python.exe'
Copy-Item .\tools\subgoal_pipeline.py C:\UVC2026\subgoal_pipeline-v3-before-citation-fix.py -Force
Expand-Archive -LiteralPath "$env:USERPROFILE\Downloads\farsighted-subgoal-v3-citation-fix.zip" -DestinationPath . -Force
& $Py -m pytest -q tests/test_subgoal_pipeline.py -k 'normalize or substep'
```

## Replay the 66 COMPLETED model requests FREE

**Use the SAME `--out` as before** so the existing `request-cache` is reused.
Do not add `--online`; do not change the `--page-images` flag (the original
request in the user log did not use it).

```powershell
& $Py -m tools.subgoal_pipeline `
  --out .\runs\subgoal-dev-obligations-v3 `
  --replay --max-attempts 0 --max-usd 0
```

`--replay` uses `online=False`. An uncached task fails with CacheMiss instead
of making an API call. The 66 responses should remain identical; only the
citation interpretation changes. If all issues were those reported, the
validated output is `runs\subgoal-dev-obligations-v3\submission_subgoal.json`.

## Check BEFORE upload

```powershell
& $Py tools\validate.py .\runs\subgoal-dev-obligations-v3\submission_subgoal.json `
  --requirements-old .\subgoal\dev\requirements_old.json
& $Py -m tools.score_subgoal_offline `
  .\runs\subgoal-dev-obligations-v3\submission_subgoal.json `
  --expected .\subgoal\dev\expected.json
```

The second command checks structure, not semantic equivalence or leaderboard
score. It is a dev-only evaluation and the answer key is **not** loaded by
the production solver.

**Do not upload the resulting large raw candidate solely because it validates.**
Keep the official 21-change file as fallback. The next meaningful task is to
adjudicate semantic extras and the missed removal from cached evidence, not
repeat paid calls or change the PDF parser.
