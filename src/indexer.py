#!/usr/bin/env python3
"""Deterministic PDF indexer: turns every regulation PDF into an evidence index of numbered paragraphs, page lines,
tables and definitions, with exact citations back to the lines they were built from.

    python src/indexer.py                  # everything in regulations/index.json + every subgoal/*/files.json
    python src/indexer.py --only "UN R150" # one regulation, for quick debugging (writes to build/only/)
    python src/indexer.py --allow-missing  # debugging only: skip missing / mismatching PDFs instead of failing

Outputs (in build/; every field is described in src/SCHEMA.md, copied to build/SCHEMA.md):
    paragraphs.jsonl        one record per paragraph, title entry ("Annex 6 Part 4") and table entry
    pages.jsonl             one record per page with every visible line (running headers and footnotes included)
    tables.jsonl            cells of the tables pymupdf finds on caption / table pages, with risk flags
    definitions.jsonl       the terms of the definitions sections
    regulation_scopes.json  per regulation (base version): title + full text of its scope section
    manifest.json           per file: checksum, page / line / footnote / unassigned-line counts

Integrity: every PDF is checked against the sha256 in regulations/index.json (or subgoal/*/files.json) before it is
read; a missing file or a wrong checksum stops the run with a non-zero exit. Every id is unique, or the run fails.

How the PDFs look (all are rendered by Certivity in the same style):
  * every page has a running header "UN R150, version 1.2.1 | page 7 of 54";
    page 1 also has a short boilerplate block "UN R150 (version 1.2.1)", "Consolidated text ...", "Role in the hackathon: ..."
  * body text uses one dominant font size per document (7 to 10.5 pt) at a fixed left margin;
  * paragraphs start at the margin with "3.3.1.1. Text", sometimes "5.7 Text" (no dot) or the number alone on its line;
    a heading paragraph ("6.14.1. Presence") is followed by a larger vertical gap than a wrapped line;
  * annex / appendix / part headings are bold lines at the margin: "Annex 6 Environmental Testing",
    "Annex 1 - Appendix 2 ...", "Part 4 - Resistance to corrosion". Annexes restart the paragraph numbering;
  * table captions are "Table 3 <bold title>" or a bare "Table 3a"; table cells use a smaller font or are indented;
  * footnotes start with a small superscript digit ("6This does not apply ...") and use a smaller font.

Paragraph starts are chosen globally, not line by line: every number-like line at the margin is a candidate with a
weight (closing dot, capital letter, bold heading: up; previous line ends in "paragraph": down), and per annex context
the chain with the best total score wins, where a believable next number (5.1 -> 5.1.1 -> 5.2) scores and a backward
jump costs. A stray "6. below, ..." at the start of a wrapped line is skipped because it would break the sequence.

No LLM or network calls. Same input -> byte-identical output (files are processed in parallel, results kept in order).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import structure  # noqa: E402  (titles, links, signals, cross references, definitions)
import tables     # noqa: E402  (find_tables() extraction)

ROOT = Path(__file__).resolve().parent.parent
INDEX_JSON = ROOT / "regulations" / "index.json"
PDF_DIR = ROOT / "regulations" / "pdf"
SUBGOAL_ROOT = ROOT / "subgoal"
BUILD = ROOT / "build"
SCHEMA_MD = Path(__file__).resolve().parent / "SCHEMA.md"

# ---------------------------------------------------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------------------------------------------------

HEADER_RE = re.compile(r"^(?P<reg>.+?), version (?P<ver>\S+) \| page (?P<page>\d+) of (?P<pages>\d+)$")
BOILERPLATE_RE = re.compile(
    r"^(.+ \(version [^)]+\)"
    r"|Consolidated text of this version, rendered by Certivity.*"
    r"|the bottom of every page\."
    r"|Role in the hackathon:.*)$"
)
# "3.3.1.1. Text", "5.7 Text", "12.", "A.7.2.1. Text" (appendix numbering) -- first number 1-2 digits, then up to 3
PARA_RE = re.compile(r"^(?P<num>(?:[A-Z]\.)?\d{1,2}(?:\.\d{1,3})*)(?P<dot>\.)?(?:\s+(?P<rest>.*))?$")
# Annex / appendix / part ids: "6", "2A", "A1", "IV", "B"
_ID = r"(?:\d+[A-Za-z]?|[A-Z]\d*|[IVX]+)"
ANNEX_RE = re.compile(rf"^annex\s+(?P<annex>{_ID})\b(?P<tail>.*)$", re.I)
# the appendix id is optional: "Annex 3 - Appendix Procedure for ..." has a single, unnumbered appendix
APPENDIX_RE = re.compile(rf"^\W*appendix\b(?:\s+(?P<appendix>{_ID})\b)?(?P<tail>.*)$", re.I)
PART_RE = re.compile(rf"^\W*part\s+(?P<part>{_ID})(?=$|[\s:.\-–])(?P<tail>.*)$", re.I)
# big bold structural headings in the main body that restart the numbering:
# "Introduction", "I. Statement of technical rationale ...", "A. Phase 1", "II. Text of the Regulation"
SECTION_RE = re.compile(r"^(?:(?P<roman>[IVX]+)\.|(?P<letter>[A-H])\.|(?P<intro>Introduction)\b)(?:\s+|$)")
REG_TEXT_RE = re.compile(r"text of the (?:global technical |UN )?regulation", re.I)
# letter sub-parts inside an annex, in body font: "B. Electrical transmission braking system."
LETTER_PART_RE = re.compile(r"^(?P<part>[A-H])\.\s+[A-Z][^.]{2,80}\.?$")
TABLE_RE = re.compile(r"^Table\s+(?P<tid>[A-Z]{0,2}\d+(?:[-.]\d+)*[a-z]?-?)(?P<rest>.*)$")
# a table-of-contents entry: "5.1. General specifications ........ 12", "Annex 6 Environmental testing .... 42"
# (communication forms also have dotted leaders, but no page number after them)
TOC_LINE_RE = re.compile(r"^(\d+(\.\d+)*\.?|Annex\s+\w+|Appendix\s+\w+|Part\s+\w+)\s.*?(\.{3,}|…{2,})\s*\d{1,3}$", re.I)
# a line ending like this means the next line's leading number is a reference, not a new paragraph:
# "... as defined in paragraph" / "... in paragraphs 2.3.10.," + "2.3.10.1. and 2.3.10.2."
REF_WORD_RE = re.compile(r"(paragraphs?|paras?\.?|§|Nos?\.|Annex(es)?|Appendix|Tables?|Figures?|Parts?)$", re.I)
REF_LIST_RE = re.compile(r"\b(paragraphs?|paras?\.?|§)\s.{0,60}(,|\band|\bor|\bto)$", re.I)
SHALL_RE = re.compile(r"\bshall\b", re.I)


def slug(regulation: str) -> str:
    """Folder name of a regulation, exactly as tools/fetch_regulations.py builds it ("UN R13-H" -> "UN_R13-H")."""
    return "_".join(regulation.replace("/", " ").split())


# ---------------------------------------------------------------------------------------------------------------------
# Page lines
# ---------------------------------------------------------------------------------------------------------------------

@dataclass(eq=False)
class Line:
    text: str
    page: int        # 1-based, = cited page
    x0: float
    size: float      # dominant font size of the line (by characters)
    bold: bool       # first visible span is bold
    any_bold: bool
    footnote: bool   # starts with a small superscript digit
    bbox: tuple = (0.0, 0.0, 0.0, 0.0)
    n: int = 0       # 1-based position among the visible lines of its page (line id "...:p7:l12")
    flag: str = ""   # "running_header" | "boilerplate" | "" (content)
    # set by segment():
    kind: str = ""                   # heading / text / caption / skip / toc (see pages.jsonl)
    footnote_candidate: bool = False
    para: "Para | None" = None       # the paragraph this line was assigned to


def _line_from_spans(spans: list[dict], bbox, page_no: int) -> Line | None:
    text = "".join(s["text"] for s in spans)
    text = " ".join(text.split())
    if not text:
        return None
    visible = [s for s in spans if s["text"].strip()]
    sizes = Counter()
    for s in visible:
        sizes[round(s["size"], 1)] += len(s["text"].strip())
    size = sizes.most_common(1)[0][0]
    first = visible[0]
    footnote = (len(visible) > 1 and first["text"].strip().isdigit() and first["size"] < visible[1]["size"] - 0.5)
    return Line(text=text, page=page_no, x0=bbox[0], size=size, bbox=tuple(round(v, 2) for v in bbox),
                bold="Bold" in first["font"], any_bold=any("Bold" in s["font"] for s in visible), footnote=footnote)


def read_lines(doc: pymupdf.Document) -> tuple[list[Line], str | None, str | None]:
    """Every visible text line of the document in reading order. Running headers and page-1 boilerplate are kept but
    flagged (they are evidence too, and are skipped by the segmentation). Also returns (regulation, version) as printed
    in the running header."""
    lines: list[Line] = []
    reg = ver = None
    for pno, page in enumerate(doc):
        n = 0
        for block in page.get_text("dict")["blocks"]:
            for raw in block.get("lines", []):
                line = _line_from_spans(raw["spans"], raw["bbox"], pno + 1)
                if line is None:
                    continue
                n += 1
                line.n = n
                m = HEADER_RE.match(line.text)
                if m:
                    reg, ver = reg or m["reg"], ver or m["ver"]
                    line.flag = "running_header"
                elif pno == 0 and BOILERPLATE_RE.match(line.text):
                    line.flag = "boilerplate"
                lines.append(line)
    return lines, reg, ver


def body_style(lines: list[Line]) -> tuple[float, float]:
    """(body font size, left margin): the most common size of long lines, and the most common x0 at that size."""
    sizes = Counter(l.size for l in lines if len(l.text) > 40)
    body = sizes.most_common(1)[0][0] if sizes else 10.5
    xs = Counter(round(l.x0) for l in lines if abs(l.size - body) < 0.3)
    margin = xs.most_common(1)[0][0] if xs else 57
    return body, margin


def line_pitch(lines: list[Line], body: float) -> float:
    """Usual distance between the tops of two wrapped body lines; a paragraph or heading break is clearly larger."""
    steps = Counter()
    for a, b in zip(lines, lines[1:]):
        # only after a long line: that one surely wraps, so the next line is the same paragraph
        if a.page == b.page and len(a.text) > 60 and abs(a.size - body) < 0.3 and abs(b.size - body) < 0.3:
            dy = round(b.bbox[1] - a.bbox[1])
            if 0 < dy < 3 * body:
                steps[dy] += 1
    if not steps:
        return body * 1.4
    # the smallest step that is common (paragraph breaks can outnumber wrapped lines in list-like documents)
    top = steps.most_common(1)[0][1]
    return float(min(dy for dy, n in steps.items() if n >= 0.2 * top and n >= min(3, top)))


def toc_pages(lines: list[Line]) -> set[int]:
    """Pages that look like a table of contents: a "Contents" title, or several entries ending in a page number.
    Headings and numbers on those pages must not create paragraphs or switch the annex context."""
    per_page = Counter(l.page for l in lines if TOC_LINE_RE.match(l.text))
    titled = {l.page for l in lines if re.match(r"^(contents|table of contents)$", l.text, re.I)}
    return {p for p, n in per_page.items() if n >= 4} | titled


# ---------------------------------------------------------------------------------------------------------------------
# Paragraph numbering logic
# ---------------------------------------------------------------------------------------------------------------------

def annex_order(annex_id: str | None) -> tuple:
    """Sort key for annex ids: "9" < "9A" < "9E" < "10". Roman / letter-only ids sort after numbers."""
    if annex_id is None:
        return (-1, "")
    m = re.match(r"^(\d+)([A-Z]?)$", annex_id)
    return (int(m[1]), m[2]) if m else (10_000, annex_id)


def fix_annex_id(annex_id: str, tail: str, current: str | None) -> tuple[str, str]:
    """The renderer sometimes splits an annex id: "Annex 1 3 RESISTANCE ..." is Annex 13, "Annex 9 E Fire ..." is
    Annex 9E. Merge the next single-character token when that keeps the annexes in order and the bare id would not."""
    m = re.match(r"^\s+([0-9A-Z])(?=\s|$)(.*)$", tail)
    if m and current is not None:
        merged = annex_id + m[1]
        if annex_order(annex_id) <= annex_order(current) < annex_order(merged):
            return merged, m[2]
    return annex_id, tail


def parse_num(num: str) -> tuple[int, ...]:
    """'3.3.1' -> (3, 3, 1); a letter prefix counts as a number: 'A.7.2' -> (0, 7, 2), 'B.1' -> (1, 1)."""
    return tuple(ord(x) - 65 if x.isalpha() else int(x) for x in num.split("."))


def plausible_next(prev: tuple[int, ...], cand: tuple[int, ...], gap: int = 5) -> bool:
    """Is `cand` a believable next paragraph number after `prev` (same annex/part context)?
    Accepts: a first child (prev + .1 / .0), or the next sibling of prev or of one of its ancestors (small gaps allowed
    for deleted paragraphs)."""
    if cand[:len(prev)] == prev and len(cand) == len(prev) + 1 and cand[-1] in (0, 1, 2):
        return True
    for k in range(1, len(prev) + 1):
        if len(cand) == k and cand[:k - 1] == prev[:k - 1] and prev[k - 1] < cand[k - 1] <= prev[k - 1] + gap:
            return True
        # sibling of an ancestor followed directly by its first child, e.g. 5.9.3 -> 5.10.1
        if len(cand) == k + 1 and cand[:k - 1] == prev[:k - 1] and prev[k - 1] < cand[k - 1] <= prev[k - 1] + gap \
                and cand[k] in (0, 1):
            return True
    return False


def is_restart(cand: tuple[int, ...]) -> bool:
    return all(c <= 1 for c in cand)


def transition_score(prev: tuple[int, ...] | None, cand: tuple[int, ...]) -> float:
    """Score for `cand` directly following `prev` in the chosen paragraph sequence of one context."""
    if prev is None:                      # first paragraph of a context: usually 0., 1. or 1.1.
        return 0.5 if is_restart(cand) or (len(cand) == 1 and cand[0] <= 2) else -1.0
    if plausible_next(prev, cand):
        return 1.5
    common = next((k for k, (a, b) in enumerate(zip(prev, cand)) if a != b), min(len(prev), len(cand)))
    if cand == prev or (len(cand) >= 2 and common >= min(len(prev), len(cand)) - 2 and common >= 1):
        return -0.5                       # duplicate or typo in the regulation itself (e.g. "2.35." printed twice)
    if cand > prev:                       # forward, but a bigger jump than usual
        return -1.0
    if is_restart(cand):                  # numbering restarts (e.g. an annex heading we did not recognise)
        return -2.0
    return -3.0                           # backwards: almost always a reference at the start of a wrapped line


@dataclass
class Candidate:
    idx: int                 # index into `lines`
    ctx: int                 # context id (changes at every annex / appendix / part / section heading)
    num: tuple[int, ...]
    weight: float            # how much the line itself looks like a paragraph start


def select_starts(cands: list[Candidate], window: int = 80) -> set[int]:
    """Pick the paragraph starts: per context, the chain of candidates with the best total score
    (candidate weights + transition scores). One stray number can no longer derail the following paragraphs,
    because skipping it is cheaper than the backward jumps it would cause."""
    chosen: set[int] = set()
    by_ctx: dict[int, list[Candidate]] = {}
    for c in cands:
        by_ctx.setdefault(c.ctx, []).append(c)
    for group in by_ctx.values():
        best: list[float] = []
        back: list[int] = []
        for j, c in enumerate(group):
            score, prev = transition_score(None, c.num), -1
            for i in range(max(0, j - window), j):
                s = best[i] + transition_score(group[i].num, c.num)
                if s > score:
                    score, prev = s, i
            best.append(score + c.weight)
            back.append(prev)
        j = max(range(len(group)), key=lambda k: (best[k], -k))  # best chain end (earliest on ties: deterministic)
        while j >= 0:
            chosen.add(group[j].idx)
            j = back[j]
    return chosen


@dataclass(eq=False)
class Para:
    number: str                    # "3.3.1.2" or "3. Table A6-1" for tables; "" for a title entry
    section: str | None            # main-body section that restarts the numbering ("Introduction", "I", "B")
    annex: str | None
    appendix: str | None
    part: str | None
    page: int
    page_end: int
    ctx: int = 0                   # context id (one per annex / appendix / part / section heading)
    titles: dict = field(default_factory=dict)       # context titles: {"annex": ("6", "Environmental testing"), ...}
    head: str = ""                 # text after the number on the paragraph's first line
    merged_heading: bool = False   # "1. Scope" heading + "1. This Regulation ..." merged into one paragraph
    parts: list[str] = field(default_factory=list)   # text pieces
    lines: list[Line] = field(default_factory=list)  # source lines, in order
    has_table: bool = False
    is_table: bool = False
    owner: "Para | None" = None    # for table entries: the paragraph the table belongs to

    @property
    def kind(self) -> str:
        return "table" if self.is_table else "title" if self.number == "" else "paragraph"

    @property
    def prefix(self) -> str:
        bits = [self.section] if self.section else []
        if self.annex:
            bits.append(f"Annex {self.annex}")
        if self.appendix is not None:
            bits.append(f"Appendix {self.appendix}".strip())
        if self.part and self.annex:
            bits.append(f"Part {self.part}")
        return " ".join(bits)

    @property
    def label(self) -> str:
        return f"{self.prefix} {self.number}".strip()

    def add(self, text: str, page: int, line: Line) -> None:
        if text:
            self.parts.append(text)
        self.lines.append(line)
        line.para = self
        self.page_end = max(self.page_end, page)

    @property
    def text(self) -> str:
        out = ""
        for piece in self.parts:
            # a line ending in a hyphen continues the word on the next line ("retro-" + "reflective", "A6-" + "1")
            out = out + piece if out.endswith("-") or not out else out + " " + piece
        return " ".join(out.split())


def _table_mentions(text: str, tid: str) -> bool:
    return re.search(rf"\bTables?\s+(?:[A-Z0-9.\-]+\s*(?:,|and|or)\s*)*{re.escape(tid)}(?![\w-])", text) is not None


def _clean_title(text: str) -> str:
    """'- Resistance to corrosion' -> 'Resistance to corrosion'."""
    return " ".join(text.strip(" -–—:.\t").split())


# line kinds produced by the first pass
SKIP, HEADING, TEXT, CAPTION = "skip", "heading", "text", "caption"


def segment(lines: list[Line], rejected: list | None = None) -> list[Para]:
    """Split the document's content lines into paragraphs (and title / table entries), in three passes:
      1. classify every line (heading / table caption / text) and track the annex-appendix-part context and its titles;
         number-like lines at the margin become scored candidates;
      2. choose the paragraph starts among the candidates (`select_starts`);
      3. build the paragraphs, attaching continuation lines, table cells and table entries.
    Sets line.kind, line.footnote_candidate and line.para on every line.
    If `rejected` is a list, candidates that were not chosen are appended to it (debugging)."""
    body, margin = body_style(lines)
    skip_heading_pages = toc_pages(lines)
    small = body - 0.5

    def at_margin(l: Line) -> bool:
        return abs(l.x0 - margin) <= 4

    # ---- pass 1: classify lines, track context, collect candidates -------------------------------------------------
    kinds: list[str] = []
    ctx_of: list[tuple] = []        # per line: (ctx id, section, annex, appendix, part)
    matches: dict[int, re.Match] = {}
    cands: list[Candidate] = []
    section = annex = appendix = part = None
    ctx = 0
    ctx_titles: list[dict] = [{}]   # per ctx id: {"section" | "annex" | "appendix" | "part": (id, title)}
    title_level = None              # level whose title a wrapped heading line continues
    last_was_heading = False
    last_heading = ""      # text of the last annex heading line (wrapped titles)
    prev_text = ""         # previous body line at the margin (for reference-continuation checks)
    footnote_page = 0      # page of the footnote we are in (its continuation lines are small, at the margin)

    def open_ctx(levels: dict, level: str | None) -> None:
        """Start a new context (new annex / appendix / part / section) with these titled levels."""
        nonlocal ctx, title_level
        ctx += 1
        ctx_titles.append(levels)
        title_level = level

    def keep(*names: str) -> dict:
        return {k: v for k, v in ctx_titles[ctx].items() if k in names}

    for idx, l in enumerate(lines):
        text = l.text
        kind = TEXT
        big_bold = l.bold and at_margin(l) and l.size > body + 1
        if l.footnote and l.size < small:
            kind, footnote_page = SKIP, l.page  # footnotes are not part of the paragraph text
            l.footnote_candidate = True
        elif footnote_page == l.page and l.size < small and at_margin(l):
            kind = SKIP                         # second line of a footnote
            l.footnote_candidate = True
        elif l.size < small and at_margin(l) and re.match(r"^(\d{1,2}/|\*{1,3})\s?\S", text):
            kind, footnote_page = SKIP, l.page  # "1/ Strike out what does not apply.", "* ..." footnotes
            l.footnote_candidate = True
        elif l.page in skip_heading_pages and (TOC_LINE_RE.match(text) or
                                               re.match(r"^(table of )?contents$", text, re.I)):
            kind = SKIP  # table-of-contents entries are not paragraphs
            l.kind = "toc"
        # main-body structural headings (only before the annexes)
        elif big_bold and annex is None and l.page not in skip_heading_pages and SECTION_RE.match(text):
            ms = SECTION_RE.match(text)
            if REG_TEXT_RE.search(text):
                section = None
                open_ctx({}, None)
            else:
                section = "Introduction" if ms["intro"] else ms["roman"] or ms["letter"]
                open_ctx({"section": (section, _clean_title(text[ms.end():]))}, "section")
            kind = HEADING
        # annex / appendix / part headings: bold, at the margin, at least body size
        elif l.bold and at_margin(l) and l.size >= small and l.page not in skip_heading_pages \
                and not TOC_LINE_RE.search(text):
            m = ANNEX_RE.match(text)
            ma = APPENDIX_RE.match(text) if text.lower().startswith("appendix") else None
            mp = PART_RE.match(text) if text.lower().startswith("part") else None
            if m and last_was_heading and re.search(r"\b(of|to|in|and|or|with|per|for|the|under|by)$", last_heading):
                kind = HEADING  # "Annex 5 Requirements ... per paragraph 3.3.3. of" + "Annex 8": end of the title
                _extend_title(ctx_titles[ctx], title_level, text)
            elif m:
                annex_id, tail = fix_annex_id(m["annex"].upper(), m["tail"], annex)
                levels = keep("annex") if annex_id == annex else {}
                section, annex, appendix, part = None, annex_id, None, None
                level = "annex"
                ma2 = APPENDIX_RE.match(tail)
                if ma2:
                    appendix, tail, level = (ma2["appendix"] or "").upper(), ma2["tail"], "appendix"
                mp2 = PART_RE.match(tail)
                if mp2:
                    part, tail, level = mp2["part"].upper(), mp2["tail"], "part"
                levels.setdefault("annex", (annex, ""))
                levels[level] = ({"annex": annex, "appendix": appendix, "part": part}[level], _clean_title(tail))
                open_ctx(levels, level)
                kind = HEADING
            elif ma:
                appendix, part = (ma["appendix"] or "").upper(), None
                open_ctx({**keep("section", "annex"), "appendix": (appendix, _clean_title(ma["tail"]))}, "appendix")
                kind = HEADING
            elif mp:
                if annex:  # parts in the main body (e.g. "Part I - Approval of ...") do not restart the numbering
                    part = mp["part"].upper()
                    open_ctx({**keep("section", "annex", "appendix"), "part": (part, _clean_title(mp["tail"]))},
                             "part")
                kind = HEADING
            elif last_was_heading and l.size > body + 1 and not PARA_RE.match(text):
                kind = HEADING  # second line of a wrapped annex title (bold, larger than body)
                _extend_title(ctx_titles[ctx], title_level, text)
        mlp = LETTER_PART_RE.match(text) if kind == TEXT and annex and at_margin(l) and l.size >= small else None
        if mlp and not SHALL_RE.search(text) and (not prev_text or prev_text.rstrip()[-1:] in ".:;)") \
                and mlp["part"] == ("A" if part is None else chr(ord(part[0]) + 1) if len(part) == 1 else ""):
            part, kind = mlp["part"], HEADING  # Part A, then B, C ...
            open_ctx({**keep("annex", "appendix"), "part": (part, _clean_title(text[2:]))}, "part")
        if kind == TEXT and section == "Introduction" and big_bold and PARA_RE.match(text):
            section = None  # "1. Scope" ends a (numbered) introduction
            open_ctx({k: v for k, v in ctx_titles[ctx].items() if k != "section"}, None)
        if kind != SKIP:
            footnote_page = 0
        if kind == HEADING:
            last_heading = text
        last_was_heading = kind == HEADING
        kinds.append(kind)
        ctx_of.append((ctx, section, annex, appendix, part))
        if kind != TEXT or l.size < small or not at_margin(l):
            continue

        # table captions: "Table 3 <bold title>" / bare "Table 3a" / "Table 2: Title"
        mt = TABLE_RE.match(text)
        if mt and (l.any_bold or not mt["rest"].strip(" :.") or re.match(r"^\s*:\s*[A-Z]", mt["rest"])):
            kinds[idx] = CAPTION
            matches[idx] = mt
            continue

        # paragraph-start candidates
        m = PARA_RE.match(text)
        if m:
            rest = m["rest"] or ""
            has_dot = bool(m["dot"])
            num = parse_num(m["num"])
            starts_upper = bool(re.match(r"^[A-Z\"'“‘(\[•]", rest))
            tail = prev_text.rstrip()
            ref_cont = bool(REF_WORD_RE.search(tail) or REF_LIST_RE.search(tail))
            # "5.7 Grouped ..." without the closing dot: only multi-level numbers followed by a word ("13.5 V" is not),
            # or a bold section heading like "1 Scope"
            if has_dot or ((len(num) > 1 or big_bold) and re.match(r"^[A-Z][a-z]{2,}", rest)):
                weight = 0.5 + (0.5 if has_dot else 0) + (0.5 if starts_upper else 0 if not rest else -0.5) \
                    - (2.0 if ref_cont else 0) + (1.5 if big_bold else 0)  # "1. Scope" section headings
                cands.append(Candidate(idx=idx, ctx=ctx, num=num, weight=weight))
                matches[idx] = m
        prev_text = text

    # ---- pass 2: choose paragraph starts ---------------------------------------------------------------------------
    starts = select_starts(cands)
    if rejected is not None:
        for c in cands:
            if c.idx not in starts:
                rejected.append((lines[c.idx].page, ctx_of[c.idx][1:], lines[c.idx].text[:90], round(c.weight, 1)))

    # ---- pass 3: build paragraphs -----------------------------------------------------------------------------------
    paras: list[Para] = []
    cur: Para | None = None
    cur_ctx = 0
    cur_start = 0          # line index where `cur` started
    for idx, l in enumerate(lines):
        kind = kinds[idx]
        if not l.kind:
            l.kind = kind
        ctx, section, annex, appendix, part = ctx_of[idx]

        def new_para(number: str, **kw) -> Para:
            return Para(number=number, section=section, annex=annex, appendix=appendix, part=part, page=l.page,
                        page_end=l.page, ctx=ctx, titles=ctx_titles[ctx], **kw)

        if ctx != cur_ctx:
            # a new annex / appendix / part / section: its heading and any unnumbered lead-in text become a title
            # entry labelled with the prefix alone, e.g. "Annex 6 Part 4"
            cur, cur_ctx = None, ctx
            title = new_para("")
            if title.prefix:
                cur = title
                paras.append(cur)
        if kind == SKIP:
            continue
        if kind == HEADING:
            if cur is not None and cur.number == "":
                cur.add(l.text, l.page, l)
            continue
        if idx in starts:
            m = matches[idx]
            if cur is not None and not cur.is_table and cur.number == m["num"] and len(cur.text) < 80 \
                    and lines[cur_start].size > body + 0.5:
                # "1. Scope" (bold heading) + "1. This Regulation applies ...": one paragraph
                cur.add(m["rest"] or "", l.page, l)
                cur.merged_heading = True
                continue
            cur_start = idx
            cur = new_para(m["num"], head=m["rest"] or "")
            cur.add(m["rest"] or "", l.page, l)
            paras.append(cur)
            continue
        if kind == CAPTION and cur is not None:
            mt = matches[idx]
            tid = mt["tid"]
            if tid.endswith("-") and idx + 1 < len(lines) and re.match(r"^\d+\b", lines[idx + 1].text):
                tid += re.match(r"^\d+", lines[idx + 1].text).group(0)  # "Table A2-" + "1"
            tid = tid.rstrip("-")
            # attach the table to the latest paragraph of this context that mentions it, else the current one
            base = cur.owner if cur.is_table else cur
            owner = base
            for p in [p for p in paras[-15:] if not p.is_table][::-1]:
                if p.prefix != base.prefix:
                    break
                if _table_mentions(p.text, tid):
                    owner = p
                    break
            owner.has_table = True
            number = f"{owner.number}. Table {tid}" if owner.number else f"Table {tid}"
            cur = Para(number=number, section=owner.section, annex=owner.annex, appendix=owner.appendix,
                       part=owner.part, page=l.page, page_end=l.page, ctx=owner.ctx, titles=owner.titles,
                       head=mt["rest"], has_table=True, is_table=True, owner=owner)
            cur.add(l.text, l.page, l)
            paras.append(cur)
            continue
        # continuation text (wrapped lines, lists, table cells)
        if cur is None:
            continue  # text before the first paragraph of a context (introduction, annex titles, forms)
        if l.size < small or (not at_margin(l) and l.size < body):
            cur.has_table = True
        cur.add(l.text, l.page, l)
    merge_sections(paras)
    return [p for p in paras if p.label]


def _extend_title(levels: dict, level: str | None, text: str) -> None:
    """Append a wrapped heading line to the title of `level` in the current context."""
    if level and level in levels:
        ident, title = levels[level]
        levels[level] = (ident, f"{title} {_clean_title(text)}".strip())


def merge_sections(paras: list[Para]) -> None:
    """Main-body sections ("A. Administrative provisions", "I. Statement of technical rationale", "Introduction")
    only keep their label prefix when their numbering restarts, i.e. when their numbers would collide with the
    main body's. Otherwise ("A." / "B." headings over continuous numbering) they are part of the main body."""
    main = [p for p in paras if p.annex is None and p.appendix is None]
    taken = {p.number for p in main if p.section is None and p.number}
    for sec in dict.fromkeys(p.section for p in main if p.section):
        members = [p for p in main if p.section == sec]
        numbers = {p.number for p in members if p.number}
        if numbers and not numbers & taken:
            for p in members:
                p.section = None   # a title entry of a merged section ends up with an empty label and is dropped
            taken |= numbers


