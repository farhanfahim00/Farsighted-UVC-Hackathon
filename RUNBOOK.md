# RUNBOOK: held-out case (Sunday)

Everything below runs from the repository root in the `Farsighted` conda environment (`conda activate Farsighted`).
`.env` must hold `ANTHROPIC_API_KEY`. Nothing here is committed for you.

## 0. Before you start (Saturday night)
- Commit or stash your local work, so that `git pull` has nothing to merge into.
- Decide the budget. A full run on a front-bumper-sized case costs about **EUR 11 to 17** with the `v8_b_lean` preset and EUR 16 to 25 with
  `v8_b` (table in section 5, Sonnet 5.5 at $2 / $10 per million tokens). The team has EUR 50 reserved for Sunday.
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
Pick the preset (section 3). The recommended run is `v8_b_lean` (v8_b with the cost cuts; the config default, unset, is plain `v8_b`):
```bash
FARSIGHTED_PRESET=v8_b_lean python src/run_case.py cases/$CASE 2>&1 | tee build/run_$CASE.log
```
Stages and where they stop being repeated: profile (2), screening (3), overlap (3b), sections (4), sub-sections (4b),
extraction (5), faithfulness (5b), review (5c, only the prompt the preset uses), export (6). A finished stage is skipped on the
next call (its output file exists), and every API answer is cached, so rerunning the same command after a failure repeats
no paid call.

Look at the log after the screening stage: the number of regulations that apply, and `cases_extra`-style surprises (a
whole-vehicle regulation kept for an exterior part is expected; do NOT turn on `SCREEN_EXCLUDE_VEHICLE_LEVEL`, see section 6).

## 3. Presets (`FARSIGHTED_PRESET` unset = **v8_b**, the default)
| Preset | Switches set | What it exports |
|---|---|---|
| `v5_a` | none (config defaults) | drops test_method / conformity_of_production unless high, drops low confidence, basic review of medium items |
| `v7_a` | `REVIEW_INSTEAD_OF_CATEGORY_DROP` | v5_a + medium test_method / CoP items go to the basic review instead of being dropped |
| `v7_b` | v7_a + `REVIEW_LOW_CONFIDENCE` | + low-confidence items reviewed (basic prompt) |
| `v7_c` | v7_b + `REVIEW_PROMPT = "criteria"` | same, with the criteria review prompt (`review_criteria_system.md`) (default) |
| `v8_a` | `REVIEW_ALL` (criteria prompt) | every extracted item reviewed, any confidence or category, kept at probability >= 15; no floor, no category rules |
| `v8_b` | v8_a + `REVIEW_BORDERLINE_TWICE` | items with a first probability of 10-40 get a second review (request differs by a "Review pass 2." line); the mean decides (default; platform on dev: 404 req, P 39.6%, R 84.2%, F2 68.7%) |
| `v8_c` | v8_b + `SPEC_LISTS_COMPLETE = True` | sub-sections for devices/functions not on an explicit list in the materials are dropped too. Dev: 403 req (1 fewer than v8_b), judge F2 .660 vs .659: no gain. **Not recommended for a bumper**: on the synthetic bumper it drops 88 more sub-sections, including pedestrian headform tests, the AEBS pedestrian test, sensor EMC clauses and the number-plate lamp clauses. Needs no new calls if `subsections.json` exists (it holds both settings). |
| `v8_b_lean` | v8_b + cost cuts | same exported requirements as v8_b, but: no extraction of candidates inside sub-sections the export drops anyway (`SKIP_DESELECTED_BEFORE_EXTRACTION`), `FAITHFULNESS_CHECK` off, `DROP_RATIONALE_PARTS` on (also the default in every preset; export-only, no calls), and the vehicle category check on with the primary category (`VEHICLE_CATEGORY_CHECK`, `VEHICLE_CATEGORY_USE_PRIMARY`) |

Dev result (local judge, requirement level; the judge reads about 0.05 below the platform for v5_a):
v5_a F2 0.611, v7_a 0.568, v7_b 0.555, v7_c 0.648, v8_a 0.655, **v8_b 0.659** (differences between v7_c, v8_a and v8_b are within noise).
v8 needs reviews of the high-confidence items too (about 215 on dev, 24 calls, 0.6 USD) and, for v8_b, a second pass on about 320 borderline items (35 calls, 0.9 USD):
`FARSIGHTED_PRESET=v8_b python src/run_case.py cases/$CASE` runs both (stage 5c). Upload order for the held-out case: `v8_b_lean` (= v8_b) first; if time remains, `v7_c` as a second, different file. Platform on dev: v7_c 452 req, P 35.4%, R 84.2%, F2 66.0%; v8_b 404 req, P 39.6%, R 84.2%, F2 68.7%.

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

