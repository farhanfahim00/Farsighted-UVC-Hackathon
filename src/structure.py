"""Structure on top of the segmented paragraphs of ONE document: titles, breadcrumbs, parent / child links, obligation
and condition signals, resolved cross references, annex kinds and definitions.

Everything here is a deterministic heuristic over the records built by indexer.py; nothing is dropped, ambiguous
cases are flagged instead of guessed. The fields are described in SCHEMA.md.
"""

from __future__ import annotations

import re
from collections import defaultdict

# ---------------------------------------------------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------------------------------------------------

# counted separately; "shall_not" is a subset of "shall", "must_not" of "must"
OBLIGATION_RES = {
    "shall": re.compile(r"\bshall\b", re.I),
    "shall_not": re.compile(r"\bshall\s+not\b", re.I),
    "must": re.compile(r"\bmust\b", re.I),
    "must_not": re.compile(r"\bmust\s+not\b", re.I),
    "is_required": re.compile(r"\b(?:is|are)\s+required\b", re.I),
    "need_not": re.compile(r"\bneed\s+not\b", re.I),
    "may": re.compile(r"\bmay\b", re.I),
}
OBLIGATION_KEYS = ("shall", "must", "is_required")   # "need not" and "may" are permissions, not obligations
OBLIGATION_WORD_RE = re.compile(r"\b(shall|must|required)\b", re.I)

# longest alternatives first, so "if fitted" is not also counted as "if"
CONDITION_RE = re.compile(
    r"\b(if\s+(?:so\s+)?(?:fitted|equipped|installed)|provided\s+that|in\s+the\s+case\s+of|unless|except|either"
    r"|whenever|if|when|where)\b", re.I)


def obligation_signals(text: str) -> dict[str, int]:
    return {k: len(rx.findall(text)) for k, rx in OBLIGATION_RES.items()}


def has_obligation(signals: dict[str, int]) -> bool:
    return any(signals[k] for k in OBLIGATION_KEYS)


def condition_signals(text: str) -> list[dict]:
    """[{"signal": "if_fitted", "pos": 17}, ...]; pos is the character offset in the paragraph text."""
    out = []
    for m in CONDITION_RE.finditer(text):
        word = " ".join(m[1].lower().split())
        if word.startswith("if ") and word != "if":
            sig = "if_equipped" if "equipped" in word else "if_fitted"
        elif word == "either":
            if not re.search(r"\bor\b", text[m.end():m.end() + 300], re.I):
                continue          # "either" without an "or" is not an alternative
            sig = "either_or"
        else:
            sig = word.replace(" ", "_").replace("whenever", "when")
        out.append({"signal": sig, "pos": m.start()})
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Titles
# ---------------------------------------------------------------------------------------------------------------------

# "In width: that point ..." / "Horizontal angle: 30° ..." -- a short label, a colon, a value
INLINE_LABEL_RE = re.compile(r"^(?P<label>[A-Z][A-Za-z'’\-/() ]{0,48}?)\s?:\s+(?=\S)")
# a heading line never ends in one of these (the sentence continues on the next line)
OPEN_END_RE = re.compile(r"\b(the|of|and|or|to|a|an|in|with|for|by|on|at|from|as|that|which|its|their)$", re.I)


def paragraph_title(p, pitch: float) -> tuple[str | None, str | None]:
    """(title, how) of a numbered paragraph:
      * "bold_heading":  "1. Scope" (bold) merged with "1. This Regulation applies ...";
      * "inline_label":  "6.14.4.1. In width: that point ..." -> "In width";
      * "heading_line":  "6.14. Rear retro-reflector, non-triangular" -- a short first line, no closing punctuation
                         or obligation word, followed by a clear vertical gap (or nothing: the children follow)."""
    head = p.head.strip()
    if not head:
        return None, None
    if p.merged_heading:
        return head.rstrip(". "), "bold_heading"
    m = INLINE_LABEL_RE.match(head)
    if m and len(m["label"].split()) <= 5 and not OBLIGATION_WORD_RE.search(m["label"]):
        return m["label"].strip(), "inline_label"
    words = head.split()
    if len(head) > 120 or len(words) > 15 or not head[0].isupper() or OBLIGATION_WORD_RE.search(head) \
            or head[-1] in ",;:" or OPEN_END_RE.search(head.rstrip(". ")):
        return None, None
    if head.endswith(".") and len(words) > 6:
        return None, None          # a short sentence, not a heading
    first = p.lines[0]
    nxt = p.lines[1] if len(p.lines) > 1 else None
    gap_after = nxt is None or nxt.page != first.page or nxt.bbox[1] - first.bbox[1] > 1.15 * pitch
    if first.bold or gap_after:
        return head.rstrip(". "), "heading_line"
    return None, None


