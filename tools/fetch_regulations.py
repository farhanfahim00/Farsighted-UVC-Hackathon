#!/usr/bin/env python3
"""Download the PDFs this package points to: every file of every version in regulations/index.json,
and the old/new versions listed in subgoal/*/files.json.

    python tools/fetch_regulations.py                       # everything: regulations/pdf/ and subgoal/*/
    python tools/fetch_regulations.py --only "UN R48"       # only that regulation
    python tools/fetch_regulations.py --role base           # only the base versions (or: latest)
    python tools/fetch_regulations.py --out /some/folder    # where the regulation PDFs go

Each regulation has a `base` version (the one the main goal is built on: cite the pages of ITS files)
and, for most regulations, a `latest` version (the newer one, for the optional sub-goal). Files are
saved as <out>/<regulation>/<role>/<file>.

These are the exact PDF files whose page numbers the scoring uses: cite a page by the page of
THIS file, not of a copy you found elsewhere. Every download is checked against the sha256 in the
index, so a wrong or truncated file is reported, not silently kept.

The links are temporary: they stop working after the event. Download what you need while it runs.
Standard library only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import unquote, urlparse


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _filename(entry: dict) -> str:
    name = Path(unquote(urlparse(entry["pdf_url"]).path)).name
    if not name.lower().endswith(".pdf") or name in ("", ".", ".."):
        raise ValueError(f"{entry['regulation']}: cannot derive a file name from {entry['pdf_url']}")
    return name


def fetch_one(entry: dict, out_dir: Path, *, name: str | None = None, retries: int = 3, timeout: int = 60) -> str:
    """'ok', 'cached' or raises RuntimeError. The file is only moved into place once verified.
    `name` overrides the file name derived from the URL (sub-goal files are called old.pdf / new.pdf)."""
    target = out_dir / (name or _filename(entry))
    expected = entry["sha256"].lower()
    if target.exists() and _sha256(target) == expected:
        return "cached"
    out_dir.mkdir(parents=True, exist_ok=True)
    part = target.with_suffix(target.suffix + ".part")
    last = "unknown error"
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(entry["pdf_url"], timeout=timeout) as response, part.open("wb") as handle:
                while chunk := response.read(1 << 20):
                    handle.write(chunk)
            if _sha256(part) != expected:
                part.unlink(missing_ok=True)
                raise RuntimeError(
                    f"{entry['regulation']}: downloaded file does not match the expected checksum; "
                    "do not use it (the link may be serving the wrong file)"
                )
            part.replace(target)
            return "ok"
        except RuntimeError:
            raise  # a checksum mismatch will not fix itself on retry
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = getattr(exc, "reason", None) or str(exc)
            part.unlink(missing_ok=True)
            if attempt < retries:
                time.sleep(attempt)
    raise RuntimeError(f"{entry['regulation']}: download failed after {retries} attempts ({last})")


def _default_index() -> Path | None:
    root = Path(__file__).resolve().parent.parent
    for candidate in (root / "regulations" / "index.json", Path("regulations/index.json")):
        if candidate.is_file():
            return candidate
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Download the regulation PDFs from regulations/index.json.")
    parser.add_argument("--index", help="path to regulations/index.json (default: the one in this package)")
    parser.add_argument("--out", help="folder for the PDFs (default: regulations/pdf next to the index)")
    parser.add_argument("--only", action="append", default=[], help="fetch only this regulation (repeatable)")
    parser.add_argument("--role", choices=["base", "latest"], help="fetch only the base or only the latest versions")
    args = parser.parse_args(argv)

    index_path = Path(args.index) if args.index else _default_index()
    if index_path is None:
        print("error: regulations/index.json not found; pass --index", file=sys.stderr)
        return 2
    entries = json.loads(index_path.read_text(encoding="utf-8"))
    if args.only:
        wanted = set(args.only)
        unknown = wanted - {e["regulation"] for e in entries}
        if unknown:
            print(f"error: not in the index: {', '.join(sorted(unknown))}", file=sys.stderr)
            return 2
        entries = [e for e in entries if e["regulation"] in wanted]
    out_dir = Path(args.out) if args.out else index_path.parent / "pdf"

    jobs = []
    for entry in entries:
        slug = "_".join(entry["regulation"].replace("/", " ").split())
        for version in entry.get("versions", []):
            if args.role and version.get("role") != args.role:
                continue
            for file in version.get("files", []):
                label = f"{entry['regulation']} {version.get('role')}/{file.get('name', '?')}"
                jobs.append((label, {"regulation": label, **file}, out_dir / slug / str(version.get("role")), file.get("name")))
    if not args.only and not args.role:  # the sub-goal's old/new versions sit next to their files.json
        root = index_path.parent.parent
        for listing in sorted(root.glob("subgoal/*/files.json")):
            for item in json.loads(listing.read_text(encoding="utf-8")):
                label = f"sub-goal {listing.parent.name}/{item.get('name', '?')}"
                jobs.append((label, {"regulation": label, **item}, listing.parent, item.get("name")))

    failures = 0
    for number, (label, entry, target_dir, name) in enumerate(jobs, start=1):
        try:
            state = fetch_one(entry, target_dir, name=name)
            print(f"[{number}/{len(jobs)}] {label}: {state}")
        except (RuntimeError, ValueError, KeyError) as exc:
            failures += 1
            print(f"[{number}/{len(jobs)}] FAILED {exc}", file=sys.stderr)
    print(f"\n{len(jobs) - failures} of {len(jobs)} files downloaded or already present")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