### 4b. Sunday upload plan: dedup on, threshold variants (export only, no API calls)
`DEDUP_NEAR_DUPLICATES` is on in every preset (config default; `FARSIGHTED_OVERRIDES="DEDUP_NEAR_DUPLICATES=0"` turns it off). Within one
regulation it drops a requirement that is a near-duplicate of a better one (token-set similarity >= 0.9 or >= 90% containment, no more than
one differing content word, identical numbers/units/limit words); the survivor has the higher review probability. Never across regulations.
After the held-out run (the cache then holds extraction and reviews; bumper-style case needs the lean overrides for the plain presets):
```bash
export OV="FAITHFULNESS_CHECK=0,VEHICLE_CATEGORY_CHECK=1,VEHICLE_CATEGORY_USE_PRIMARY=1"     # only if the run used v8_b_lean
for P in v8_a_dedup v8_a_t25 v8_a_t35 v8_b_dedup; do
  FARSIGHTED_PRESET=$P FARSIGHTED_OVERRIDES="$OV" python src/export.py cases/$CASE --name $P     # validates
  cp build/cases/$CASE/submission_$P.json ~/Desktop/UPLOAD_${CASE}_${P}_$(date +%H%M).json
done
```
Upload in that order (v8_a_dedup, v8_a_t25, v8_a_t35, v8_b_dedup); `v8_a_t25` / `v8_a_t35` differ from `v8_a_dedup` only in the review keep
threshold (>= 25 / >= 35 instead of >= 15). Re-upload the best one last, before 12:30. Dev (local judge, cached): v8_a_dedup 400 req F2 .656,
v8_a_t25 319 req F2 .654, v8_a_t35 251 req F2 .583 (recall falls fast), v8_b_dedup 396 req F2 .661.

## 5. Estimated cost and time of a full bumper-sized run
Token counting only (no generation) on the synthetic front bumper; Sonnet 5.5 at $2 / $10 per million tokens, cache reads
$0.20, USD to EUR 0.92. Output tokens per candidate are a range measured on two runs (96 and 182); review items per
candidate 0.30 to 0.55. Prompt caching (system prompt and profile, `PROMPT_CACHING`) is on by default in all presets: it was
off before; a test showed a 5,107-token prefix written once and read afterwards. The review prefix is only about 1,450
tokens, so no review saving is assumed.

| | v8_b | v8_b, caching off | v8_b_lean |
|---|---|---|---|
| Candidates / extraction batches | 5,232 / 342 | same | 4,238 / 285 (-366 in deselected sub-sections, -628 in a regulation for other vehicle categories) |
| Extraction + second look (USD) | 8.7 to 13.5 | 11.7 to 16.4 | 7.1 to 11.0 |
| Review: first pass + 42% second pass (USD) | 3.7 to 7.0 | same | 3.0 to 5.6 |
| Faithfulness (USD) | 3.0 to 5.5 | same | 0 (off) |
| Profile, screening, overlap, sections, sub-sections (USD) | 1.7 | same | 1.7 |
| **Total (EUR)** | **15.7 to 25.4** | 18.4 to 28.1 | **10.9 to 16.8** |
| API calls | 770 to 1,040 | same | 580 to 730 |
| Wall-clock at 6 workers (1.3 to 1.7 s per call effective) | 17 to 29 min | same | 13 to 21 min |

Rate limits can double the time (earlier dev extraction runs took up to 35 minutes). Opus on the second review pass was not
built (the diagnosis did not call for it); if ever added it costs about $1 to 2 more. Both presets are far under EUR 50.

## 5b. Measured: cold rehearsal on the synthetic front bumper (2026-10-25, `v8_b_lean`, empty cache)
Real figures from `build/cache/usage.jsonl` (EUR at $2 / $10, cache read $0.20, cache write $2.50, 0.92), wall-clock from the log:

| Stage | Wall-clock | Calls | EUR |
|---|---|---|---|
| 2 profile | 18 s | 1 | 0.03 |
| 3 screening | 21 s | 56 | 0.35 |
| 3b overlap | 9 s | 1 | 0.02 |
| 4 sections | 32 s | 16 | 0.31 |
| 4b sub-sections | 39 s | 26 | 0.35 |
| 5 extraction (+ second look) | 355 s | 305 | 5.79 |
| 5c review pass 1 / pass 2 | 45 s / 19 s | 60 / 25 | 0.68 / 0.26 |
| 6 export | 1 s | 0 | 0 |
| **Total** | **539 s (9.0 min)** | **490** | **7.78** |

0 failed calls, 0 truncated (all stop_reason end_turn), 1 batch re-asked for a missing answer, 0 unanswered reviews.
This is below the section 5 estimate (EUR 10.9 to 16.8, 13 to 21 min). 23 of 56 regulations apply, 16 after overlap (7 dropped),
4,723 candidates (258 skipped as deselected), 538 extracted, 174 final (v8_b = v8_b_lean export; v8_a 168).
`export.py --name v8_a|v8_b` with the plain presets demands faithfulness.jsonl; use
`FARSIGHTED_OVERRIDES="FAITHFULNESS_CHECK=0,VEHICLE_CATEGORY_CHECK=1,VEHICLE_CATEGORY_USE_PRIMARY=1"` to export them from the lean cache.

Rationale filter (`DROP_RATIONALE_PARTS`, export only): requirements from a part whose heading says "technical rationale" /
"justification" are dropped. Dev v8_a/v8_b exports are byte-identical with it; on the bumper it removes 14 GTR 9 items
(v8_a 168 -> 154, v8_b 174 -> 160); the operative legform/headform limits (GTR 9 5.1, 5.2, 7.1; R127 5.1, 5.2) stay.

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
| Cost estimate exceeded or a stage keeps failing | Fall back to `FARSIGHTED_PRESET=v8_b_lean` (cheapest), then `v7_c` (no second review pass, no review of high-confidence items), then `v5_a`. |
| Time is nearly up | Export whatever exists: the cheapest valid fallback ladder is v7_c, then v5_a, then the no-review line above. Validate and upload the best file you have. |
