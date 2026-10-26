# Farsighted

From a product spec and drawing to every UN Regulation requirement the part must fulfil, each traceable to its source page and paragraph.

## The hackathon

Industrial Agents Hackathon, Munich, 24 to 25 October 2026, organised by Manage & More, UVC Partners and Anthropic.
Track: Certivity. Team: Farhan Fahim Taimoor and Sami Ul Haq. It was our first hackathon. We did not win.

## The problem

Engineers find every obligation in long, versioned UN Regulations by hand.

- **Main goal:** a product spec plus an exploded view drawing go in, and a JSON list of requirements comes out. Each requirement cites the regulation, the base version, the page and the paragraph.
- **Optional sub-goal:** given requirements from an old regulation version and the new version, flag which ones are modified, removed or added.
- **Practice case:** a rear lamp, with a public answer key.
- **Scored case:** a front bumper, with a hidden answer key.
- **Ranking metric:** F2, where recall counts more than precision.

## Our approach

The idea is simple: code finds and cites the evidence, and Claude only judges it.

1. **Deterministic PDF index** with exact pages. It found 121 of 121 expected dev paragraphs exactly.
2. **Product profile** built from the spec and drawing, with evidence and an explicit "unknown" status. Unknown never excludes a regulation.
3. **Regulation screening** with a reason for each decision, recall first.
4. **Overlap check** that keeps the newer consolidated regulation when two cover the same ground.
5. **Section and sub-section selection.**
6. **Extraction** into one obligation per requirement.
7. **Calibrated review.** An item is kept only if its chance of being right is above about F2/5 (about 15 percent).
8. **Export with citations taken from the index**, never from the model.
9. **The official validator.**

## Results

- **Dev rear lamp (platform):** F2 69.4 percent, precision 39.8, recall 85.3. The first version scored 32.3.
- **Held-out front bumper, final upload:** F2 69 percent, 317 requirements across 7 regulations.
- **Sub-goal dev:** flag recall 87.5 percent, change accuracy 100 percent, flag precision 48.8 percent.
- **Held-out sub-goal:** [submissions/subgoal/regulation-changes-v48-153.json](https://github.com/farhanfahim00/Farsighted-UVC-Hackathon/blob/main/submissions/subgoal/regulation-changes-v48-153.json) (153 changes). TODO: add the held-out sub-goal score, none is recorded in the repo.
- **Held-out main submission (317 requirements):** [submissions/main/front-bumper-candidate-317.json](https://github.com/farhanfahim00/Farsighted-UVC-Hackathon/blob/main/submissions/main/front-bumper-candidate-317.json). A 348-requirement variant is next to it.

The final submission files were reviewed and refined after the pipeline runs, so they are not reproducible from a single pipeline run.

Weak points:

- Precision, which is the price of high recall.
- Regulations we judged applicable that the experts did not list.
- Wording differences from the expert list.
- Only one graded example to learn from.
- Screening that is not perfectly repeatable.

## Tested and rejected

- Filtering out vehicle-level regulations: it would remove most bumper regulations.
- Assuming the spec's part list is complete: it would drop pedestrian and sensor sections.
- A blanket test-method filter: it would drop most of a collision-test regulation.
- Raising the review threshold: dev F2 fell from 69.4 to 67.7.

## Where things are

| Branch | What it holds |
|---|---|
| `main` | Showcase: Sami's pipeline, the Streamlit demo, the submission files, notes |
| `farhan` | Farhan's main-goal pipeline: `src/run_case.py`, presets, `RUNBOOK.md` |
| `sami` | Sami's sub-goal pipeline (`tools/subgoal_pipeline.py`, `SUBGOAL_GUIDE.md`) |

Folders on `main`:

| Folder | Contents |
|---|---|
| `src/` | Pipeline code and prompts |
| `demo/` | Streamlit demo (`app_v2.py`, older `streamlit_app.py`) and the static `Farsighted-Demo/index.html` |
| `tools/` | Certivity's tools (MIT) plus our indexing, audit, sub-goal and demo-builder scripts |
| `schemas/` | Certivity's submission schemas (MIT) |
| `submissions/` | Our final held-out submission files |
| `docs/notes/` | Working notes and guides written during the event |

## Getting the data

The Certivity data is not included here because of Certivity's data licence (see `LICENSE-DATA.md`). Get the Certivity hackathon package (`cases/`, `subgoal/`, `regulations/index.json`) from Certivity and copy it into the repo root. Then run:

```bash
python tools/fetch_regulations.py
```

The download links may no longer work after the event. Regulation PDFs are never committed.

## Setup

Python 3.12 is needed.

Mac or Linux:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt   # only for the tests
```

Windows (PowerShell):

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

A conda environment works as well. Create a `.env` file in the repo root with `ANTHROPIC_API_KEY=...`. It is git-ignored and must never be committed.

## Run the demo (no API calls)

```bash
python -m streamlit run demo/app_v2.py
```

The demo reads results from `build/` and `runs/`, which are not in git. On a fresh clone, upload a submission JSON from `submissions/` in the sidebar, plus the spec and drawing from the Certivity package.

`demo/streamlit_app.py` is the older version. `demo/Farsighted-Demo/index.html` is a static demo you can open in a browser.

## Run the main pipeline

This uses the Anthropic API and costs money. `RUNBOOK.md` measures a front-bumper-sized run at about EUR 11 to 17 with the `v8_b_lean` preset.

```bash
git switch farhan
export CASE=heldout-front-bumper            # the folder name under cases/
python tools/fetch_regulations.py
python src/indexer.py
python src/check_index.py                   # dev coverage should print 121/121 and 0 integrity problems
FARSIGHTED_PRESET=v8_b_lean python src/run_case.py cases/$CASE 2>&1 | tee build/run_$CASE.log
python tools/validate.py build/cases/$CASE/submission.json
```

`run_case.py` runs every stage up to and including the export. `RUNBOOK.md` on the `farhan` branch lists all presets and options. Every API answer is cached in `build/cache/`, so rerunning after a failure repeats no paid call.

## Run the sub-goal

This uses the Anthropic API. The command below caps spending at 4 USD.

```bash
git switch sami
python -m tools.subgoal_pipeline \
    --old-pdf subgoal/heldout/old.pdf \
    --new-pdf subgoal/heldout/new.pdf \
    --requirements-old subgoal/heldout/requirements_old.json \
    --files-index subgoal/heldout/files.json \
    --case-id subgoal-heldout \
    --out runs/subgoal-heldout \
    --online --page-images --max-attempts 18 --max-usd 4
python -m tools.finalize_subgoal runs/subgoal-heldout/needs-review.json \
    --out runs/subgoal-heldout/submission_subgoal.json
python tools/validate.py runs/subgoal-heldout/submission_subgoal.json \
    --kind subgoal --requirements-old subgoal/heldout/requirements_old.json
```

`SUBGOAL_GUIDE.md` on the `sami` branch has the dev run and the review steps in more detail. Its examples are written for Windows PowerShell. TODO: confirm the exact `--case-id` for the held-out sub-goal; the guide only gives a placeholder.

## Validate any submission

```bash
python tools/validate.py <file>
python tools/validate.py <file> --kind subgoal --requirements-old <requirements_old.json>
```

## Disclaimer

This is a research prototype built in about 31 hours. It is not a substitute for type approval or legal review.

## Thanks

Thanks to Certivity for the challenge, and to Manage & More, UVC Partners and Anthropic for organising the hackathon.
