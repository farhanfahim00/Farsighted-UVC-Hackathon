#!/usr/bin/env python3
"""Check a hackathon submission file before you upload it.

    python tools/validate.py my_submission.json
    python tools/validate.py my_submission.json --index regulations/index.json
    python tools/validate.py my_changes.json --requirements-old subgoal/dev/requirements_old.json

Every problem is printed with its JSON path. The exit code is 0 when the file is valid, 1 when
it has problems, and 2 when the file could not be read at all.

The submission platform runs exactly this code on every upload, so a file that passes here
passes there. Needs Python 3.12+ and `pip install jsonschema`; nothing else.

Used as a module (the platform does):

    from hackathon_package.validate import validate
    problems = validate(data, index=index, requirements_old=requirements_old)

This file is deliberately self-contained so it can be copied anywhere and run.
"""

from __future__ import annotations

import argparse
import difflib
import json
import re
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

MAX_BYTES = 5 * 1024 * 1024
MAIN = "main"
SUBGOAL = "subgoal"
_SCHEMA_FILES = {MAIN: "submission_main.schema.json", SUBGOAL: "submission_subgoal.schema.json"}


@dataclass(frozen=True)
class Problem:
    path: str  # JSON path, "$" for the whole document
    message: str

    def __str__(self) -> str:
        return f"{self.path}: {self.message}"


# --- schemas ---------------------------------------------------------------------------------


def _schema_dir() -> Path:
    here = Path(__file__).resolve().parent
    # next to this file in the source tree, or one level up in the release (tools/ and schemas/)
    for candidate in (here / "schemas", here.parent / "schemas"):
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(f"schemas directory not found next to {here}")


def _forbid_extra_fields(node):
    """The schema with `additionalProperties: false` on every object that lists its properties."""
    if isinstance(node, dict):
        out = {k: _forbid_extra_fields(v) for k, v in node.items()}
        if out.get("type") == "object" and "properties" in out:
            out["additionalProperties"] = False
        return out
    if isinstance(node, list):
        return [_forbid_extra_fields(v) for v in node]
    return node


@lru_cache(maxsize=None)
def _validator(kind: str, strict: bool = False):
    from jsonschema import Draft202012Validator

    schema = json.loads((_schema_dir() / _SCHEMA_FILES[kind]).read_text(encoding="utf-8"))
    if strict:
        schema = _forbid_extra_fields(schema)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def kind_of(data: Any) -> str | None:
    """`main` for a requirements file, `subgoal` for a changes file, None if neither."""
    if isinstance(data, dict):
        if "requirements" in data:
            return MAIN
        if "changes" in data:
            return SUBGOAL
    return None


def _json_path(parts) -> str:
    return "$" + "".join(f"[{p}]" if isinstance(p, int) else f".{p}" for p in parts)


def _sort_key(problem: Problem):
    # natural order along the document: $.requirements[2] before $.requirements[10]
    return [(0, int(t)) if t.isdigit() else (1, t) for t in re.findall(r"\d+|[^\d]+", problem.path)]


_ARTICLE = {"string": "a string", "integer": "an integer", "object": "an object",
            "array": "a list", "null": "null", "number": "a number", "boolean": "true or false"}


def _friendly(error) -> list[Problem]:
    """One readable Problem per schema error. 'Required' errors point at the missing field."""
    base = list(error.absolute_path)
    validator, wanted = error.validator, error.validator_value
    if validator == "required":
        return [Problem(_json_path([*base, k]), "Required.") for k in wanted if k not in error.instance]
    path = _json_path(base)
    if validator == "additionalProperties":
        extras = sorted(set(error.instance) - set(error.schema.get("properties", {})))
        return [Problem(_json_path([*base, k]), "Not part of the format.") for k in extras]
    if validator == "type":
        types = wanted if isinstance(wanted, list) else [wanted]
        return [Problem(path, "Must be " + " or ".join(_ARTICLE.get(t, t) for t in types) + ".")]
    if validator == "pattern" or validator == "minLength":
        return [Problem(path, "Must not be empty.")]
    if validator == "maxLength":
        return [Problem(path, f"At most {wanted} characters (found {len(error.instance)}).")]
    if validator == "maxItems":
        return [Problem(path, f"At most {wanted} items (found {len(error.instance)}).")]
    if validator == "minimum":
        return [Problem(path, f"Must be at least {wanted}.")]
    if validator == "enum":
        return [Problem(path, "Must be one of: " + ", ".join(map(str, wanted)) + ".")]
    return [Problem(path, error.message)]


