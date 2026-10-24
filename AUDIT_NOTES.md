# Farsighted Certivity code audit - 24 October 2026

This is a **reviewed source snapshot**, not the entire executable repository. The ZIP contains
`src/`, `tools/`, `tests/`, `requirements.txt`, `README.md`, and this note, but **not**
`cases/`, `regulations/`, `runs/`, `config/`, schemas, PDFs, .env, Git history, or cached model responses.

## What was actually changed

1. `src/verification.py`: table-row detection now requires an actual pulse/row reference for
   short numeric labels. `Table 3b` no longer falsely matches *pulse 3b*. Missing
   classifications can be flagged across blank-key continuation rows. Numeric constraint
   hints exclude standard/paragraph IDs. **This is a heuristic checklist, not legal validation.**
2. `src/packets.py`: recursive descendant inclusion is independent of index order;
   truncated output splits prioritize complete target paragraphs. A new compact context
   fallback tries to unblock single-target packets by omitting *otherwise-unlinked*
   page lines, while retaining target, linked parent, cross-reference, definition, table,
   and footnote evidence. It records `context_reduced` and omitted line counts.
   Oversized packets that cannot safely fit remain blocked. Normal packet IDs/wire
   inputs are intentionally unchanged for compatibility with older response caches.
3. `src/guard.py`: table pseudo-paragraph citations resolve to the closest indexed
   non-table governing clause even if it is a context paragraph. Missing governing
   clauses cause quarantine instead of emitting a synthetic label.
4. `src/extraction.py`: a truncated packet whose children all correctly return no
   applicable duties retains that exclusion result; duplicate split-child rows are merged.
5. `src/llm.py`: response cache keys depend on the *exact model request*, including model,
   schema, prompt and input, not on unrelated source changes. Optional legacy fingerprints
   allow migration of old cache entries only when the request payload is byte-equivalent.
6. `src/pipeline.py`: extracted packet checkpoints are genuinely replayed when completed
   and their packet/product/index/model/prompt/schema settings match; incomplete checkpoints
   are NOT treated as success. Candidate reuse checks content identity for newly created
   runs; old runs lacking this hash are marked as unverified. Produces a review-flag summary,
   unique-target counts, and compact-context counts.
7. `tests/test_offline_hardening.py`: 10 new self-contained, zero-API-cost regression tests.
   `tests/test_runtime.py` updates one cache expectation to match exact-request cache keys.

## Verification performed here

- Baseline uploaded source: **22** self-contained tests passed.
- Reviewed source: **32** self-contained tests passed, with no paid inference.
- `python -m compileall -q src tools tests` also succeeds.
- Full suite: 32 passed, 9 failures and 18 setup errors because the uploaded ZIP omitted
  the development spec, PDF evidence indices, runtime config and schemas. These failures
  cannot be interpreted as real-source regressions or successful full integration.
- No organizer PDF was opened here, no real Claude API request was made, no official
  development submission was evaluated, and no new semantic F2 was established.

Offline test command from repository root (requires Python + pytest + dependencies):

    python -m pytest -q tests/test_offline_hardening.py tests/test_source_verification.py tests/test_budget_preflight.py tests/test_candidate_audit.py src/tests

Once original cases, indices, config and schemas are present, also run:

    python -m pytest -q

## Existing paid response caches

Old source-wide runtime fingerprint for the uploaded snapshot:

    b599360a663641fa5914786c395b9cf3fdea4fe47d68226aa5b3a857e3929313

When invoking `src.pipeline` with old caches in `runs/runtime-cache`, add:

    --legacy-cache-fingerprint b599360a663641fa5914786c395b9cf3fdea4fe47d68226aa5b3a857e3929313

If the old cached responses came from a different code revision, use the
`runtime_fingerprint` recorded in that prior run's `run-status.json` instead.
You can repeat the flag for more than one prior fingerprint. **No match is guaranteed**;
request payloads must be identical. With `--reuse-candidates`, the prior candidate
run's fingerprint is also offered automatically for legacy cache lookup. Previous
extraction checkpoints without a strict key are not replayed automatically.

Default behavior remains offline/cache-only until `--online` is explicitly supplied.
Do not increase `--max-attempts` or `--max-usd` without considering API costs.

## Remaining risks and correct next experiment

- The reported 42 blocked packets were NOT replayed with the real corpus here;
  the new compact fallback is tested on synthetic evidence only.
- The full-case semantic score, over/under-splitting, table-cell accuracy and bumper
  transfer are still unmeasured.
- The broad candidate safety net and review queue were intentionally left unchanged:
  removing either without semantic evaluation could destroy recall.
- Do NOT use `obligation-checklist.json` or `review-flags-summary.json` as proof that
  a requirement is correct; they help human audit, not regulatory certification.
- Use the first complete primary-case run and official development score to choose
  further changes. Preserve a validated previous submission before any new upload.

## Safest Windows replacement (overlay, not delete)

A replacement ZIP with root `src/`, `tools/` and `tests/` paths can be expanded over
`C:\UVC2026\farsighted`. Existing `config/`, `runs/`, `cases/`, `regulations/`, `.git/`
and other files remain in place because Expand-Archive overwrites matching paths,
not entire directories. **Do not delete the original repository and extract only this ZIP.**

Use the targeted patch ZIP when someone has edited unrelated files since the snapshot.
Back up source folders before overlaying, and inspect `git status --short` after.

Example (first put the downloaded patch ZIP in C:\UVC2026):

    Set-Location C:\UVC2026\farsighted
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $items = @('src','tools','tests','README.md','requirements.txt') | Where-Object { Test-Path $_ }
    Compress-Archive -Path $items -DestinationPath "C:\UVC2026\farsighted-source-backup-$stamp.zip"
    Expand-Archive -LiteralPath 'C:\UVC2026\farsighted-targeted-update.zip' -DestinationPath 'C:\UVC2026\farsighted' -Force
    git status --short

Unpacking into the existing repository is different from creating an entirely new
functional clone. The archive never contained the external PDFs, config or caches.