# ---------------------------------------------------------------------------------------------------------------------
# Inputs and integrity
# ---------------------------------------------------------------------------------------------------------------------

def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_targets(index_json: Path, pdf_dir: Path, subgoal_root: Path, only: str | None) -> list[dict]:
    """Every file of every version in index.json (roles base / latest), plus the old/new PDFs listed in every
    subgoal/*/files.json (roles subgoal_old / subgoal_new). Each target carries the sha256 it must have."""
    out = []
    for reg in json.loads(index_json.read_text(encoding="utf-8")):
        if only and reg["regulation"] != only:
            continue
        folder = pdf_dir / slug(reg["regulation"])
        for v in reg["versions"]:
            for f in v["files"]:
                out.append({"regulation": reg["regulation"], "title": reg.get("title"), "version": v["version_label"],
                            "role": v["role"], "file": f["name"], "path": str(folder / v["role"] / f["name"]),
                            "sha256": f["sha256"].lower(), "subgoal_case": None})
    if not only or only.lower().startswith("subgoal"):
        for listing in sorted(subgoal_root.glob("*/files.json")):
            case = listing.parent.name
            if only and only.lower() not in ("subgoal", f"subgoal/{case}".lower()):
                continue
            for item in json.loads(listing.read_text(encoding="utf-8")):
                stem = Path(item["name"]).stem
                role = {"old": "subgoal_old", "new": "subgoal_new"}.get(stem, f"subgoal_{stem}")
                # regulation and version are read from the running header
                out.append({"regulation": None, "title": None, "version": None, "role": role,
                            "file": f"subgoal/{case}/{item['name']}", "path": str(listing.parent / item["name"]),
                            "sha256": item["sha256"].lower(), "subgoal_case": case})
    return out


