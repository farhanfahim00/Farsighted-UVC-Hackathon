# RUNBOOK: held-out case (Sunday)

Everything below runs from the repository root in the `Farsighted` conda environment (`conda activate Farsighted`).
`.env` must hold `ANTHROPIC_API_KEY`. Nothing here is committed for you.

## 0. Before you start (Saturday night)
- Commit or stash your local work, so that `git pull` has nothing to merge into.
- Decide the budget. A full run on a front-bumper-sized case costs about **EUR 22 to 32** (table in section 5), well above
  the dev experiments. Decide whether that is acceptable before 08:00.
- Keep `build/cache/` (every dev API answer is cached there; deleting it makes dev reruns cost money).

## 1. Get the case (as soon as it is released)
```bash
git pull upstream main                      # adds cases/<heldout-folder>/ and subgoal/
ls cases/                                   # the folder name is the case_id; the commands below use $CASE
export CASE=heldout-front-bumper            # <- replace with the real folder name if it differs
python tools/fetch_regulations.py           # downloads the PDFs of the held-out case too (skips files already there)
python src/indexer.py                       # rebuilds build/*.jsonl, regulation_scopes.json (about a few minutes)
python src/check_index.py                   # dev coverage must still print 121/121 and 0 integrity problems
ls tools/                                   # preflight: none exists today. If upstream adds tools/preflight*, run it on the submission file in step 4.
```

## 2. Run the pipeline
Pick the preset (section 3). The recommended first run is `v7_c`:
```bash
FARSIGHTED_PRESET=v7_c python src/run_case.py cases/$CASE 2>&1 | tee build/run_$CASE.log
```
Stages and where they stop being repeated: profile (2), screening (3), overlap (3b), sections (4), sub-sections (4b),
extraction (5), faithfulness (5b), review (5c, only the prompt the preset uses), export (6). A finished stage is skipped on the
next call (its output file exists), and every API answer is cached, so rerunning the same command after a failure repeats
no paid call.

Look at the log after the screening stage: the number of regulations that apply, and `cases_extra`-style surprises (a
whole-vehicle regulation kept for an exterior part is expected; do NOT turn on `SCREEN_EXCLUDE_VEHICLE_LEVEL`, see section 6).

## 3. Presets (`FARSIGHTED_PRESET` unset = **v7_c**, the default)
| Preset | Switches set | What it exports |
|---|---|---|
| `v5_a` | none (config defaults) | drops test_method / conformity_of_production unless high, drops low confidence, basic review of medium items |
| `v7_a` | `REVIEW_INSTEAD_OF_CATEGORY_DROP` | v5_a + medium test_method / CoP items go to the basic review instead of being dropped |
| `v7_b` | v7_a + `REVIEW_LOW_CONFIDENCE` | + low-confidence items reviewed (basic prompt) |
| `v7_c` | v7_b + `REVIEW_PROMPT = "criteria"` | same, with the criteria review prompt (`review_criteria_system.md`) (default) |
| `v8_a` | `REVIEW_ALL` (criteria prompt) | every extracted item reviewed, any confidence or category, kept at probability >= 15; no floor, no category rules |
| `v8_b` | v8_a + `REVIEW_BORDERLINE_TWICE` | items with a first probability of 10-40 get a second review (request differs by a "Review pass 2." line); the mean decides |

Dev result (local judge, requirement level; the judge reads about 0.05 below the platform for v5_a):
v5_a F2 0.611, v7_a 0.568, v7_b 0.555, v7_c 0.648, v8_a 0.655, **v8_b 0.659** (differences between v7_c, v8_a and v8_b are within noise).
v8 needs reviews of the high-confidence items too (about 215 on dev, 24 calls, 0.6 USD) and, for v8_b, a second pass on about 320 borderline items (35 calls, 0.9 USD):
`FARSIGHTED_PRESET=v8_b python src/run_case.py cases/$CASE` runs both (stage 5c). Upload order for the held-out case: v7_c first, then v8_a or v8_b.

Further variants from the same extraction need no new extraction. Each preset needs reviews for its own items, which cost
a few EUR per prompt (`python src/review_medium.py cases/$CASE --dry-run` prints the estimate first):
```bash
FARSIGHTED_PRESET=v5_a python src/review_medium.py cases/$CASE   # asks only for reviews that are missing for this preset
FARSIGHTED_PRESET=v5_a python src/export.py cases/$CASE --name v5_a       # writes build/cases/$CASE/submission_v5_a.json, validated
```

