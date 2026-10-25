# Farsighted sub-goal: focused old/new obligation comparison

Replace only `tools/subgoal_pipeline.py` and its updated regression test at `tests/test_subgoal_pipeline.py`. The code does not touch the main extraction pipeline, the sub-goal PDF parser, or the organizer validator.

## Install in PowerShell

```powershell
Set-Location C:\UVC2026\farsighted
$Py = 'C:\Users\haqs\AppData\Local\Python\pythoncore-3.14-64\python.exe'
Copy-Item .\tools\subgoal_pipeline.py C:\UVC2026\subgoal_pipeline-before-fix.py -Force
Copy-Item "$env:USERPROFILE\Downloads\subgoal_pipeline.py" .\tools\subgoal_pipeline.py -Force
Copy-Item "$env:USERPROFILE\Downloads\test_subgoal_pipeline.py" .\tests\test_subgoal_pipeline.py -Force
& $Py -m pytest -q tests/test_subgoal_pipeline.py tests/test_demo_builder.py
```

Alternatively, download `farsighted-subgoal-obligation-fix.zip` and extract it over the repository root, overwriting the two included paths.

## Offline preparation (no API)

```powershell
& $Py -m tools.subgoal_pipeline --out .\runs\subgoal-dev-obligations-v3
```

On the organizer's development PDFs, this prepares 66 narrow tasks: 48 changed/new-clause addition checks and 18 affected-old-requirement decisions. Every proposed addition is supplied old-version clause text; the old requirement checks receive possible new source locations.

## Paid generation (NEW model requests)

This step is **not cached** by the previous 8 broad section-batch calls because the prompts have changed. Confirm the team's API budget before running it.

```powershell
& $Py -m tools.subgoal_pipeline `
    --out .\runs\subgoal-dev-obligations-v3 `
    --online `
    --max-attempts 85 `
    --max-usd 18
```

The USD limit is a conservative local reservation, **not** a provider billing guarantee. If the run ends `INCOMPLETE`, rerunning the same command reuses successful cached requests. **Do not switch output folders while resuming**.

## Validate and measure

```powershell
& $Py tools\validate.py .\runs\subgoal-dev-obligations-v3\submission_subgoal.json --requirements-old .\subgoal\dev\requirements_old.json
& $Py -m tools.score_subgoal_offline .\runs\subgoal-dev-obligations-v3\submission_subgoal.json --expected .\subgoal\dev\expected.json
```

The offline score is a **structural** proxy only. The actual organizer score is not established until the development leaderboard evaluates the file. Preserve the older `runs/subgoal-dev-v1/submission_subgoal.json` and `runs/subgoal-dev-recall-v2/submission_subgoal.json` as fallbacks.

If validation or evidence review fails, the code writes `draft-subgoal.json`, `review-issues.json`, and possibly `needs-review.json` / `validator-problems.json` instead of silently exporting an invalid submission. Correct the draft with `tools.finalize_subgoal` without repeating paid requests.