def verify_targets(todo: list[dict]) -> list[str]:
    """Problems (missing file, checksum mismatch) for every target; empty when all PDFs are the expected ones."""
    problems = []
    for t in todo:
        path = Path(t["path"])
        if not path.is_file():
            problems.append(f"MISSING   {path}  (run tools/fetch_regulations.py)")
            t["verified"] = False
            continue
        actual = sha256(path)
        t["sha256_actual"] = actual
        t["verified"] = actual == t["sha256"]
        if not t["verified"]:
            problems.append(f"CHECKSUM  {path}: sha256 {actual} != expected {t['sha256']}")
    return problems


def doc_id(t: dict) -> str:
    return f"{t['regulation']}|{t['version']}|{t['role']}|{t['file']}"


def line_id(doc: str, l: Line) -> str:
    return f"{doc}:p{l.page}:l{l.n}"


# ---------------------------------------------------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------------------------------------------------

def make_records(paras: list[Para], t: dict, doc: str, footnotes: dict[int, list[str]]) -> list[dict]:
    """Base fields of every paragraph record; structure.enrich() adds titles, links and signals."""
    seen: Counter = Counter()
    records = []
    for p in paras:
        label = p.label
        seen[label] += 1
        text = p.text
        records.append({
            "id": f"{doc}|{label}#{seen[label]}",
            "doc_id": doc,
            "regulation": t["regulation"],
            "version": t["version"],
            "role": t["role"],
            "file": t["file"],
            "page": p.page,
            "page_end": p.page_end,
            "paragraph": label,
            "number": p.number,
            "kind": p.kind,
            "section": p.section,
            "annex": p.annex,
            "appendix": p.appendix,
            "part": p.part if p.annex else None,
            "text": text,
            "shall_count": len(SHALL_RE.findall(text)),
            "has_table": p.has_table,
            "table_ids": [],
            "source_line_ids": [line_id(doc, l) for l in p.lines],
            "footnote_context_ids": [i for pg in range(p.page, p.page_end + 1) for i in footnotes.get(pg, [])],
        })
    return records


