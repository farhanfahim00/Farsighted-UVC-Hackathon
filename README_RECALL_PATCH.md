# Sub-goal recall recovery patch

This patch does not replace the existing sub-goal pipeline. It adds a second pass that:

1. Loads the already-valid base `submission_subgoal.json`.
2. Reuses the old/new PDF diff.
3. Selects every genuinely new clause (`kind == new_clause`) generically.
4. Sends one clause per Claude request, together with additions already captured for that paragraph.
5. Asks only for *additional* atomic `added` obligations, one row per independently testable duty.
6. Verifies paragraph/page, quarantines invalid rows, merges only valid new additions, and runs the official validator.

It never reads `expected.json`.

## Install

Copy:

- `tools/recover_subgoal_additions.py` -> `<repo>\tools\recover_subgoal_additions.py`
- `tests/test_subgoal_recovery.py` -> `<repo>\tests\test_subgoal_recovery.py`

## Test

```powershell
Set-Location C:\UVC2026\farsighted
$Py = "C:\Users\haqs\AppData\Local\Python\pythoncore-3.14-64\python.exe"
& $Py -m pytest -q tests/test_subgoal_recovery.py
```

## Free preflight

```powershell
& $Py -m tools.recover_subgoal_additions `
  --base-submission .\runs\subgoal-dev-v1\submission_subgoal.json `
  --out .\runs\subgoal-dev-recall-v2
```

On the current R175 development case this should report `base_changes: 21` and `new_clause_targets: 34`. No API calls are made.

## Online recovery

```powershell
& $Py -m tools.recover_subgoal_additions `
  --base-submission .\runs\subgoal-dev-v1\submission_subgoal.json `
  --out .\runs\subgoal-dev-recall-v2 `
  --online `
  --page-images `
  --max-attempts 50 `
  --max-usd 12
```

The `$12` value is a local reservation ceiling, not a prediction of actual provider spend. The recovery prompts use a reduced 3,500-token output cap and small one-clause contexts.

If a request fails, rerun the same online command. Successful responses are cached; only missing calls should need provider access.

## Validate

```powershell
& $Py tools\validate.py `
  .\runs\subgoal-dev-recall-v2\submission_subgoal.json `
  --requirements-old .\subgoal\dev\requirements_old.json

& $Py -m tools.score_subgoal_offline `
  .\runs\subgoal-dev-recall-v2\submission_subgoal.json `
  --expected .\subgoal\dev\expected.json
```

The offline score is development-only structural diagnostics, not the official semantic score.

## Output

A successful run creates:

`runs\subgoal-dev-recall-v2\submission_subgoal.json`

The original 21-change submission remains unchanged and is your fallback.
