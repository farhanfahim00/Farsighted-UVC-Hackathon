# Development extraction pilot

## Product evidence contract (offline)

`tools/product_contract.py` prepares input evidence and validates a proposed
profile. It does not call Claude or automatically extract verified product facts.
The contract accepts component-independent property names, explicit absence,
unknowns, representative information and conflicts. The original source text
is retained under stable evidence IDs. Images are inventoried, not interpreted;
PDF text extraction does not OCR scans. Qualification detection is heuristic.

```powershell
$pilotPython = 'C:\UVC2026\certivity-env\Scripts\python.exe'
& $pilotPython tools\product_contract.py prepare --case cases\dev-rear-lamp --inputs spec.md exploded_view.png --out runs\product-dev\evidence-packet.json
```

Give Claude the packet and the actual drawing when visual interpretation is
needed. Its profile must follow the included schema. Save a future response as
`runs/product-dev/proposed-profile.json`, then validate it:

```powershell
& $pilotPython tools\product_contract.py validate --packet runs\product-dev\evidence-packet.json --profile runs\product-dev\proposed-profile.json --out runs\product-dev\validation.json
```

Validation checks evidence references, packet identity and consistency; it
cannot prove a passage entails an arbitrary model-proposed value. Qualification
flags block promotion of representative sources into definite facts. `expected.json`
is prohibited as a product input. For held-out cases use their own folder/input
names; no lamp-specific attribute list is required.

The library function `compare_condition` accepts explicit eq/ne/and/or conditions
and returns true, false or None (unknown). It preserves unresolved alternatives
and treats missing facts, provisional values and unit/type mismatches as unknown.
It does not interpret regulatory prose, convert units, implement numerical
thresholds or decide overall applicability. Do not discard requirements merely
because one comparison is unresolved, and semantically validate profile values
and condition translations before using comparisons to exclude anything.

Current priority: offline indexing. Model experiments below are opt-in and spend
credits. They are not needed for checksums, PDF parsing, paragraph anchors,
lexical condition signals, source IDs or official schema validation.

```powershell
$pilotPython = 'C:\UVC2026\certivity-env\Scripts\python.exe'
& $pilotPython tools\index_evidence.py --check-dev
& $pilotPython tools\check_index.py
```

The reviewed entry point uses the teammate's numbering/annex/table-label parser
in `tools/paragraph_structure.py` and preserves raw evidence independently.
It indexes all provided base PDFs locally into `runs/evidence-base/`: full page
text (including footnotes), raw line IDs and bounding boxes, paragraphs, explicit
source pointers, parent links, lexical condition signals and a checksum manifest.
IDs distinguish role, filename, version and file content. Unknown parent links,
tables, repeated labels and unassigned lines remain visible for review. Footnote
context links are page-level candidates, not verified attachment to a clause.
`--check-dev` evaluates exact source locations and versions after indexing; it
does not evaluate semantic correctness, requirement recall or F2. The optional
`--roles base latest --out runs/evidence-both` also indexes latest versions,
without mixing them into main-case retrieval by default.

The earlier baseline remains available for comparisons:
`tools/index_regulations.py --check-dev` writes `runs/index-base/`: page text and
line IDs, structural paragraph blocks, parent links, condition/reference signals,
and a manifest. `--check-dev` checks heading coverage only, not semantic recall.
Table captions are queued for review. Use `--tables captions --only 'UN R10'`
with a different `--out` folder to extract selected tables; merged cells and signs
still need review. An 'if', 'or', 'may' or 'shall' match is a signal, not a final
applicability decision. No source paragraph is discarded just for lacking 'shall'.

This is a small experiment, not the completed solver or a full-case submission.
It compares an isolated target paragraph with the same target plus neighbouring
PDF pages retaining table layout. Both receive the same product specification and
extraction instructions. Four fixtures cover nested markings, an EMC table, a
simple duty and an irrelevant front-lamp condition.

Use the indexed **base** PDFs for this development test. Gold answers are isolated
in `evaluation-only.json`; extraction never receives them. Results and local API
configuration are ignored by Git.

In PowerShell at the repository root:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\experiments\save_claude_key.ps1
$pilotPython = 'C:\UVC2026\certivity-env\Scripts\python.exe'
& $pilotPython experiments\pilot.py prepare
& $pilotPython experiments\pilot.py models
```

The setup command prompts with hidden input and saves the key in the ignored
`.env` locally. Never put the key in a command, a chat, a screenshot or a commit.
Direct Anthropic API is supported; do not use a gateway key here without adapting
and reviewing its endpoint/protocol first.

Choose an enabled model ID returned by `models`, then run:

```powershell
$pilotModel = Read-Host 'Paste the exact model ID from the list above (not the API key)'
& $pilotPython experiments\pilot.py run --model $pilotModel --tasks module-markings immunity-table --max-calls 4
& $pilotPython experiments\pilot.py review
```

This initial comparison makes up to four new generation jobs, each capped at
3,600 output tokens. Transient HTTP errors may retry up to twice. It saves usage
and checkpoints responses; repeating a completed request reuses its cache. A model
or prompt change creates a different request hash. No numeric dollar cap is claimed.

Then test the simple and negative controls with the same model:

```powershell
& $pilotPython experiments\pilot.py run --model $pilotModel --tasks simple-marking irrelevant-front-lamp --max-calls 4
& $pilotPython experiments\pilot.py review
```

Inspect `runs/pilot/comparison.json`, the detailed results, and the separate gold
fixtures. Match generated obligations one to one by scope, condition and constraint.
Record true positives, false positives and false negatives, then calculate
`precision=TP/(TP+FP)`, `recall=TP/(TP+FN)` and `F2=5PR/(4P+R)`.
Counts, exact quote presence and the official schema cannot establish semantic
correctness. These known development fixtures are diagnostic, not an unbiased test.

Only outputs with valid text, applicability, explicit conditional scope and exact
evidence quotes are exported into `runs/pilot/candidates/`. They contain only a few
paragraphs. Do not upload them as a completed main-case answer. Check the full
generator's future output with the unchanged official validator.

Adopt added context only if review shows useful additional matches without changed
conditions, invented numbers or irrelevant duties. If both variants lose table
relations, add structured cell extraction or page-image input before scaling.
The four small fixtures do not prove the approach generalizes to the bumper.
The context variant changes both surrounding text and table layout. If it helps,
separate those factors in a follow-up experiment before attributing the gain.

## Additional experiments implemented

- `--variant atomic`: more explicit obligation splitting; still requests copied quotes.
- `--variant grounded`: same splitting with server-enforced JSON schema and immutable
  source-line IDs. Code resolves selected IDs into exact page/quote evidence.
- `--variant grounded --refine`: audit a previous grounded output with its source
  text and a rendered page image. Uses a result for the same model when present,
  otherwise requires exactly one grounded output for that task from another model.

Example of a bounded experiment (omit `prepare` to reuse the existing packets):

```powershell
& $pilotPython experiments\pilot.py run --model claude-sonnet-5-5 --variant grounded --tasks module-markings immunity-table irrelevant-front-lamp --max-calls 3
& $pilotPython experiments\pilot.py run --model claude-sonnet-5-5 --variant grounded --refine --tasks module-markings immunity-table --max-calls 2
& $pilotPython experiments\pilot.py review
```

The review model can differ from the extraction model, using `--model` with an
enabled model ID. Gold is still never supplied. Source IDs and valid JSON prevent
format/citation fabrication; they do not prove correct table interpretation.
Conditional scope is retained in exported text even if the model only put it in
its internal condition field. Every exported file is a small **candidate**, not an
approved submission. A higher item count is not necessarily a better result.