def page_records(doc_obj: pymupdf.Document, lines: list[Line], t: dict, doc: str, para_ids: dict) -> list[dict]:
    """pages.jsonl: one record per page with every visible line and its flags."""
    by_page: dict[int, list[Line]] = {}
    for l in lines:
        by_page.setdefault(l.page, []).append(l)
    out = []
    for pno, page in enumerate(doc_obj, start=1):
        rows = []
        for l in by_page.get(pno, []):
            pid = para_ids.get(id(l.para)) if l.para is not None else None
            rows.append({
                "id": line_id(doc, l),
                "text": l.text,
                "bbox": list(l.bbox),
                "size": l.size,
                "bold": l.bold,
                "kind": l.flag or l.kind,
                "running_header": l.flag == "running_header",
                "boilerplate": l.flag == "boilerplate",
                "footnote_candidate": l.footnote_candidate,
                "assigned_to_paragraph": pid is not None,
                "paragraph_id": pid,
            })
        out.append({"doc_id": doc, "regulation": t["regulation"], "version": t["version"], "role": t["role"],
                    "file": t["file"], "page": pno, "width": round(page.rect.width, 2),
                    "height": round(page.rect.height, 2), "lines": rows})
    return out


def scope_record(records: list[dict], title: str | None) -> dict:
    """The scope section of one regulation: the top-level main-body paragraph whose heading mentions 'scope'
    (falls back to paragraph 1), with all its sub-paragraphs."""
    main = [r for r in records if r["kind"] == "paragraph" and r["annex"] is None and r["appendix"] is None
            and r["section"] is None]
    top = [r for r in main if "." not in r["paragraph"]]
    head = next((r for r in top if re.search(r"\bscope\b", r["text"][:80], re.I)), None)
    head = head or next((r for r in top if r["paragraph"] == "1"), None)
    if head is None:
        return {"title": title, "version": records[0]["version"] if records else None, "paragraphs": [], "text": ""}
    num = head["paragraph"]
    section = [r for r in main if r["paragraph"] == num or r["paragraph"].startswith(num + ".")]
    return {
        "title": title,
        "version": head["version"],
        "page": head["page"],
        "page_end": max(r["page_end"] for r in section),
        "paragraphs": [r["paragraph"] for r in section],
        "paragraph_ids": [r["id"] for r in section],
        "text": "\n".join(f"{r['paragraph']}. {r['text']}" for r in section),
    }


