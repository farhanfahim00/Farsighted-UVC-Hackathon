# Short technical explanation - Farsighted

Farsighted is a regulatory-engineering assistant built for the Certivity Industrial Agents challenge. It turns a component specification and exploded view into traceable, atomic engineering requirements under applicable UN vehicle regulations.

The main pipeline extracts a product/function profile, indexes regulation PDFs, screens regulation and section applicability, generates individual requirement candidates, reviews relevance and evidence, and exports schema-validated JSON. Each requirement retains its regulatory identifier, version and source page, with paragraph citations where available. A separate version-change pipeline compares old and new regulatory provisions with existing requirements and proposes added, modified and removed obligations, including the reason for each change.

The browser interface is a local, read-only viewer for actual pipeline submissions. It exposes product evidence, searchable requirements, citations and change rationales; it does not run inference or modify the results. Final applicability and compliance judgments remain with qualified reviewers.

## Implementation detail (if the form allows more text)

- Python for document/indexing, structured processing and validation.
- Claude API for constrained screening, extraction and obligation comparison.
- Cached stage outputs so interrupted workflows can resume without repeating successful model calls.
- Schema verification before exporting organizer-compatible JSON.
- Local browser UI reads real JSON files via a file picker; it sends no results to a backend.

## Submit only after verifying

- Repository URL and default branch contain both main and sub-goal code.
- The held-out case uses the held-out IDs, not development IDs.
- Do not claim official held-out scores or accuracy without evaluation.
