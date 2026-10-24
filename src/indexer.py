#!/usr/bin/env python3
"""Deterministic PDF indexer: turns every regulation PDF into numbered paragraphs with exact citations.

    python src/indexer.py                  # index everything in regulations/index.json + the sub-goal PDFs
    python src/indexer.py --only "UN R150" # one regulation, for quick debugging

Outputs (in build/, or build/only/ with --only):
    paragraphs.jsonl          one JSON object per paragraph:
        id          "UN R150|1.2.1|Annex 6 Part 4 1" (+ "#2", "#3" if a label repeats in one file, e.g. forms)
        regulation, version, role ("base" | "latest" | "subgoal_old" | "subgoal_new"), file
        page, page_end   cited page where the paragraph starts / ends (physical page index + 1)
        paragraph   label as the experts write it: "3.3.1.2", "Annex 6 Part 4 1", "5.1.4.4.1. Table 3",
                    "Annex 6 Part 4" (title entry: the heading and unnumbered lead-in text of that annex part)
        section     main-body section that restarts the numbering ("Introduction", "I", "B"), else null
        annex, appendix, part   context ids ("6", "1", "4"); part only inside annexes. appendix "" = unnumbered
        parent      "3.3.1" for "3.3.1.2"; the title entry for a top-level annex paragraph; the paragraph a table
                    belongs to for table entries
        text, shall_count, has_table, cross_refs ("paragraph 3.3.4.2." -> "3.3.4.2")
    regulation_scopes.json    per regulation (base version): title + full text of its scope section

How the PDFs look (all are rendered by Certivity in the same style):
  * every page has a running header "UN R150, version 1.2.1 | page 7 of 54" (dropped);
    page 1 also has a short boilerplate block "UN R150 (version 1.2.1)", "Consolidated text ...", "Role in the hackathon: ..."
  * body text uses one dominant font size per document (7 to 10.5 pt) at a fixed left margin;
  * paragraphs start at the margin with "3.3.1.1. Text", sometimes "5.7 Text" (no dot) or the number alone on its line;
  * annex / appendix / part headings are bold lines at the margin: "Annex 6 Environmental Testing",
    "Annex 1 - Appendix 2 ...", "Part 4 - Resistance to corrosion". Annexes restart the paragraph numbering;
  * table captions are "Table 3 <bold title>" or a bare "Table 3a"; table cells use a smaller font or are indented;
  * footnotes start with a small superscript digit ("6This does not apply ...") and use a smaller font.

Paragraph starts are chosen globally, not line by line: every number-like line at the margin is a candidate with a
weight (closing dot, capital letter, bold heading: up; previous line ends in "paragraph": down), and per annex context
the chain with the best total score wins, where a believable next number (5.1 -> 5.1.1 -> 5.2) scores and a backward
jump costs. A stray "6. below, ..." at the start of a wrapped line is skipped because it would break the sequence.

No LLM or network calls. Same input -> byte-identical output.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parent.parent
INDEX_JSON = ROOT / "regulations" / "index.json"
PDF_DIR = ROOT / "regulations" / "pdf"
SUBGOAL_DIR = ROOT / "subgoal" / "dev"
BUILD = ROOT / "build"

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
# cross references: "paragraph 3.3.4.2.", "paragraphs 2.3.10., 2.3.10.1. and 2.3.10.2."
XREF_RE = re.compile(r"\b(?:paragraphs?|paras?\.?)\s+((?:\d+(?:\.\d+)*\.?)(?:\s*(?:,|and|or|to|-|–)\s*\d+(?:\.\d+)*\.?)*)", re.I)
SHALL_RE = re.compile(r"\bshall\b", re.I)


# ---------------------------------------------------------------------------------------------------------------------
# Page lines
# ---------------------------------------------------------------------------------------------------------------------

@dataclass
class Line:
    text: str
    page: int        # 1-based, = cited page
    x0: float
    size: float      # dominant font size of the line (by characters)
    bold: bool       # first visible span is bold
    any_bold: bool
    footnote: bool   # starts with a small superscript digit


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
    return Line(text=text, page=page_no, x0=bbox[0], size=size,
                bold="Bold" in first["font"], any_bold=any("Bold" in s["font"] for s in visible), footnote=footnote)


def read_lines(doc: pymupdf.Document) -> tuple[list[Line], str | None, str | None]:
    """All text lines of the document in reading order, without running headers and page-1 boilerplate.
    Also returns (regulation, version) as printed in the running header."""
    lines: list[Line] = []
    reg = ver = None
    for pno, page in enumerate(doc):
        for block in page.get_text("dict")["blocks"]:
            for raw in block.get("lines", []):
                line = _line_from_spans(raw["spans"], raw["bbox"], pno + 1)
                if line is None:
                    continue
                m = HEADER_RE.match(line.text)
                if m:
                    reg, ver = reg or m["reg"], ver or m["ver"]
                    continue
                if pno == 0 and BOILERPLATE_RE.match(line.text):
                    continue
                lines.append(line)
    return lines, reg, ver


def body_style(lines: list[Line]) -> tuple[float, float]:
    """(body font size, left margin): the most common size of long lines, and the most common x0 at that size."""
    sizes = Counter(l.size for l in lines if len(l.text) > 40)
    body = sizes.most_common(1)[0][0] if sizes else 10.5
    xs = Counter(round(l.x0) for l in lines if abs(l.size - body) < 0.3)
    margin = xs.most_common(1)[0][0] if xs else 57
    return body, margin


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


@dataclass
class Para:
    number: str                    # "3.3.1.2" or "3. Table A6-1" for tables
    section: str | None            # main-body section that restarts the numbering ("Introduction", "I", "B")
    annex: str | None
    appendix: str | None
    part: str | None
    page: int
    page_end: int
    parts: list[str] = field(default_factory=list)   # text pieces
    has_table: bool = False
    is_table: bool = False
    owner: "Para | None" = None    # for table entries: the paragraph the table belongs to

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

    def add(self, text: str, page: int) -> None:
        self.parts.append(text)
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


# line kinds produced by the first pass
SKIP, HEADING, TEXT, CAPTION = "skip", "heading", "text", "caption"


def segment(lines: list[Line], rejected: list | None = None) -> list[Para]:
    """Split the document's lines into paragraphs (and table entries), in three passes:
      1. classify every line (heading / table caption / text) and track the annex-appendix-part context;
         number-like lines at the margin become scored candidates;
      2. choose the paragraph starts among the candidates (`select_starts`);
      3. build the paragraphs, attaching continuation lines, table cells and table entries.
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
    last_was_heading = False
    last_heading = ""      # text of the last annex heading line (wrapped titles)
    prev_text = ""         # previous body line at the margin (for reference-continuation checks)
    footnote_page = 0      # page of the footnote we are in (its continuation lines are small, at the margin)

    for idx, l in enumerate(lines):
        text = l.text
        kind = TEXT
        big_bold = l.bold and at_margin(l) and l.size > body + 1
        if l.footnote and l.size < small:
            kind, footnote_page = SKIP, l.page  # footnotes are not part of the paragraph text
        elif footnote_page == l.page and l.size < small and at_margin(l):
            kind = SKIP                         # second line of a footnote
        elif l.size < small and at_margin(l) and re.match(r"^(\d{1,2}/|\*{1,3})\s?\S", text):
            kind, footnote_page = SKIP, l.page  # "1/ Strike out what does not apply.", "* ..." footnotes
        elif l.page in skip_heading_pages and (TOC_LINE_RE.match(text) or
                                               re.match(r"^(table of )?contents$", text, re.I)):
            kind = SKIP  # table-of-contents entries are not paragraphs
        # main-body structural headings (only before the annexes)
        elif big_bold and annex is None and l.page not in skip_heading_pages and SECTION_RE.match(text):
            ms = SECTION_RE.match(text)
            if REG_TEXT_RE.search(text):
                section = None
            elif ms["intro"]:
                section = "Introduction"
            elif ms["roman"]:
                section = ms["roman"]
            else:
                section = ms["letter"]
            ctx, kind = ctx + 1, HEADING
        # annex / appendix / part headings: bold, at the margin, at least body size
        elif l.bold and at_margin(l) and l.size >= small and l.page not in skip_heading_pages \
                and not TOC_LINE_RE.search(text):
            m = ANNEX_RE.match(text)
            ma = APPENDIX_RE.match(text) if text.lower().startswith("appendix") else None
            mp = PART_RE.match(text) if text.lower().startswith("part") else None
            if m and last_was_heading and re.search(r"\b(of|to|in|and|or|with|per|for|the|under|by)$", last_heading):
                kind = HEADING  # "Annex 5 Requirements ... per paragraph 3.3.3. of" + "Annex 8": end of the title
            elif m:
                annex_id, tail = fix_annex_id(m["annex"].upper(), m["tail"], annex)
                section, annex, appendix, part = None, annex_id, None, None
                ma2 = APPENDIX_RE.match(tail)
                if ma2:
                    appendix, tail = (ma2["appendix"] or "").upper(), ma2["tail"]
                mp2 = PART_RE.match(tail)
                if mp2:
                    part = mp2["part"].upper()
                ctx, kind = ctx + 1, HEADING
            elif ma:
                appendix, part = (ma["appendix"] or "").upper(), None
                ctx, kind = ctx + 1, HEADING
            elif mp:
                if annex:  # parts in the main body (e.g. "Part I - Approval of ...") do not restart the numbering
                    part = mp["part"].upper()
                    ctx += 1
                kind = HEADING
            elif last_was_heading and l.size > body + 1 and not PARA_RE.match(text):
                kind = HEADING  # second line of a wrapped annex title (bold, larger than body)
        mlp = LETTER_PART_RE.match(text) if kind == TEXT and annex and at_margin(l) and l.size >= small else None
        if mlp and not SHALL_RE.search(text) and (not prev_text or prev_text.rstrip()[-1:] in ".:;)") \
                and mlp["part"] == ("A" if part is None else chr(ord(part[0]) + 1) if len(part) == 1 else ""):
            part, ctx, kind = mlp["part"], ctx + 1, HEADING  # Part A, then B, C ...
        if kind == TEXT and section == "Introduction" and big_bold and PARA_RE.match(text):
            section, ctx = None, ctx + 1  # "1. Scope" ends a (numbered) introduction
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
        ctx, section, annex, appendix, part = ctx_of[idx]
        if ctx != cur_ctx:
            # a new annex / appendix / part / section: its heading and any unnumbered lead-in text become a title
            # entry labelled with the prefix alone, e.g. "Annex 6 Part 4"
            cur, cur_ctx = None, ctx
            title = Para(number="", section=section, annex=annex, appendix=appendix, part=part, page=l.page,
                         page_end=l.page)
            if title.prefix:
                cur = title
                paras.append(cur)
        if kind == SKIP:
            continue
        if kind == HEADING:
            if cur is not None and cur.number == "":
                cur.add(l.text, l.page)
            continue
        if idx in starts:
            m = matches[idx]
            if cur is not None and not cur.is_table and cur.number == m["num"] and len(cur.text) < 80 \
                    and lines[cur_start].size > body + 0.5:
                # "1. Scope" (bold heading) + "1. This Regulation applies ...": one paragraph
                cur.add(m["rest"] or "", l.page)
                continue
            cur_start = idx
            cur = Para(number=m["num"], section=section, annex=annex, appendix=appendix, part=part, page=l.page,
                       page_end=l.page)
            if m["rest"]:
                cur.add(m["rest"], l.page)
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
                       part=owner.part, page=l.page, page_end=l.page, has_table=True, is_table=True, owner=owner)
            cur.add(l.text, l.page)
            paras.append(cur)
            continue
        # continuation text (wrapped lines, lists, table cells)
        if cur is None:
            continue  # text before the first paragraph of a context (introduction, annex titles, forms)
        if l.size < small or (not at_margin(l) and l.size < body):
            cur.has_table = True
        cur.add(l.text, l.page)
    merge_sections(paras)
    return [p for p in paras if p.label]


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
# Records
# ---------------------------------------------------------------------------------------------------------------------