def _schema_problems(data: Any, kind: str, strict: bool = False) -> list[Problem]:
    out: list[Problem] = []
    for error in _validator(kind, strict).iter_errors(data):
        out.extend(_friendly(error))
    return out


# --- rules JSON Schema cannot express ----------------------------------------------------------


def _suggest(value: str, choices: set[str]) -> str:
    close = difflib.get_close_matches(value, sorted(choices), n=1, cutoff=0.6)
    return f" Did you mean {close[0]!r}?" if close else ""


def _main_rules(data: dict, index: list[dict] | None) -> list[Problem]:
    problems: list[Problem] = []
    requirements = data.get("requirements")
    if not isinstance(requirements, list):
        return problems
    first_seen: dict[str, int] = {}
    allowed = {e["regulation"] for e in index} if index is not None else None
    # version_label -> role, per regulation, for the index entries that list versions (older flat ones are skipped)
    versions = {
        e["regulation"]: {v["version_label"]: v.get("role", "") for v in e.get("versions", []) if isinstance(v, dict) and v.get("version_label")}
        for e in (index or [])
    }
    for i, req in enumerate(requirements):
        if not isinstance(req, dict):
            continue
        rid = req.get("id")
        if isinstance(rid, str) and rid:
            if rid in first_seen:
                problems.append(Problem(
                    f"$.requirements[{i}].id",
                    f"Duplicate id {rid!r} (first used at $.requirements[{first_seen[rid]}].id).",
                ))
            else:
                first_seen[rid] = i
        source = req.get("source")
        regulation = source.get("regulation") if isinstance(source, dict) else None
        if allowed is not None and isinstance(regulation, str) and regulation and regulation not in allowed:
            problems.append(Problem(
                f"$.requirements[{i}].source.regulation",
                f"{regulation!r} is not in regulations/index.json.{_suggest(regulation, allowed)}",
            ))
        version = source.get("version") if isinstance(source, dict) else None
        known = versions.get(regulation) if isinstance(regulation, str) else None
        if known and isinstance(version, str) and version and version not in known:
            listing = ", ".join(f"{label!r} ({role})" if role else repr(label) for label, role in known.items())
            problems.append(Problem(
                f"$.requirements[{i}].source.version",
                f"{version!r} is not a version of {regulation} in regulations/index.json. Its versions: {listing}.{_suggest(version, set(known))}",
            ))
    return problems


def _subgoal_rules(data: dict, requirements_old: list[dict] | None) -> list[Problem]:
    problems: list[Problem] = []
    changes = data.get("changes")
    if not isinstance(changes, list) or requirements_old is None:
        return problems
    known = {r["id"] for r in requirements_old if isinstance(r, dict) and isinstance(r.get("id"), str)}
    for i, change in enumerate(changes):
        if not isinstance(change, dict) or change.get("change") not in ("modified", "removed"):
            continue
        rid = change.get("requirement_id")
        if isinstance(rid, str) and rid and rid not in known:
            problems.append(Problem(
                f"$.changes[{i}].requirement_id",
                f"{rid!r} is not in requirements_old.json.{_suggest(rid, known)}",
            ))
    return problems