## 4. Validate and copy for upload
```bash
python tools/validate.py build/cases/$CASE/submission.json                   # prints "OK: ... is valid"; exit code 0 required
python - <<'PY'
import json,os; s=json.load(open(f"build/cases/{os.environ['CASE']}/submission.json")); print(s["team_id"], s["case_id"], len(s["requirements"]))
PY
cp build/cases/$CASE/submission.json ~/Desktop/UPLOAD_${CASE}_v7_c_$(date +%H%M).json
```
`case_id` must equal the folder name, `team_id` is `team-07`. For a variant exported with `--name X`, copy
`submission_X.json` the same way with X in the file name. The last valid upload before the lock counts for the held-out
case, so do not upload a worse file at the end.

## 5. Estimated cost and time of a full bumper-sized run
From token counting of the synthetic front bumper (no generation; 5,232 candidate paragraphs, 342 extraction batches,
about 3.05 M input tokens at 0.533 tokens per character), Sonnet at $3 / $15 per million tokens, USD to EUR 0.92:

| Stage | Calls | Cost (EUR) |
|---|---|---|
| Profile (Opus, 1 call), screening (56), overlap, sections (16), sub-sections (about 32) | about 105 | 2.5 to 3 |
| Extraction + second look | about 350 | **15 to 22** (input 9.1 USD; output 7.5 to 14.3 USD, from 96 to 182 output tokens per candidate measured on two runs) |
| Faithfulness | not measured on the bumper | about 2 to 4 (an estimate from dev: 1.5 USD) |
| Review, one prompt | not measured on the bumper | about 2 to 5 (an estimate: about 0.0025 USD per item, 800 to 1,800 items) |
| **Total** | | **about 22 to 32** |

Time at the current concurrency (6 workers): the 11-call bumper test took 18 s, which gives about 10 minutes for the 350
extraction calls; earlier dev extraction runs took 7 to 35 minutes depending on rate limits. Plan 25 to 60 minutes for
the whole pipeline. Screening, sections and review run in a few minutes each.

Levers if the cost is too high (none is applied automatically): skip regulations whose scope is clearly for other vehicle
categories (about 12% of the bumper's candidates are in a regulation for buses), or extract in two goes with
`python src/extract.py cases/$CASE --only "UN Rxx,UN Ryy"` (partial run; overwrites `extracted.jsonl`, so do it once, with all
regulations you want).

## 6. Settings that are safe or not for a bumper
- **v5_a settings drop most test requirements of a collision-test regulation.** On the synthetic bumper, for the
  protective-devices regulation: 52 extracted requirements, none high confidence, 35 of them `test_method`; v5_a exports 6
  (88% dropped), v7_a 37, v7_b 52, v7_c 15 and v8_a 15 (same items), v8_b 20 (the criteria review drops test set-up, keeps conditions with a speed or limit; 27 of 35 test_method items are dropped, 7 of 8 test_requirement items kept).
  The dev-derived rule "test_method unless high" does not carry over, so prefer **v7_c** for the bumper.
- `SCREEN_EXCLUDE_VEHICLE_LEVEL` stays **off**: on the bumper it would exclude pedestrian protection, external projections,
  frontal collision and lighting installation regulations.
- `VEHICLE_CATEGORY_CHECK` is safe (no effect on dev; flags only regulations scoped to other categories) but only helps
  when the profile lists the exact categories. Turn it on with `FARSIGHTED_OVERRIDES="VEHICLE_CATEGORY_CHECK=1"` if the profile says M1 only.

## 7. If a stage fails
| Failure | What to do |
|---|---|
| Any stage stops with an API error | Run the same `run_case` command again: finished stages are skipped and answered calls are cached. |
| `indexer` / PDF download fails | Run `python tools/fetch_regulations.py` again (skips what exists); `python src/indexer.py --allow-missing` only as a last resort. |
| Profile is empty or wrong | Edit nothing by hand; run `python src/product_profile.py cases/$CASE --force`, then `run_case --force` from stage 3 (cache keeps paid answers). |
| Extraction has `failed` / `no_answer` paragraphs | Rerun `python src/extract.py cases/$CASE`: batches already answered come from the cache for free, failed ones are asked again. Then run `run_case` (without `--force`) to continue. |
| Faithfulness fails or runs out of money | `python src/export.py cases/$CASE --no-faithfulness --name nofaith` (uncorrected texts). |
| Sub-section stage fails | `FARSIGHTED_OVERRIDES="SUBSECTION_FILTER=0"`. |
| Review is missing or fails | Export raises "no review for ..."; run `python src/review_medium.py cases/$CASE` again (unanswered items are asked again), or fall back: `FARSIGHTED_OVERRIDES="REVIEW_MEDIUM=0,REVIEW_INSTEAD_OF_CATEGORY_DROP=0,REVIEW_LOW_CONFIDENCE=0"` exports the v3-style file with no review. |
| Time is nearly up | Export whatever exists: the cheapest valid fallback ladder is v7_c, then v5_a, then the no-review line above. Validate and upload the best file you have. |