def cross_refs(text: str) -> list[str]:
    refs: list[str] = []
    for m in XREF_RE.finditer(text):
        for num in re.findall(r"\d+(?:\.\d+)*", m.group(1)):
            if num not in refs:
                refs.append(num)
    return refs


def make_record(p: Para, target: dict, seen: Counter) -> dict:
    label = p.label
    seen[label] += 1
    pid = f"{target['regulation']}|{target['version']}|{label}"
    if seen[label] > 1:  # same label twice in one file (repeated forms etc.): keep ids unique
        pid += f"#{seen[label]}"
    if p.is_table:
        parent = p.owner.label
    else:
        up = p.number.rsplit(".", 1)[0] if "." in p.number else None
        # "3.3.1" for "3.3.1.2"; the title entry ("Annex 6 Part 4") for a top-level annex paragraph
        parent = f"{p.prefix} {up}".strip() if up else (p.prefix or None) if p.number else None
    text = p.text
    return {
        "id": pid,
        "regulation": target["regulation"],
        "version": target["version"],
        "role": target["role"],
        "file": target["file"],
        "page": p.page,
        "page_end": p.page_end,
        "paragraph": label,
        "section": p.section,
        "annex": p.annex,
        "appendix": p.appendix,
        "part": p.part if p.annex else None,
        "parent": parent,
        "text": text,
        "shall_count": len(SHALL_RE.findall(text)),
        "has_table": p.has_table,
        "cross_refs": cross_refs(text),
    }


