# Start here

Work in **C:\UVC2026\farsighted**, branch `sami`. No separate repository is needed.
`src/` is the connected runtime. Keep shared JSON interfaces stable when working
with your teammate. Generated data stays under ignored `runs/`.

```text
farsighted/
  CODEBASE.md              this guide: open first
  config/runtime.json      team, model, concurrency, attempt/spend reservations
  src/
    pipeline.py            ONE entry point: prepare or run
    contracts.py           fixed JSON schemas, prompt loading, cache fingerprint
    product.py             original specification + actual drawing -> profile
    selection.py           all-regulation screening + candidate safety net
    packets.py             indexed targets + context + page images + aliases
    llm.py                 network calls, timeout/retry, cache, budget, concurrency
    extraction.py          extraction, truncation splitting, one row-repair call
    guard.py               row validation, quarantine, deduplication, citations/export
    prompts/               fixed profile, screen and extract instructions
  tools/
    validate.py            official submission validator
    fetch_regulations.py   official downloader
    score_local.py         official local page/regulation evaluation
    index_evidence.py      preferred OFFLINE index generator
    paragraph_structure.py frozen paragraph parser
    check_index.py         source-location evaluation (uses gold only here)
    product_contract.py    source-evidence packet preparation and profile validation
    index_regulations.py   earlier baseline; runtime imports its small helpers only
  tests/test_runtime.py    connected-runtime regression tests; mocked provider only
  experiments/             previous pilot, key setup, earlier focused tests
  cases/                   organizer product inputs and separate dev answers
  regulations/             organizer index and downloaded PDFs
  runs/                    ignored packets, caches, profiles, ledger, submissions
```

The earlier `experiments/pilot.py` remains a diagnostic record, not the full solver.
Its 15 cached responses are not automatically imported as production results:
they used different prompts, source aliases and input context. Do not confuse a
production cache miss with missing authentication.

## Commands: PowerShell

```powershell
Set-Location C:\UVC2026\farsighted
$runtimePython = 'C:\UVC2026\certivity-env\Scripts\python.exe'
```

First prepare one connected slice locally. This spends zero API credits:

```powershell
& $runtimePython -m src.pipeline prepare --case cases\dev-rear-lamp --inputs spec.md exploded_view.png --targets 'UN R10::6.9.1' --out runs\connected-r10
```

Then run it. Without `--online` only existing matching responses can be used.
Adding `--online` permits paid requests, within the attempt and local reservation
limits. One profile call and one extraction call are typical; repairs/retries
also count. The default timeout is 300 seconds, concurrency 4, output cap 12k.

```powershell
& $runtimePython -m src.pipeline run --case cases\dev-rear-lamp --inputs spec.md exploded_view.png --targets 'UN R10::6.9.1' --out runs\connected-r10 --online --max-attempts 3 --max-usd 3
```

For the whole development case, omit `--targets`. Screening covers the 56 indexed
regulations in batches of four; a full run needs a larger explicit attempt budget.
Start with a bounded run and inspect progress rather than assuming every packet
will finish. Cached completed responses make subsequent invocations resumable.
Limits are per invocation, not a permanent account-level cap. Prices in config
are estimates; reservations are conservative local controls, not provider billing
guarantees. Failed-attempt reservations remain held because delivery is uncertain.

```powershell
& $runtimePython -m src.pipeline run --case cases\dev-rear-lamp --inputs spec.md exploded_view.png --out runs\dev-main --online --max-attempts 30 --max-usd 10
```

Use the actual held-out folder and filenames when released. Do not copy lamp
properties or a lamp-specific regulation list. Version policy currently requires
a base-only index. An organizer change needs an explicit case-policy adjustment.

## Where to look after a run

- `run-status.json`: read FIRST. Prepared/partial/failed/complete, call count,
  validator result, unresolved packet count, submission checksum and estimated cost.
- `product-input.json`: original source passages, qualifiers and image asset IDs.
- `profile.json`: model-proposed facts plus reference/consistency validation.
- `scope-input.json`, `selection.json`: scope inputs and selected candidates.
- `packets.json`: target/context line mappings and unresolved cross-references.
- `ledger.json`: accepted rows, exact indexed evidence, quarantined rows, repair
  history and packet errors. One bad row does not discard accepted neighbors.
- `submission.json`: ONLY official-format fields, team read from config. Never
  assume a syntactically valid file is a completed or semantically accurate case.
- `official-validation.json`: unchanged official validator's result.
- `usage.jsonl`: attempt/cache/usage records; no keys or request headers.

After a failed invocation an older submission may still exist. Its checksum and
the new run status prevent treating it as the new result. No automatic portal
upload exists. Target-specific diagnostic runs remain marked partial even if
their rows pass the official validator.

## What is automatic, and what is still uncertain

Python prepares evidence and preserves qualifiers; Claude proposes product facts,
screens candidate sections and writes obligations. Original spec text and actual
image bytes are supplied. Structured outputs use a fixed schema, without forced
tool calls or removed sampling parameters. Evidence aliases are mapped back to
index line IDs, PDF physical pages, bounding boxes and paragraph owners.

Table packets initially carry full page images to preserve headers/notes. Target
groups are bounded by text length; expanded context has a separate maximum.
Oversized contexts are explicitly reported for review. Truncated output splits
target line ranges up to depth three while retaining context. Unresolved refs,
numeric warnings, missing context, ambiguous diagrams and semantically incorrect
facts remain risks. Schema validity and source IDs do not prove engineering truth.

Unknown/representative/conflicting/visual-uncertain facts cannot independently
justify exclusion. The equality comparator is not used as an applicability filter.
No blanket rules discard section 2 or all introductory text. The safety net adds
modal paragraphs/tables to model-selected sections; candidate recall is unmeasured
until evaluation. Full-case F2 and bumper performance are not yet established.

## Tests

```powershell
& $runtimePython -m unittest discover -s tests -p 'test_*.py' -v
& $runtimePython -m unittest discover -s experiments -p 'test_*.py' -v
```

Runtime tests exercise concurrent budgets, retries/timeouts, cache replay and
invalidation, truncation splitting, per-row quarantine, deduplication, real index
citations, image bytes, qualification policy and a connected CLI/official-validator
run with simulated model responses. Mocked outputs do not measure model quality.
These tests require the generated evidence index and provided development inputs.

## Working together

Sami: model client, packet builder, extraction, guard/export.
Teammate: profile prompt, scope/section selection and evaluation/miss analysis.
Both coordinate changes to `contracts.py`, `product_contract.py`, and config.
Use `sami` for these local edits; commit/push only the intended code and docs.
Keep `.env`, downloaded PDFs, run outputs and caches out of commits. The optional
old/new PDFs are currently untracked: do not stage them with a blanket `git add .`.
