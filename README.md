# Certivity hackathon challenge: from a product specification to the requirements it must fulfil

Certivity turns regulations into structured text with paragraph-level citations. Your task is the problem our users have: a company builds a product, and its engineers need to know every requirement the product must fulfil under the UN Regulations that apply to it.

You may use any tools, models and methods you like. **You submit outputs, not code.** Certivity never runs your code.

## The two goals

**Main goal (required).** Input: a technical specification and/or an exploded view of a component or product. Output: the engineering requirements it must fulfil. Each requirement cites the UN Regulation it comes from, **at least to the page number**. A test case per requirement is optional.

**Sub-goal (optional).** Input: the requirements derived from one version of a UN Regulation, and a later version of the same regulation. Output: which requirements must change, and what in the regulation caused it.

## What is in this package

```
README.md
LICENSE  LICENSE-DATA.md        MIT for the tools; the data is for use in this hackathon only
regulations/
  index.json                    every regulation you may cite: a base and (for most) a latest version, as download links with checksums
cases/
  dev-rear-lamp/                the worked example (a vehicle rear lamp)
    spec.md
    exploded_view.png
    expected.json               the answer we expect, so you can see what a good result looks like
subgoal/
  dev/                          the worked example for the sub-goal
    files.json                  the old and the new version of the regulation, as download links
    requirements_old.json       the requirements derived from the old version (input to the sub-goal)
    expected.json               the answer we expect for this example
schemas/                        JSON Schemas of the two submission formats
tools/
  validate.py                   check your file before uploading
  score_local.py                score regulation and page level on the worked example, offline
  fetch_regulations.py          download the regulation PDFs
```

The held-out case (the one that decides the ranking) is **released on Sunday morning**: we add it to this repository. Run `git pull` (or download the repository again): it adds the held-out case's folders under `cases/` and `subgoal/`, and running `tools/fetch_regulations.py` again downloads its PDFs too.

## 1. Get the PDFs

```bash
python tools/fetch_regulations.py             # everything: regulations/pdf/<regulation>/<base|latest>/*.pdf and the sub-goal old.pdf / new.pdf
python tools/fetch_regulations.py --role base # only the base versions
```

Every download is checked against its checksum. The links expire after the hackathon, so download what you need while it runs. `regulations/index.json` also gives the original UNECE address of each regulation.

**Two versions of every regulation.** Each entry in `regulations/index.json` has a `base` version and, for most regulations, a `latest` version (a newer supplement or series).

- **Main goal:** the expected requirements are built on the **base** version. Cite the pages of its file(s).
- **Sub-goal:** the `latest` version is what changed. Compare it with the base version to find which requirements must change.

You may only cite regulations that appear in `regulations/index.json`, and not every regulation in it applies to the product: choosing the right ones is part of the task.

## 2. Main goal: the output format

One JSON file. See `cases/dev-rear-lamp/expected.json` for a full example.

```json
{
  "team_id": "team-07",
  "case_id": "dev-rear-lamp",
  "requirements": [
    {
      "id": "R-001",
      "text": "The rear lamp shall ...",
      "source": { "regulation": "UN R48", "version": "9.3.0", "page": 6, "paragraph": "6.2" },
      "test_case": "optional free text"
    }
  ]
}
```

- `team_id`: your team id, shown on the platform after you register.
- `case_id`: the id of the case you submit for: `dev-rear-lamp` for the worked example; for the held-out case it is the name of its folder under `cases/`.
- `id` must be unique in the file. `text` is not empty and at most 2,000 characters.
- `source.regulation` must be one of the `regulation` values in `regulations/index.json`.
- `source.version` is required: the `version_label` of one of that regulation's versions in `regulations/index.json`. A page only means something
  together with its version, because each version is its own PDF. The expected requirements are built on the **`base`** version, so cite that one:
  a page of the `latest` version is counted as a different page, not as the right one.
- `source.page` is a positive integer and is required: a page of that version's PDF. `source.paragraph` and `test_case` are optional.
- At most 5,000 requirements and 5 MB per file.

**Granularity: write one engineering obligation per requirement, as in the worked example.**

## 3. Sub-goal: the output format

```json
{
  "team_id": "team-07",
  "case_id": "subgoal-dev",
  "changes": [
    {
      "requirement_id": "R-014",
      "change": "modified",
      "new_text": "...",
      "reason": "Paragraph 5.3 now sets ...",
      "source": { "page": 9, "paragraph": "5.3" }
    }
  ]
}
```

- `change` is `modified`, `removed` or `added`.
- For `modified` and `removed`, `requirement_id` must be one of the ids in that case's `requirements_old.json`. For `added` it must be `null`.
- `new_text` is required for `modified` and `added`. `reason` is always required: say what changed in the regulation.

## 4. Check your file before you upload it

```bash
pip install jsonschema          # the only dependency
python tools/validate.py my_submission.json
python tools/validate.py my_changes.json --requirements-old subgoal/dev/requirements_old.json
```

Every problem is printed with its JSON path, for example `$.requirements[3].source.page: Must be at least 1.` Add `--strict` to also list any field that is not part of the format (extra fields are allowed, just ignored). The submission platform runs this same check on every upload, so a file that passes here is accepted there.

You can also score the regulation and page levels of the worked example without uploading anything:

```bash
python tools/score_local.py my_submission.json      # compares with cases/dev-rear-lamp/expected.json
```

The requirement-level score needs a model to compare meanings, so it is computed on the platform.

## 5. Upload and see your score

Register your team on the submission platform at the address and with the invite code shown on the screen, then upload your files for the main goal and the optional sub-goal. Your upload is scored in the background and the result appears on the page. At the moment you can upload **5 times per hour for each challenge**; a file that fails validation does not count.

**Which upload counts.** For the worked example, your best upload is the one shown on the leaderboard. For the **held-out case, your last valid upload counts**, not your best: your final upload before the lock is your result, so do not upload a worse file at the end.

## How it is judged

**Main goal.** The primary measure is **recall and precision** of your requirements against Certivity's golden dataset, which our regulatory experts prepared. We look at three levels:

- *Regulations:* did you cite the right UN Regulations, and only those?
- *Pages:* did you cite the right pages (a page of the right version) of them? **The page number or the paragraph number is enough:** a page counts if either is right, so give the paragraph number too (`source.paragraph`) and a page number that is off by one does not cost you. Paragraph labels are compared by meaning, not by spelling: `Annex 8 2.1`, `Annex 8, 2.1.` and `annex 8 2.1` are the same paragraph (case, spaces, commas and a closing dot do not matter), but `5.4.2.3` is not `5.4.23`.
- *Requirements:* each of your requirements is matched one to one with an expected requirement that states the same engineering obligation (same scope, condition and constraint). Precision is the share of your requirements that match; recall is the share of expected requirements that you found.

The secondary measure is the user interface, judged by the jury.

**Sub-goal.** The **recall** of the requirements you flagged for change, and the **accuracy** of the change you state for each one.

Certivity pre-selects the finalist teams, who pitch on Sunday from 15:00.

## Licence

The tools and schemas are MIT-licensed (`LICENSE`). The data in this package is for use in this hackathon only (`LICENSE-DATA.md`).