def context_title(p) -> str | None:
    """Title of a title entry: the title of the deepest context level it opens ("Annex 6 Part 4" -> part 4's title)."""
    levels = [k for k, present in (("section", p.section), ("annex", p.annex), ("appendix", p.appendix is not None),
                                   ("part", p.part and p.annex)) if present]
    if not levels:
        return None
    ident_title = p.titles.get(levels[-1])
    return ident_title[1] or None if ident_title else None


def context_crumbs(p) -> list[str]:
    """Breadcrumb items of the contexts a paragraph sits in: ["Annex 6 Environmental testing", "Part 4 Resistance ..."]."""
    t = p.titles
    out = []

    def title_of(level: str, ident) -> str:
        v = t.get(level)
        return v[1] if v and v[0] == ident else ""

    if p.section:
        out.append(" ".join(f"{p.section} {title_of('section', p.section)}".split()))
    if p.annex:
        out.append(" ".join(f"Annex {p.annex} {title_of('annex', p.annex)}".split()))
    if p.appendix is not None:
        out.append(" ".join(f"Appendix {p.appendix} {title_of('appendix', p.appendix)}".split()))
    if p.part and p.annex:
        out.append(" ".join(f"Part {p.part} {title_of('part', p.part)}".split()))
    return out


def _prefix(section, annex, appendix, part) -> str:
    bits = [section] if section else []
    if annex:
        bits.append(f"Annex {annex}")
    if appendix is not None:
        bits.append(f"Appendix {appendix}".strip())
    if part and annex:
        bits.append(f"Part {part}")
    return " ".join(bits)


def enclosing_prefix(p) -> str | None:
    """Label of the context that contains a title entry's context ("Annex 6 Part 4" -> "Annex 6"), None at the top."""
    levels = [p.section, p.annex, p.appendix, p.part if p.annex else None]
    present = [i for i, v in enumerate(levels) if v is not None and (i != 0 or v)]
    if len(present) <= 1:
        return None
    levels[present[-1]] = None
    return _prefix(*levels) or None


# ---------------------------------------------------------------------------------------------------------------------
# Cross references
# ---------------------------------------------------------------------------------------------------------------------

_ID = r"(?:\d+[A-Za-z]?|[A-Z]\d*|[IVX]+)"
_NUM = r"\d+(?:\.\d+)*"
PARA_REF_RE = re.compile(rf"\b(?:paragraphs?|paras?\.?|§)\s*((?:{_NUM}\.?)(?:\s*(?:,|and|or|to|-|–)\s*{_NUM}\.?)*)", re.I)
ANNEX_REF_RE = re.compile(rf"\bAnnex(?:es)?\s+({_ID}(?:\s*(?:,|and|or|to)\s*{_ID})*)\b(?:\s*,?\s*Appendix\s+({_ID})\b)?")
APPX_REF_RE = re.compile(rf"\bAppendix\s+({_ID})\s+(?:to|of)\s+(?:this\s+)?Annex\s+({_ID})\b")
TABLE_REF_RE = re.compile(r"\bTables?\s+((?:[A-Z]{0,2}\d+(?:[-.]\d+)*[a-z]?)(?:\s*(?:,|and|or|to)\s*"
                          r"[A-Z]{0,2}\d+(?:[-.]\d+)*[a-z]?)*)")
# what follows a reference: "... of this Regulation", "... of this annex", "... of Annex 3", "... of UN Regulation No. 48"
QUALIFIER_RE = re.compile(
    rf"^\s*(?:\([a-z0-9]{{1,3}}\)\s*)?(?:above|below)?\s*,?\s*(?:of|in|to)\s+(?:"
    rf"(?P<reg>this|the present)\s+Regulation"
    rf"|(?P<here>this|the present)\s+(?:annex|appendix)"
    rf"|(?:Part\s+(?P<qpart>{_ID})|Appendix\s+(?P<qapp>{_ID}))\s+(?:of|to)\s+(?:(?P<qhere>this|the present)\s+annex|Annex\s+(?P<qannex>{_ID}))"
    rf"|Annex\s+(?P<annex>{_ID})(?:\s*,?\s*(?:Appendix\s+(?P<app>{_ID})|Part\s+(?P<part>{_ID})))?"
    rf"|(?P<other>(?:Part|Appendix|Annex)\b)"
    rf"|(?P<ext>(?:UN\s+)?(?:Regulations?|GTR|Global Technical Regulation)\s+No|the\s+\d{{2}}\s+series|ISO|IEC|CISPR|EN\s\d))",
    re.I)


