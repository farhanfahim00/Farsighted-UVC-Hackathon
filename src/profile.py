#!/usr/bin/env python3
"""Stage 2: product profile. Reads a case folder (every text file and every image) and asks PROFILE_MODEL for a
structured, product-agnostic description of the product, with a status and evidence for every fact.

    python src/profile.py cases/dev-rear-lamp [--force]     # -> build/cases/<case_id>/profile.json

The model gets, in one request: each text file as raw text (unchanged) and split by markdown headings, and each image.
It never sees expected.json: any file whose name contains "expected" is skipped, so a case folder can be passed as is.
Python then validates the answer (statuses, evidence). A profile may be empty or partial when the input is unusable.
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import sys
from pathlib import Path

import pymupdf

import config
from llm import LLMFailure, call_json, object_schema, usage_summary

TEXT_EXT = {".md", ".txt", ".rst", ".csv", ".tsv", ".yaml", ".yml", ".json"}
IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".gif": "image/gif",
               ".webp": "image/webp"}
MAX_IMAGE_BYTES = 4_500_000        # the API limit is 5 MB per image
MAX_IMAGE_SIDE = 7_900             # and 8000 px per side

STATUSES = ["stated", "explicitly_absent", "unknown", "representative", "conflicting", "observed_uncertain"]
KINDS = ["component_device", "system", "whole_vehicle", "unknown"]   # "unknown" only for unusable input
NEEDS_EVIDENCE = {"stated", "explicitly_absent"}
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

S = {"type": "string"}
PROFILE_SCHEMA = object_schema({
    "product_name": S,
    "product_kind": {"type": "string", "enum": KINDS},
    "vehicle_categories": {"type": "array", "items": S},
    "parts": {"type": "array", "items": object_schema({"name": S, "function": S, "evidence": S})},
    "facts": {"type": "array", "items": object_schema({
        "feature": S, "status": {"type": "string", "enum": STATUSES}, "evidence": S, "source": S})},
    "drawing_annotations": {"type": "array", "items": object_schema({"part": S, "note": S})},
})


# ---------------------------------------------------------------------------------------------------------------------
# Reading the case folder
# ---------------------------------------------------------------------------------------------------------------------

def case_files(case_dir: Path) -> list[Path]:
    """Every readable file of the case, sorted. expected*.json (the answer) is never included."""
    out = []
    for p in sorted(case_dir.rglob("*")):
        rel = p.relative_to(case_dir)
        if not p.is_file() or any(part.startswith(".") for part in rel.parts) or "expected" in p.name.lower():
            continue
        if p.suffix.lower() in TEXT_EXT | set(IMAGE_TYPES):
            out.append(p)
    return out


def split_sections(text: str) -> list[tuple[str, str]]:
    """[(heading path, body)] for a markdown text; text before the first heading has the path ''."""
    sections: list[tuple[str, list[str]]] = [("", [])]
    stack: list[tuple[int, str]] = []
    for line in text.splitlines():
        m = HEADING_RE.match(line)
        if m:
            level, title = len(m[1]), m[2]
            stack = [s for s in stack if s[0] < level] + [(level, title)]
            sections.append((" > ".join(t for _, t in stack), []))
        else:
            sections[-1][1].append(line)
    return [(path, "\n".join(body).strip()) for path, body in sections if path or "".join(body).strip()]


def image_block(path: Path) -> dict:
    """A base64 image block; images over the API limits are shrunk (jpeg), others are sent untouched."""
    data, media = path.read_bytes(), IMAGE_TYPES[path.suffix.lower()]
    pix = pymupdf.Pixmap(str(path))
    if len(data) > MAX_IMAGE_BYTES or max(pix.width, pix.height) > MAX_IMAGE_SIDE:
        while len(data) > MAX_IMAGE_BYTES or max(pix.width, pix.height) > MAX_IMAGE_SIDE:
            pix.shrink(1)
            data = pix.tobytes("jpeg")
        media = "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media,
                                        "data": base64.b64encode(data).decode("ascii")}}


def build_content(case_dir: Path, files: list[Path]) -> list[dict]:
    """The request content: text files (raw, then split by headings), then each image with its file name."""
    content: list[dict] = []
    for p in (f for f in files if f.suffix.lower() in TEXT_EXT):
        rel, raw = p.relative_to(case_dir).as_posix(), p.read_text(encoding="utf-8", errors="replace")
        content.append({"type": "text", "text": f"=== FILE {rel} (raw text, unchanged) ===\n{raw}"})
        parts = split_sections(raw)
        body = "\n\n".join(f"--- section: {path or '(before the first heading)'} ---\n{text}" for path, text in parts)
        content.append({"type": "text", "text": f"=== FILE {rel} split by markdown headings ===\n"
                                                f"{body if parts else '(empty)'}"})
    for p in (f for f in files if f.suffix.lower() in IMAGE_TYPES):
        content.append({"type": "text", "text": f"=== IMAGE {p.relative_to(case_dir).as_posix()} ==="})
        content.append(image_block(p))
    content.append({"type": "text", "text": "Build the product profile now."})
    return content


# ---------------------------------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------------------------------

def empty_profile(name: str) -> dict:
    return {"product_name": name, "product_kind": "unknown", "vehicle_categories": [], "parts": [], "facts": [],
            "drawing_annotations": []}


def validate_profile(raw: dict) -> tuple[dict, list[str]]:
    """Clean the model's answer. Nothing is invented: a fact that breaks a rule is downgraded to 'unknown' (never to
    'explicitly_absent') or dropped, and every change is returned as a warning."""
    warnings: list[str] = []
    clean = lambda v: " ".join(str(v or "").split())
    profile = empty_profile(clean(raw.get("product_name")))
    kind = clean(raw.get("product_kind"))
    if kind in KINDS:
        profile["product_kind"] = kind
    else:
        warnings.append(f"invalid product_kind {kind!r}; set to 'unknown'")
    profile["vehicle_categories"] = [c for c in (clean(c) for c in raw.get("vehicle_categories", [])) if c]
    for p in raw.get("parts", []):
        if clean(p.get("name")):
            profile["parts"].append({k: clean(p.get(k)) for k in ("name", "function", "evidence")})
        else:
            warnings.append("dropped a part without a name")
    seen: set[tuple] = set()
    for f in raw.get("facts", []):
        fact = {k: clean(f.get(k)) for k in ("feature", "status", "evidence", "source")}
        if not fact["feature"]:
            warnings.append("dropped a fact without a feature name")
            continue
        if fact["status"] not in STATUSES:
            warnings.append(f"dropped fact {fact['feature']!r}: invalid status {fact['status']!r}")
            continue
        if fact["status"] in NEEDS_EVIDENCE and not fact["evidence"]:
            warnings.append(f"fact {fact['feature']!r} was {fact['status']} without evidence; set to 'unknown'")
            fact["status"] = "unknown"
        if fact["status"] in NEEDS_EVIDENCE and not fact["source"]:
            warnings.append(f"fact {fact['feature']!r} has no source")
        key = (fact["feature"].lower(), fact["status"], fact["evidence"].lower())
        if key in seen:
            warnings.append(f"dropped duplicate fact {fact['feature']!r}")
            continue
        seen.add(key)
        profile["facts"].append(fact)
    profile["drawing_annotations"] = [{"part": clean(a.get("part")), "note": clean(a.get("note"))}
                                      for a in raw.get("drawing_annotations", []) if clean(a.get("note"))]
    names = [f["feature"].lower() for f in profile["facts"]]
    for dup in sorted({n for n in names if names.count(n) > 1}):
        warnings.append(f"feature name {dup!r} appears with several statuses or evidence (kept, not merged)")
    if not profile["facts"]:
        warnings.append("the profile has no facts")
    return profile, warnings


# ---------------------------------------------------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------------------------------------------------

def build_profile(case_dir: Path, out_dir: Path | None = None) -> dict:
    """Build and write build/cases/<case_id>/profile.json for a case folder; returns the profile."""
    case_dir = Path(case_dir)
    case_id = case_dir.name
    out_dir = out_dir or config.CASES_BUILD / case_id
    out_dir.mkdir(parents=True, exist_ok=True)
    files = case_files(case_dir)
    meta = {"case_id": case_id, "model": config.PROFILE_MODEL,
            "input_files": [p.relative_to(case_dir).as_posix() for p in files]}
    if not files:
        profile, warnings, info = empty_profile("(no usable input files)"), ["the case folder has no readable files"], {}
    else:
        raw, info = call_json(config.PROFILE_MODEL, "profile_system.md", build_content(case_dir, files),
                              PROFILE_SCHEMA, label=f"profile:{case_id}", max_tokens=16000)
        profile, warnings = validate_profile(raw)
    # tokens come from the cached response, so this block is the same on every run (a "cached" flag would not be)
    meta.update(validation_warnings=warnings, input_tokens=info.get("input_tokens"),
                output_tokens=info.get("output_tokens"))
    profile["meta"] = meta
    (out_dir / "profile.json").write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return profile


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir", help="a case folder, e.g. cases/dev-rear-lamp")
    ap.add_argument("--out", help="output folder (default: build/cases/<case_id>/)")
    args = ap.parse_args(argv)
    try:
        profile = build_profile(Path(args.case_dir), Path(args.out) if args.out else None)
    except LLMFailure as exc:
        print(f"error: profile failed: {exc}", file=sys.stderr)
        return 1
    statuses = {}
    for f in profile["facts"]:
        statuses[f["status"]] = statuses.get(f["status"], 0) + 1
    print(f"profile: {profile['product_name']!r} ({profile['product_kind']}), {len(profile['parts'])} parts, "
          f"{len(profile['facts'])} facts {statuses}, {len(profile['meta']['validation_warnings'])} warnings")
    print(usage_summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