def process_target(t: dict) -> dict:
    """Index one PDF: paragraphs, pages, tables, definitions and its manifest entry (runs in a worker process)."""
    with pymupdf.open(t["path"]) as pdf:
        all_lines, hreg, hver = read_lines(pdf)
        t = {**t, "regulation": t["regulation"] or hreg or Path(t["file"]).stem, "version": t["version"] or hver or ""}
        doc = doc_id(t)
        content = [l for l in all_lines if not l.flag]
        paras = segment(content)
        body, _ = body_style(content)
        footnotes: dict[int, list[str]] = {}
        for l in content:
            if l.footnote_candidate:
                footnotes.setdefault(l.page, []).append(line_id(doc, l))
        records = make_records(paras, t, doc, footnotes)
        para_ids = {id(p): r["id"] for p, r in zip(paras, records)}
        structure.enrich(records, paras, line_pitch(content, body))
        table_rows = tables.extract(pdf, content, paras, records, para_ids, doc, t)
        defs = structure.definitions(records)
        pages = page_records(pdf, all_lines, t, doc, para_ids)
    visible = all_lines
    assigned = sum(1 for l in visible if l.para is not None and id(l.para) in para_ids)
    entry = {
        "doc_id": doc, "regulation": t["regulation"], "version": t["version"], "role": t["role"], "file": t["file"],
        "subgoal_case": t["subgoal_case"], "path": str(Path(t["path"]).resolve().relative_to(ROOT))
        if Path(t["path"]).resolve().is_relative_to(ROOT) else t["path"],
        "sha256": t["sha256"], "sha256_verified": t.get("verified", False),
        "pages": len(pages), "visible_lines": len(visible),
        "running_header_lines": sum(1 for l in visible if l.flag == "running_header"),
        "boilerplate_lines": sum(1 for l in visible if l.flag == "boilerplate"),
        "footnote_lines": sum(1 for l in visible if l.footnote_candidate),
        "assigned_lines": assigned,
        "unassigned_content_lines": sum(1 for l in content if not l.footnote_candidate
                                        and (l.para is None or id(l.para) not in para_ids)),
        "paragraphs": len(records),
        "paragraphs_with_shall": sum(1 for r in records if r["shall_count"]),
        "tables": len(table_rows),
        "definitions": len(defs),
    }
    scope = scope_record(records, t["title"]) if t["role"] == "base" else None
    return {"target": t, "records": records, "pages": pages, "tables": table_rows, "definitions": defs,
            "manifest": entry, "scope": scope}