def validate(
    data: Any,
    kind: str | None = None,
    *,
    index: list[dict] | None = None,
    requirements_old: list[dict] | None = None,
    strict: bool = False,
) -> list[Problem]:
    """All problems in `data`, in document order. `index` (regulations/index.json) turns on the
    check that every cited regulation exists; `requirements_old` turns on the check that a
    modified/removed change names a real requirement. Without them those two checks are skipped.

    Extra fields are normally allowed (a team may add its own notes). With `strict=True` every field that is not
    part of the format is a problem: the release builder uses this for the expected files, which must contain
    nothing but what a participant's submission contains."""
    if not isinstance(data, dict):
        return [Problem("$", "The top level must be a JSON object.")]
    kind = kind or kind_of(data)
    if kind is None:
        return [Problem("$", "Expected a main-goal file (with 'requirements') or a sub-goal file (with 'changes').")]
    problems = _schema_problems(data, kind, strict)
    problems += _main_rules(data, index) if kind == MAIN else _subgoal_rules(data, requirements_old)
    unique = {(p.path, p.message): p for p in problems}  # one message per path and wording
    return sorted(unique.values(), key=_sort_key)


# --- loading helpers -------------------------------------------------------------------------


def load_index(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(e, dict) and isinstance(e.get("regulation"), str) for e in data):
        raise ValueError(f"{path}: expected a list of objects that each have a 'regulation' string")
    return data


def load_requirements_old(path: str | Path) -> list[dict]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("requirements"), list):
        data = data["requirements"]
    if not isinstance(data, list):
        raise ValueError(f"{path}: expected a list of requirements (each with an 'id')")
    return data


def read_submission(path: str | Path) -> Any:
    """Read and parse a submission file. Raises ValueError with a readable message."""
    path = Path(path)
    try:
        size = path.stat().st_size
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc.strerror}") from None
    if size > MAX_BYTES:
        raise ValueError(f"{path.name} is {size / 1024 / 1024:.1f} MB; the limit is {MAX_BYTES // (1024 * 1024)} MB")
    try:
        return json.loads(path.read_bytes().decode("utf-8"))
    except UnicodeDecodeError:
        raise ValueError(f"{path.name} is not UTF-8 text") from None
    except (json.JSONDecodeError, RecursionError) as exc:
        detail = f"{exc.msg} at line {exc.lineno}, column {exc.colno}" if isinstance(exc, json.JSONDecodeError) else "nested too deeply"
        raise ValueError(f"{path.name} is not valid JSON: {detail}") from None


# --- command line ----------------------------------------------------------------------------


def _default(*relative: str) -> Path | None:
    """regulations/index.json etc., found next to tools/ in the release layout."""
    root = Path(__file__).resolve().parent.parent
    for candidate in (root.joinpath(*relative), Path.cwd().joinpath(*relative)):
        if candidate.is_file():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a hackathon submission file.")
    parser.add_argument("submission", help="your submission JSON file")
    parser.add_argument("--index", help="regulations/index.json (default: the one in this package)")
    parser.add_argument("--requirements-old", help="requirements_old.json of the sub-goal case, to check requirement_id")
    parser.add_argument("--kind", choices=[MAIN, SUBGOAL], help="override the automatic main/sub-goal detection")
    parser.add_argument("--strict", action="store_true", help="also report fields that are not part of the format")
    args = parser.parse_args(argv)

    try:
        data = read_submission(args.submission)
        kind = args.kind or kind_of(data)
        index_path = Path(args.index) if args.index else _default("regulations", "index.json")
        index = load_index(index_path) if index_path else None
        old_path = Path(args.requirements_old) if args.requirements_old else None
        requirements_old = load_requirements_old(old_path) if old_path else None
    except (ValueError, OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    notes = []
    if kind == MAIN and index is None:
        notes.append("regulation names were not checked (no regulations/index.json found; pass --index)")
    if kind == SUBGOAL and requirements_old is None:
        notes.append("requirement ids were not checked (pass --requirements-old)")

    problems = validate(data, kind, index=index, requirements_old=requirements_old, strict=args.strict)
    for problem in problems:
        print(f"  {problem}")
    items = data.get("requirements" if kind == MAIN else "changes") if isinstance(data, dict) else None
    if problems:
        print(f"\n{len(problems)} problem(s) found. Fix them and run this again.")
    else:
        print(f"OK: {Path(args.submission).name} is valid" + (f" ({len(items)} {'requirements' if kind == MAIN else 'changes'})." if isinstance(items, list) else "."))
    for note in notes:
        print(f"note: {note}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