def _split_ids(group: str) -> list[str]:
    # A6-1 and 3-2 are atomic table identifiers, not two references.
    return [x for x in re.split(r"\s*(?:,|\band\b|\bor\b|\bto\b)\s*|\s+[-–]\s+", group.strip()) if x]


class _Lookup:
    """Records of one document by label / kind, for resolving links."""

    def __init__(self, records: list[dict], paras: list):
        self.records, self.paras = records, paras
        self.by_label: dict[str, list[int]] = defaultdict(list)
        for i, r in enumerate(records):
            self.by_label[r["paragraph"]].append(i)
        self.titles = [i for i, r in enumerate(records) if r["kind"] == "title"]
        self.tables = [i for i, r in enumerate(records) if r["kind"] == "table"]

    def find(self, label: str, kind: str | None = None) -> list[int]:
        return [j for j in self.by_label.get(label, ()) if kind is None or self.records[j]["kind"] == kind]


def _ref(kind, ref, raw, pos, qualifier, found: list[int], records, scope=None, external=False) -> dict:
    out = {"kind": kind, "ref": ref, "raw": raw, "pos": pos, "qualifier": qualifier}
    if external:
        out.update(status="external", target_id=None)
    elif len(found) == 1:
        out.update(status="resolved", target_id=records[found[0]]["id"], scope=scope)
    elif found:
        out.update(status="ambiguous", target_id=None, candidates=[records[j]["id"] for j in found[:10]])
    else:
        out.update(status="unresolved", target_id=None)
    return out


