#!/usr/bin/env python3
"""Evaluation side only: where do the extra (not expected) paragraphs come from, which general rules would remove
them, and does our splitting match the experts'. Reads expected.json; the pipeline never does.

    python src/analyze_extras.py                 # step 1: expected vs ours, grouped
    python src/analyze_extras.py --rules         # step 3: candidate rules with the paragraphs they would remove
    python src/analyze_extras.py --splitting     # step 5: requirement counts and wording per found paragraph

"Our paragraphs" are paragraphs with at least one requirement in extracted.jsonl, for the regulations that are in
expected.json and kept by the pipeline. "Extras" are ours that are not expected.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import config

sys.path.insert(0, str(config.ROOT / "tools"))
from score_local import _paragraph_key  # noqa: E402

from select_sections import load_base_records  # noqa: E402

CASE = "dev-rear-lamp"
COP_RE = re.compile(r"conformity of production|production control|sampling by an? inspector|minimum requirements for", re.I)
ADMIN_RE = re.compile(r"transitional provisions|modification and extension|names and addresses|approval and|"
                      r"production definitively discontinued|application for approval", re.I)


class Data:
    def __init__(self, case_id: str, expected_path: Path):
        d = config.CASES_BUILD / case_id
        exp = json.loads(expected_path.read_text(encoding="utf-8"))["requirements"]
        self.exp_reqs = exp
        self.gold: dict[tuple, list[str]] = defaultdict(list)
        for r in exp:
            self.gold[(r["source"]["regulation"], _paragraph_key(r["source"].get("paragraph")))].append(r["text"])
        self.regs = {k[0] for k in self.gold}
        self.rows = [json.loads(l) for l in (d / "extracted.jsonl").read_text(encoding="utf-8").splitlines() if l]
        self.base = load_base_records(self.regs)
        self.rec = {r["id"]: r for v in self.base.values() for r in v}
        sections = json.loads((d / "sections.json").read_text(encoding="utf-8"))["regulations"]
        self.unit_title = {(s["regulation"], u["id"]): u["title"] for s in sections for u in s["outline"]}
        self.row_by_id = {r["id"]: r for r in self.rows}
        # every paragraph we produced requirements for, in the regulations of expected.json
        self.ours = [r for r in self.rows if r["requirements"] and r["regulation"] in self.regs]
        self.ours_keys = {self.key(r) for r in self.ours}

    def key(self, row: dict) -> tuple:
        return (row["regulation"], _paragraph_key(self.rec[row["id"]]["paragraph"]))

    def is_gold(self, row: dict) -> bool:
        return self.key(row) in self.gold

    def annex_title(self, row: dict) -> str:
        crumbs = [c for c in self.rec[row["id"]]["breadcrumb"] if re.match(r"(Annex|Appendix|Part)\b", c)]
        return " / ".join(c[:50] for c in crumbs[:2]) or "(main body)"

    def section_title(self, row: dict) -> str:
        return f"{row['unit']} {self.unit_title.get((row['regulation'], row['unit']), '')[:40]}"


def group_table(data: Data, name: str, group_of) -> None:
    """group -> expected paragraphs (all expected paragraphs in the group), our paragraphs, extras."""
    exp, ours, extra = Counter(), Counter(), Counter()
    for key, texts in data.gold.items():
        recs = [r for r in data.base[key[0]] if _paragraph_key(r["paragraph"]) == key[1]]
        if recs and recs[0]["id"] in data.row_by_id:
            exp[group_of(data.row_by_id[recs[0]["id"]])] += 1
        else:
            exp["(not extracted)"] += 1
    for row in data.ours:
        g = group_of(row)
        ours[g] += 1
        extra[g] += not data.is_gold(row)
    print(f"\n-- by {name} --")
    print(f"  {'group':<62} {'expected':>8} {'ours':>6} {'extras':>7}")
    for g in sorted(set(exp) | set(ours), key=lambda x: (-extra[x], str(x))):
        print(f"  {str(g)[:62]:<62} {exp[g]:>8} {ours[g]:>6} {extra[g]:>7}")
    print(f"  {'TOTAL':<62} {sum(exp.values()):>8} {sum(ours.values()):>6} {sum(extra.values()):>7}")


def step1(data: Data) -> None:
    print(f"Regulations in expected.json: {sorted(data.regs)}; expected paragraphs {len(data.gold)}; "
          f"ours {len(data.ours_keys)}; extras {len(data.ours_keys - set(data.gold))}")
    group_table(data, "top-level section (regulation, unit, title)", lambda r: (r["regulation"], data.section_title(r)))
    group_table(data, "annex / part title", lambda r: (r["regulation"], data.annex_title(r)))
    group_table(data, "annex_kind", lambda r: data.rec[r["id"]]["annex_kind"] or "(main body)")
    group_table(data, "extraction category", lambda r: r["category"])
    group_table(data, "confidence", lambda r: r["confidence"])
    group_table(data, "concerns a device/function the profile does not list (model: other_device_type)",
                lambda r: "other_device_type" if r["category"] == "other_device_type" else "listed in profile or general")


# ---------------------------------------------------------------------------------------------------------------------
# Step 3: candidate rules
# ---------------------------------------------------------------------------------------------------------------------

def candidate_rules(data: Data) -> list[tuple[str, str, object]]:
    """(name, kind, predicate(row) -> bool). A rule removes the paragraphs for which the predicate is true.
    Every rule is stated in type-approval terms, not in terms of this case."""
    rules = []
    for cat in ("conformity_of_production", "administrative", "other_device_type", "test_method", "test_requirement",
                "installation_requirement", "device_requirement", "other"):
        rules.append((f"category = {cat}", "category", lambda r, c=cat: r["category"] == c))
    for conf in ("low",):
        rules.append((f"confidence = {conf}", "confidence", lambda r, c=conf: r["confidence"] == c))
    # a category the model is not highly sure about (production, test method and administrative text are checks on
    # the manufacturer or the test house rather than design requirements, unless the paragraph clearly applies)
    for cat in ("conformity_of_production", "administrative", "test_method"):
        rules.append((f"category = {cat} and confidence != high", "combined",
                      lambda r, c=cat: r["category"] == c and r["confidence"] != "high"))
    rules.append(("annex_kind = marking_example", "structure",
                  lambda r: data.rec[r["id"]]["annex_kind"] == "marking_example"))
    rules.append(("conformity-of-production title (annex or section)", "structure",
                  lambda r: bool(COP_RE.search(" ".join(data.rec[r["id"]]["breadcrumb"]) + " "
                                               + data.unit_title.get((r["regulation"], r["unit"]), "")))))
    rules.append(("administrative title (annex or section)", "structure",
                  lambda r: bool(ADMIN_RE.search(" ".join(data.rec[r["id"]]["breadcrumb"]) + " "
                                                 + data.unit_title.get((r["regulation"], r["unit"]), "")))))
    return rules


def step3(data: Data, max_expected: int = 1, min_extras: int = 20) -> set[str]:
    print(f"\nCandidate rules (a rule is applied only if it removes <= {max_expected} expected paragraph and "
          f">= {min_extras} extras)")
    print(f"  {'rule':<52} {'removes expected':>16} {'removes extras':>14} {'reqs removed':>12}   decision")
    passing_categories, passing_combined, passing_all = set(), set(), []
    rules = candidate_rules(data)
    for name, kind, pred in rules:
        removed = [r for r in data.ours if pred(r)]
        e = sum(data.is_gold(r) for r in removed)
        x = len(removed) - e
        ok = e <= max_expected and x >= min_extras
        print(f"  {name:<52} {e:>16} {x:>14} {sum(len(r['requirements']) for r in removed):>12}   "
              f"{'APPLY' if ok else 'reject'}")
        if ok:
            passing_all.append((name, pred))
            if kind == "category":
                passing_categories.add(name.split(" = ")[1])
            elif kind == "combined":
                passing_combined.add(name.split(" = ")[1].split(" and ")[0])
    left = [r for r in data.ours if not any(pred(r) for _, pred in passing_all)]
    hit = sum(data.is_gold(r) for r in left)
    print(f"\nWith every applied rule: {len(left)} paragraphs / {sum(len(r['requirements']) for r in left)} requirements "
          f"remain; expected paragraphs kept {hit}/{len(data.gold)} (before rules: "
          f"{sum(data.is_gold(r) for r in data.ours)}); extras {len(left) - hit}")
    print(f"\nsuggested config.DROP_CATEGORIES = {sorted(passing_categories)}")
    print(f"suggested config.DROP_CATEGORIES_UNLESS_HIGH = {sorted(passing_combined)}")
    structural = [n for n, k, _ in rules if k == "structure" and n in {a for a, _ in passing_all}]
    if structural:
        print(f"structural rules that also pass (not wired into export; the categories already cover them): {structural}")
    return passing_categories


# ---------------------------------------------------------------------------------------------------------------------
# Step 5: splitting
# ---------------------------------------------------------------------------------------------------------------------

def tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def jaccard(a: str, b: str) -> float:
    x, y = tokens(a), tokens(b)
    return len(x & y) / len(x | y) if x | y else 0.0


def step5(data: Data) -> None:
    found = [k for k in data.gold if k in data.ours_keys]
    ours = defaultdict(list)
    for r in data.ours:
        ours[data.key(r)].extend(r["requirements"])
    print(f"\nSplitting check on the {len(found)} expected paragraphs we found "
          f"(expected requirements {sum(len(data.gold[k]) for k in found)}, ours {sum(len(ours[k]) for k in found)})")
    matrix = Counter()
    kinds = Counter()
    examples = defaultdict(list)
    for k in found:
        e, o = len(data.gold[k]), len(ours[k])
        matrix[(min(e, 4), min(o, 4))] += 1
        kind = "same" if e == o else ("over-split (ours > expected)" if o > e else
                                      ("merged (ours 1, expected >1)" if o == 1 else "under-split (ours < expected)"))
        kinds[kind] += 1
        examples[kind].append((k, e, o))
    print("  rows: expected requirements per paragraph (4 = 4+), columns: ours (4 = 4+), cells: paragraphs")
    print("        ours:  " + "".join(f"{c:>6}" for c in range(0, 5)))
    for e in range(1, 5):
        print(f"  expected {e}:  " + "".join(f"{matrix[(e, o)]:>6}" for o in range(0, 5)))
    print("  " + ", ".join(f"{k}: {v}" for k, v in kinds.items()))
    for kind, ex in examples.items():
        if kind != "same":
            print(f"  e.g. {kind}: " + "; ".join(f"{k[0]} {k[1]} (exp {e}, ours {o})" for k, e, o in ex[:6]))

    # wording: does each expected requirement have a close one among ours for the same paragraph?
    matched = total = 0
    unmatched = []
    for k in found:
        for text in data.gold[k]:
            total += 1
            best = max((jaccard(text, o) for o in ours[k]), default=0.0)
            if best >= 0.5:
                matched += 1
            else:
                unmatched.append((best, k, text))
    print(f"\n  expected requirements with a close match (token overlap >= 0.5) among ours for the same paragraph: "
          f"{matched}/{total} = {matched / total:.1%}   (a rough proxy for the platform's judge)")
    wexp = [len(t.split()) for k in found for t in data.gold[k]]
    wour = [len(t.split()) for k in found for t in ours[k]]
    print(f"  mean words per requirement: expected {sum(wexp) / len(wexp):.1f}, ours {sum(wour) / len(wour):.1f}")
    for best, k, text in sorted(unmatched)[:8]:
        print(f"  UNMATCHED {k[0]} {data.gold[k] and ''}{[r for r in data.base[k[0]] if _paragraph_key(r['paragraph']) == k[1]][0]['paragraph']} "
              f"(best overlap {best:.2f})\n      expected: {text[:200]}\n      ours:     "
              f"{(max(ours[k], key=lambda o: jaccard(text, o)) if ours[k] else '')[:200]}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--case", default=CASE)
    ap.add_argument("--expected", default=str(config.ROOT / "cases" / "dev-rear-lamp" / "expected.json"))
    ap.add_argument("--rules", action="store_true")
    ap.add_argument("--splitting", action="store_true")
    args = ap.parse_args(argv)
    data = Data(args.case, Path(args.expected))
    if args.rules:
        step3(data)
    elif args.splitting:
        step5(data)
    else:
        step1(data)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