def targets(only: str | None) -> list[dict]:
    """Every file of every version in index.json, plus the two sub-goal PDFs."""
    out = []
    for reg in json.loads(INDEX_JSON.read_text(encoding="utf-8")):
        if only and reg["regulation"] != only:
            continue
        folder = PDF_DIR / reg["regulation"].replace(" ", "_")
        for v in reg["versions"]:
            for f in v["files"]:
                out.append({"regulation": reg["regulation"], "title": reg.get("title"), "version": v["version_label"],
                            "role": v["role"], "file": f["name"], "path": folder / v["role"] / f["name"]})
    if not only or only.lower().startswith("subgoal"):
        for role, name in (("subgoal_old", "old.pdf"), ("subgoal_new", "new.pdf")):
            # regulation and version are read from the running header
            out.append({"regulation": None, "title": None, "version": None, "role": role, "file": name,
                        "path": SUBGOAL_DIR / name})
    return out


def scope_record(records: list[dict], title: str | None) -> dict:
    """The scope section of one regulation: the top-level main-body paragraph whose heading mentions 'scope'
    (falls back to paragraph 1), with all its sub-paragraphs."""
    main = [r for r in records if r["annex"] is None and r["appendix"] is None and r["section"] is None]
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
        "text": "\n".join(f"{r['paragraph']}. {r['text']}" for r in section),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--only", help='index one regulation, e.g. "UN R150" (or "subgoal")')
    ap.add_argument("--out", help="output directory (default: build/, or build/only/ with --only)")
    args = ap.parse_args(argv)

    out_dir = Path(args.out) if args.out else (BUILD / "only" if args.only else BUILD)
    out_dir.mkdir(parents=True, exist_ok=True)
    started = time.time()
    all_records: list[dict] = []
    scopes: dict[str, dict] = {}
    todo = targets(args.only)
    if not todo:
        print(f"no regulation named {args.only!r} in {INDEX_JSON}", file=sys.stderr)
        return 2

    for t in todo:
        if not t["path"].is_file():
            print(f"MISSING  {t['path'].relative_to(ROOT)}  (run tools/fetch_regulations.py)")
            continue
        with pymupdf.open(t["path"]) as doc:
            lines, hreg, hver = read_lines(doc)
        t["regulation"] = t["regulation"] or hreg or t["file"]
        t["version"] = t["version"] or hver or ""
        seen: Counter = Counter()
        records = [make_record(p, t, seen) for p in segment(lines)]
        all_records.extend(records)
        n_shall = sum(1 for r in records if r["shall_count"])
        print(f"{t['regulation']:<10} {t['version']:<8} {t['role']:<12} {t['file']:<28} "
              f"paragraphs={len(records):>5}  with_shall={n_shall:>5}")
        if t["role"] == "base":
            scopes[t["regulation"]] = scope_record(records, t["title"])

    with open(out_dir / "paragraphs.jsonl", "w", encoding="utf-8") as fh:
        for r in all_records:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    (out_dir / "regulation_scopes.json").write_text(
        json.dumps(scopes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"\n{len(all_records)} paragraphs from {len(todo)} files -> {out_dir}/paragraphs.jsonl "
          f"({time.time() - started:.1f}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