def resolve_refs(i: int, lk: _Lookup) -> list[dict]:
    """Every "paragraph X", "Annex N" and "Table N" reference in record i, resolved to a record of the same document
    when the target is unique. Scope rules: an explicit qualifier wins ("of this Regulation" -> main body, "of this
    annex", "of Annex 3"); without one, a reference inside an annex means that annex's own paragraph when it has one
    (the drafting convention), else the main body. A reference to another regulation or standard is "external"."""
    r, p, records = lk.records[i], lk.paras[i], lk.records
    text = r["text"]
    out: list[dict] = []
    for m in PARA_REF_RE.finditer(text):
        q = QUALIFIER_RE.match(text[m.end():m.end() + 90])
        qual = q.group(0).strip() if q else None
        if re.search(r'\bto\b|[-–]', m.group(1)):
            # Endpoints alone omit intermediate obligations. Keep the complete
            # range visible for later expansion instead of claiming resolution.
            ref = _ref('paragraph', m.group(1), m.group(0), m.start(), qual, [], records,
                       external=bool(q and q['ext']))
            ref['reason'] = 'range_requires_expansion'
            out.append(ref)
            continue
        for num in re.findall(_NUM, m.group(1)):
            if q and q["ext"]:
                out.append(_ref("paragraph", num, m.group(0), m.start(), qual, [], records, external=True))
                continue
            if q and q["reg"]:
                scopes = [("main_body", num)]
            elif q and q["here"]:
                scopes = [("same_context", f"{p.prefix} {num}".strip()), ("same_annex", f"Annex {p.annex} {num}")]
            elif q and (q["annex"] or q["qannex"] or q["qhere"]):
                # "of Annex 3", "of Annex 3, Appendix 1", "of Part 1 of Annex 4", "of Appendix 2 to this annex"
                annex = (q["annex"] or q["qannex"] or p.annex or "").upper()
                app, part = q["app"] or q["qapp"], q["part"] or q["qpart"]
                sub = f" Appendix {app.upper()}" if app else f" Part {part.upper()}" if part else ""
                scopes = [("named_annex", f"Annex {annex}{sub} {num}")]
            elif q and q["other"]:
                scopes = []          # a qualifier we cannot parse: leave the reference unresolved, never guess
            elif p.prefix:
                scopes = [("same_context", f"{p.prefix} {num}"), ("main_body", num)]
            else:
                scopes = [("main_body", num)]
            found, scope = [], None
            for scope, label in scopes:          # first scope that has the label
                found = lk.find(label, "paragraph")
                if found:
                    break
            out.append(_ref("paragraph", num, m.group(0), m.start(), qual, found, records, scope))
    annex_refs = [(m.start(), m.group(0), ident, m.group(2)) for m in ANNEX_REF_RE.finditer(text)
                  for ident in _split_ids(m.group(1))]
    annex_refs += [(m.start(), m.group(0), m.group(2), m.group(1)) for m in APPX_REF_RE.finditer(text)]
    for pos, raw, ident, app in sorted(annex_refs):
        q = QUALIFIER_RE.match(text[pos + len(raw):pos + len(raw) + 90])
        qual = q.group(0).strip() if q else None
        if q and q["ext"]:
            out.append(_ref("annex", ident, raw, pos, qual, [], records, external=True))
            continue
        ident, app = ident.upper(), app.upper() if app else None
        label = f"Annex {ident}" + (f" Appendix {app}" if app else "")
        found = lk.find(label, "title")
        if not found:   # the annex heading may open a part / appendix directly ("Annex 1 - Part 1"): flag, not guess
            found = [j for j in lk.titles if records[j]["annex"] == ident and (not app or records[j]["appendix"] == app)]
        if found and all(j == i for j in found):
            continue    # the heading of an annex mentions its own annex
        found = [j for j in found if j != i]
        out.append(_ref("annex", label.replace("Annex ", "", 1), raw, pos, qual, found, records, "document"))
    for m in TABLE_REF_RE.finditer(text):
        q = QUALIFIER_RE.match(text[m.end():m.end() + 90])
        qual = q.group(0).strip() if q else None
        if re.search(r'\bto\b|\s[-–]\s', m.group(1)):
            ref = _ref('table', m.group(1), m.group(0), m.start(), qual, [], records,
                       external=bool(q and q['ext']))
            ref['reason'] = 'range_requires_expansion'
            out.append(ref)
            continue
        for tid in _split_ids(m.group(1)):
            if q and q['ext']:
                out.append(_ref('table', tid, m.group(0), m.start(), qual, [], records, external=True))
                continue
            if q and not q['reg'] and not q['here']:
                # Named-context table references need explicit context resolution.
                # Do not fall back to a same-number table in the current context.
                out.append(_ref('table', tid, m.group(0), m.start(), qual, [], records))
                continue
            tables = [j for j in lk.tables if records[j]["number"].endswith(f"Table {tid}")]
            if q and q['reg']:
                tables = [j for j in tables if not lk.paras[j].prefix]
            same = [j for j in tables if lk.paras[j].prefix == p.prefix]
            found, scope = (same, "same_context") if same else (tables, "document")
            if q and q['here']:
                found, scope = same, 'same_context'
            found = [j for j in found if j != i]
            out.append(_ref("table", tid, m.group(0), m.start(), qual, found, records, scope))
    out.sort(key=lambda x: (x["pos"], x["kind"]))
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Annex kinds and definitions
# ---------------------------------------------------------------------------------------------------------------------

FORM_RE = re.compile(r"\b(communication|notification|information (?:document|folder)|concerning\s*:|issued by\b"
                     r"|approval (?:granted|extended|refused|withdrawn))", re.I)
MARK_RE = re.compile(r"\b(approval marks?|arrangements? of (?:the )?(?:approval )?marks?|examples? of (?:the )?marks?"
                     r"|markings?)\b", re.I)
LEADER_RE = re.compile(r"(\.{4,}|…{2,}|_{4,})")


def annex_kinds(records: list[dict], paras: list) -> None:
    """annex_kind for every annex record: "communication_form", "marking_example" or "content" (a flag only).
    Decided per context (annex / appendix / part) from its titles, its lead-in text and its dotted fill-in leaders."""
    groups: dict[tuple, list[int]] = defaultdict(list)
    for i, r in enumerate(records):
        if r["annex"] is not None:
            groups[(r["annex"], r["appendix"], r["part"])].append(i)
    for idxs in groups.values():
        p = paras[idxs[0]]
        titles = " ".join(v[1] for v in p.titles.values())
        lead = " ".join(records[j]["text"] for j in idxs[:3])[:500]
        leaders = sum(1 for j in idxs if LEADER_RE.search(records[j]["text"]))
        if MARK_RE.search(titles):
            kind = "marking_example"
        elif FORM_RE.search(titles) or FORM_RE.search(lead) or (len(idxs) >= 3 and leaders / len(idxs) >= 0.4):
            kind = "communication_form"
        else:
            kind = "content"
        for j in idxs:
            records[j]["annex_kind"] = kind


