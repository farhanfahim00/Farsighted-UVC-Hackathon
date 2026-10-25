# Farsighted
### AI-powered Automotive Regulatory Engineering
**UVC Industrial Agents Hackathon 2026 | Team 07**

Farsighted helps automotive engineers turn complex regulatory documents into actionable, traceable engineering requirements.

Instead of manually reviewing hundreds of pages of UNECE regulations, engineers can use Farsighted to identify relevant obligations, inspect supporting evidence, and review the proposed requirements before incorporating them into their development workflow.

## Challenges

### Main Challenge — Regulatory Requirements Extraction

**Held-out case: Front Bumper**

The system analyzes automotive regulations against a specific component description and generates structured engineering requirements.

Core capabilities:
- Identify applicable regulatory obligations.
- Extract atomic, individually testable requirements.
- Associate every requirement with its regulation, version, page, and paragraph.
- Preserve source evidence for verification.
- Filter requirements according to component applicability.
- Generate machine-readable JSON for engineering review.

The team's final selected main-challenge artifact contains 317 requirements.

### Optional Challenge — Regulation Change Detection

**Subgoal V48 — 153 Changes**

The optional challenge identifies changes between regulatory versions and produces structured outputs describing the affected requirements.

The goal is to help engineering teams understand what has changed and where further compliance review may be necessary.

## Engineering Review Demo

The Streamlit application provides an interactive interface for reviewing extracted requirements.

Features include:
- Guided engineering review workflow
- Regulation and source inspection
- Requirement-level evidence and explanations
- Keep/discard review decisions
- Visual component context
- Structured results for downstream use

Run the latest demo from the repository root:

    python -m streamlit run demo/app_v2.py

The demo implementation and supporting UI files are located in:

    demo/app_v2.py
    demo/ui/
    demo/examples/

## Approach

The solution combines regulatory document processing, contextual applicability assessment, structured requirement extraction, and evidence-based engineering review.

Typical workflow:

1. Ingest regulation documents and component context.
2. Identify potentially applicable regulations and provisions.
3. Extract technical obligations into individual requirements.
4. Evaluate relevance to the component.
5. Attach verifiable source references.
6. Produce structured JSON.
7. Review results through the interactive demo.

## Submission Format

The main challenge uses structured JSON containing:
- Team and case identifiers
- Individual requirement IDs
- Requirement text
- Regulation, version, page and paragraph
- Evidence references
- Applicability reasoning

The optional challenge produces structured regulation-change records.

## Repository Structure

- `src/` — Regulatory processing and extraction code
- `demo/` — Streamlit engineering review application
- `demo/ui/` — Demo interface helpers
- `demo/examples/` — Demo input and output examples
- `tools/` — Supporting utilities and validation scripts

## Evaluation and Limitations

The hackathon evaluates requirement extraction against a held-out reference case.

The project combines automated extraction with iterative requirement review and refinement. The curated submission artifacts should not be assumed to be reproducible from a single pipeline run without the corresponding review and post-processing steps.

Regulatory requirements remain subject to engineering verification and interpretation. Farsighted is a decision-support prototype, not a substitute for formal homologation or legal approval.

## Team

**Farsighted — Team 07**

Built for the UVC Industrial Agents Hackathon, Munich, 24–25 October 2026.