def duplicate_ids(rows: list[dict], key: str = "id") -> list[str]:
    counts = Counter(r[key] for r in rows)
    return sorted(k for k, n in counts.items() if n > 1)


def write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help='index one regulation, e.g. "UN R150" (or "subgoal", "subgoal/dev")')
    ap.add_argument("--roles", nargs='+', choices=('base', 'latest', 'subgoal_old', 'subgoal_new'),
                    help='Explicit corpus roles; use --roles base for the main pipeline')
    ap.add_argument("--out", help="output directory (default: build/, or build/only/ with --only)")
    ap.add_argument("--allow-missing", action="store_true",
                    help="debugging only: skip missing or mismatching PDFs (with a warning) instead of failing")
    ap.add_argument("--jobs", type=int, default=os.cpu_count() or 1, help="worker processes (1 = no parallelism)")
    ap.add_argument("--index", default=str(INDEX_JSON), help=argparse.SUPPRESS)
    ap.add_argument("--pdf-dir", default=str(PDF_DIR), help=argparse.SUPPRESS)
    ap.add_argument("--subgoal-root", default=str(SUBGOAL_ROOT), help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    out_dir = Path(args.out) if args.out else (BUILD / "only" if args.only else BUILD)
    started = time.time()
    todo = collect_targets(Path(args.index), Path(args.pdf_dir), Path(args.subgoal_root), args.only)
    if args.roles:
        todo = [t for t in todo if t['role'] in args.roles]
    if not todo:
        print(f"error: nothing to index for --only {args.only!r}", file=sys.stderr)
        return 2

    # ---- integrity: every PDF must be the one the index points to ----------------------------------------------------
    problems = verify_targets(todo)
    for p in problems:
        print(p, file=sys.stderr)
    if problems and not args.allow_missing:
        print(f"error: {len(problems)} PDF(s) missing or not matching their sha256; nothing was written "
              "(use --allow-missing for debugging only)", file=sys.stderr)
        return 1
    skipped = [{"path": t["path"], "role": t["role"], "file": t["file"]} for t in todo if not t["verified"]]
    todo = [t for t in todo if t["verified"]]
    if problems:
        print(f"WARNING: --allow-missing: {len(skipped)} file(s) skipped; this index is incomplete", file=sys.stderr)

    # ---- index every file (in parallel; map keeps the input order, so the output is deterministic) -----------------
    if args.jobs > 1 and len(todo) > 1:
        with ProcessPoolExecutor(max_workers=args.jobs) as pool:
            results = list(pool.map(process_target, todo))
    else:
        results = [process_target(t) for t in todo]

    records = [r for res in results for r in res["records"]]
    pages = [p for res in results for p in res["pages"]]
    table_rows = [r for res in results for r in res["tables"]]
    defs = [r for res in results for r in res["definitions"]]
    for res in results:
        m = res["manifest"]
        print(f"{m['regulation']:<10} {m['version']:<8} {m['role']:<12} {m['file']:<28} "
              f"paragraphs={m['paragraphs']:>5}  with_shall={m['paragraphs_with_shall']:>5}  tables={m['tables']:>4}"
              f"  unassigned_lines={m['unassigned_content_lines']:>4}")

    # ---- every id must be unique ------------------------------------------------------------------------------------
    dups = {"paragraphs": duplicate_ids(records), "tables": duplicate_ids(table_rows),
            "definitions": duplicate_ids(defs),
            "lines": duplicate_ids([ln for p in pages for ln in p["lines"]]),
            "documents": duplicate_ids([res["manifest"] for res in results], "doc_id")}
    if any(dups.values()):
        for kind, ids in dups.items():
            for i in ids[:20]:
                print(f"DUPLICATE {kind} id: {i}", file=sys.stderr)
        print("error: duplicate ids; nothing was written", file=sys.stderr)
        return 1

    out_dir.mkdir(parents=True, exist_ok=True)
    write_jsonl(out_dir / "paragraphs.jsonl", records)
    write_jsonl(out_dir / "pages.jsonl", pages)
    write_jsonl(out_dir / "tables.jsonl", table_rows)
    write_jsonl(out_dir / "definitions.jsonl", defs)
    scopes = {res["target"]["regulation"]: res["scope"] for res in results if res["scope"] is not None}
    (out_dir / "regulation_scopes.json").write_text(json.dumps(scopes, ensure_ascii=False, indent=2) + "\n",
                                                    encoding="utf-8")
    files = [res["manifest"] for res in results]
    keys = [k for k, v in files[0].items() if isinstance(v, int) and not isinstance(v, bool)] if files else []
    manifest = {
        "files": files,
        "skipped_files": skipped,
        "totals": {"files": len(files), **{k: sum(f[k] for f in files) for k in keys},
                   "cross_refs": dict(sorted(Counter(x["status"] for r in records for x in r["cross_refs"]).items())),
                   "parent_status": dict(sorted(Counter(r["parent_status"] for r in records).items())),
                   "table_owner_status": dict(sorted(Counter(t["owner_status"] for t in table_rows).items()))},
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if SCHEMA_MD.is_file():
        shutil.copyfile(SCHEMA_MD, out_dir / "SCHEMA.md")
    print(f"\n{len(records)} paragraphs, {len(pages)} pages, {len(table_rows)} tables, {len(defs)} definitions "
          f"from {len(results)} files -> {out_dir}/ ({time.time() - started:.1f}s)")
    return 1 if skipped and not args.allow_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