DEF_HEAD_RE = re.compile(r"^(?:terms and )?definitions?\b|^definitions and (?:abbreviations|symbols)", re.I)
QUOTED_DEF_RE = re.compile(
    r"^\s*(?:\(?[a-z0-9]{1,3}\)\s*)?[\"“”„](?P<term>[^\"“”„]{1,150}?)\s*[\"“”]\s*(?:\((?P<abbr>[^)]{1,40})\)\s*)?,?\s*"
    r"(?P<verb>means|is|are|refers to|denotes|shall mean|has the meaning|:|-|–)?", re.I)
MEANS_DEF_RE = re.compile(r"^(?P<term>[A-Z][^.:;\"“”]{1,100}?)\s+(?:means|is defined as|denotes|refers to)\b")
COLON_DEF_RE = re.compile(r"^(?P<term>[A-Za-z][^:.;]{1,60}):\s+\S")


def definition_term(text: str) -> tuple[str | None, str | None, str | None]:
    """(term, method, abbreviation): '"Vehicle type" means ...' -> ("Vehicle type", "quoted", None)."""
    m = QUOTED_DEF_RE.match(text)
    if m:
        return " ".join(m["term"].split()), "quoted", m["abbr"]
    m = MEANS_DEF_RE.match(text)
    if m:
        return m["term"].strip(), "means", None
    m = COLON_DEF_RE.match(text)
    if m:
        return m["term"].strip(), "colon", None
    return None, None, None


def definitions(records: list[dict]) -> list[dict]:
    """definitions.jsonl rows: every paragraph under a "Definitions" heading paragraph (usually 2.), with its term.
    A row is kept even when no term is recognised (term = null), so nothing in the section is lost."""
    out = []
    by_id = {r["id"]: r for r in records}
    covered: set[str] = set()   # a "Definition of ..." paragraph inside a definitions section is not a new section
    for head in records:
        if head["kind"] != "paragraph" or head["id"] in covered \
                or not DEF_HEAD_RE.match(head["title"] or head["text"][:40]):
            continue
        stack, members = list(head["children_ids"]), []
        while stack:                       # all descendants, in document order
            rid = stack.pop(0)
            members.append(by_id[rid])
            stack[:0] = by_id[rid]["children_ids"]
        for r in members:
            covered.add(r["id"])
            if r["kind"] != "paragraph":
                continue
            term, method, abbr = definition_term(r["text"])
            out.append({"id": f"{r['id']}|def", "term": term, "term_method": method, "abbreviation": abbr,
                        "paragraph_id": r["id"], "paragraph": r["paragraph"], "definitions_section_id": head["id"],
                        "regulation": r["regulation"], "version": r["version"], "role": r["role"], "file": r["file"],
                        "page": r["page"], "page_end": r["page_end"], "text": r["text"]})
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Enrichment of one document's records
# ---------------------------------------------------------------------------------------------------------------------

def enrich(records: list[dict], paras: list, pitch: float) -> None:
    """Adds titles, links, signals, cross references and annex kinds to the records (in place).
    records[i] was built from paras[i]."""
    lk = _Lookup(records, paras)
    index_of = {id(p): i for i, p in enumerate(paras)}

    # ---- titles and heading / value items ----------------------------------------------------------------------------
    for r, p in zip(records, paras):
        r["obligation_signals"] = obligation_signals(r["text"])
        r["has_obligation"] = has_obligation(r["obligation_signals"])
        title, how, value = None, None, None
        if p.kind == "title":
            title, how = context_title(p), "context_heading"
        elif p.kind == "table":
            title, how = (" ".join(p.head.strip(" :.-–").split()) or None), "caption"
        else:
            title, how = paragraph_title(p, pitch)
        if how == "inline_label":
            value = r["text"].split(":", 1)[1].strip()
        elif how in ("heading_line", "bold_heading"):
            value = r["text"][len(p.head.strip()):].strip()
        r["title"] = title
        r["title_source"] = how if title else None
        # R48 style "6.14.2. Number" + "Two, ..." / "In height: Above the ground ...": a label with its value; the
        # obligation ("shall be fitted with") is implicit
        r["heading_value_item"] = bool(
            p.kind == "paragraph" and title and value and len(title.split()) <= 5
            and (how == "inline_label" or (how == "heading_line" and not has_obligation(obligation_signals(value)))))

    # ---- parent links (unique match only) -----------------------------------------------------------------------------
    parent_idx: list[int | None] = [None] * len(records)
    for i, (r, p) in enumerate(zip(records, paras)):
        status, cands, wanted = "root", [], None
        if p.kind == "table":
            parent_idx[i] = index_of.get(id(p.owner))
            status = "table_owner" if parent_idx[i] is not None else "not_found"
            wanted = p.owner.label
        else:
            if p.kind == "title":
                labels = [enclosing_prefix(p)] if enclosing_prefix(p) else []
            else:
                nums = p.number.split(".")
                labels = [f"{p.prefix} {'.'.join(nums[:k])}".strip() for k in range(len(nums) - 1, 0, -1)]
                if p.prefix:
                    labels.append(p.prefix)
            wanted = labels[0] if labels else None
            for n, label in enumerate(labels):
                before = [j for j in lk.by_label.get(label, ()) if j < i]   # a parent precedes its child
                same_ctx = [j for j in before if paras[j].ctx == p.ctx]
                if len(before) == 1 or len(same_ctx) == 1:
                    parent_idx[i] = before[0] if len(before) == 1 else same_ctx[0]
                    status = "ok" if n == 0 else "skipped_level"
                    break
                if before:
                    status, cands = "ambiguous", [records[j]["id"] for j in before[:10]]
                    break
            else:
                status = "not_found" if labels else "root"
        j = parent_idx[i]
        r["parent"] = records[j]["paragraph"] if j is not None else wanted
        r["parent_id"] = records[j]["id"] if j is not None else None
        r["parent_status"] = status
        if cands:
            r["parent_candidates"] = cands
    chains: list[list[int]] = []
    for i in range(len(records)):
        chain, j = [], parent_idx[i]
        while j is not None and j not in chain:
            chain.append(j)
            j = parent_idx[j]
        chains.append(chain)
        records[i]["parent_chain"] = [records[k]["id"] for k in chain]
    for r in records:
        r["children_ids"] = []
    for i, j in enumerate(parent_idx):
        if j is not None:
            records[j]["children_ids"].append(records[i]["id"])

    # ---- breadcrumbs ------------------------------------------------------------------------------------------------
    for i, (r, p) in enumerate(zip(records, paras)):
        if p.kind == "title":
            crumbs = context_crumbs(p)[:-1]
        else:
            # a table entry's breadcrumb runs through the paragraph that owns the table, that paragraph included
            q = p.owner if p.kind == "table" else p
            crumbs = context_crumbs(q)
            titles = {records[j]["number"]: records[j]["title"] for j in chains[i] if records[j]["kind"] == "paragraph"}
            nums = q.number.split(".") if q.number else []
            for k in range(1, len(nums) + (1 if p.kind == "table" else 0)):
                num = ".".join(nums[:k])
                t = titles.get(num)
                crumbs.append(f"{num} {t}" if t else num)
        r["breadcrumb"] = crumbs

    # ---- inherited obligations and conditions (parents come before their children) -----------------------------------
    for i, r in enumerate(records):
        r["inherits_obligation"], r["inherits_obligation_from"], r["inherits_obligation_reason"] = False, None, None
        if r["has_obligation"]:
            continue
        j = parent_idx[i]
        src = reason = None
        if j is not None:
            pr = records[j]
            if pr["has_obligation"]:
                src, reason = j, "parent_obligation"
            elif pr["text"].rstrip().endswith(":"):
                src, reason = j, "parent_introduces_list"
            elif pr["inherits_obligation"]:
                src, reason = None, "ancestor_inherits"
                r["inherits_obligation_from"] = pr["inherits_obligation_from"]
        if reason is None:
            titled = next((k for k in chains[i] if records[k]["title"]), None)
            if titled is not None and records[titled]["has_obligation"]:
                src, reason = titled, "titled_ancestor_obligation"
        if reason:
            r["inherits_obligation"], r["inherits_obligation_reason"] = True, reason
            if src is not None:
                r["inherits_obligation_from"] = records[src]["id"]
    for i, r in enumerate(records):
        r["condition_signals"] = condition_signals(r["text"])
    for i, r in enumerate(records):
        r["inherited_condition_ids"] = [records[k]["id"] for k in chains[i] if records[k]["condition_signals"]]

    # ---- cross references, annex kinds ------------------------------------------------------------------------------
    for i, r in enumerate(records):
        r["cross_refs"] = resolve_refs(i, lk)
        r["annex_kind"] = None
    annex_kinds(records, paras)
