#!/usr/bin/env python3
"""Stage 3: scope screening. One SCREEN_MODEL call per regulation decides, from the product profile and that
regulation's title and scope text, whether the regulation applies. Python then applies UNKNOWN_POLICY and the
supersession rule, and writes the decisions with the version to cite.

    python src/screen_regulations.py cases/dev-rear-lamp     # needs build/cases/<case_id>/profile.json

Input:  build/cases/<case_id>/profile.json, build/regulation_scopes.json, regulations/index.json
Output: build/cases/<case_id>/regulations.json   (applying regulations first; version = the CITE_ROLE version label)

Recall comes first: a failed call, or a decision that rests only on uncertain facts, keeps the regulation in when
UNKNOWN_POLICY is "include". Nothing here depends on the product or on any regulation's name.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import config
from llm import LLMFailure, call_json, object_schema, usage_summary

UNCERTAIN = {"unknown", "representative", "conflicting", "observed_uncertain"}
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
WORKERS = 6

S = {"type": "string"}
LIST_S = {"type": "array", "items": S}
DECISION_SCHEMA = object_schema({
    "applies": {"type": "string", "enum": ["yes", "no"]},
    "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
    "level": {"type": "string", "enum": ["product_itself", "installation_on_vehicle", "cross_cutting"]},
    "depends_on": LIST_S,
    "depends_only_on_uncertain": {"type": "boolean"},
    "supersession_note": S,
    "supersedes": LIST_S,
    "superseded_by": LIST_S,
    "reason": S,
})


# ---------------------------------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------------------------------

def load_regulations() -> list[dict]:
    """[{regulation, title, version}] from regulations/index.json; version = the CITE_ROLE version label."""
    out = []
    for e in json.loads(config.INDEX_JSON.read_text(encoding="utf-8")):
        version = next((v["version_label"] for v in e["versions"] if v.get("role") == config.CITE_ROLE), None)
        out.append({"regulation": e["regulation"], "title": e.get("title") or "", "version": version})
    return out


def natural_key(name: str) -> list:
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", name)]


def decision_content(profile: dict, reg: dict, scope_text: str, all_regs: list[dict]) -> list[dict]:
    listing = "\n".join(f"- {r['regulation']}: {r['title']}" for r in all_regs)
    text = (f"PRODUCT PROFILE (JSON)\n{json.dumps(profile, ensure_ascii=False)}\n\n"
            f"REGULATION: {reg['regulation']}\nTITLE: {reg['title']}\n"
            f"SCOPE TEXT:\n{scope_text or '(no scope text available; decide from the title, with low confidence)'}\n\n"
            f"ALL REGULATIONS UNDER CONSIDERATION (for supersedes / superseded_by):\n{listing}\n\n"
            f"Decide whether {reg['regulation']} applies to this product.")
    return [{"type": "text", "text": text}]


# ---------------------------------------------------------------------------------------------------------------------
# Post-processing (pure Python, no API)
# ---------------------------------------------------------------------------------------------------------------------

def feature_statuses(profile: dict) -> dict[str, set[str]]:
    """feature name (lower case) -> set of statuses it appears with in the profile."""
    out: dict[str, set[str]] = {}
    for f in profile.get("facts", []):
        out.setdefault(f["feature"].lower(), set()).add(f["status"])
    return out


def clean_decision(reg: dict, raw: dict | None, failure: str | None, profile: dict,
                   known_names: set[str]) -> dict:
    """One decision record. `raw` is the model's answer (None after a failed call)."""
    d = {"regulation": reg["regulation"], "title": reg["title"], "version": reg["version"], "role": config.CITE_ROLE,
         "applies": None, "model_applies": None, "confidence": "low", "level": "product_itself", "depends_on": [],
         "depends_only_on_uncertain": False, "supersession_note": "", "supersedes": [], "superseded_by": [],
         "reason": "", "decision_source": "model", "notes": [], "failure": failure}
    if reg["version"] is None:
        d["notes"].append(f"no {config.CITE_ROLE} version in regulations/index.json")
    if raw is None:
        d["reason"] = f"(no model answer: {failure})"
        return d
    statuses = feature_statuses(profile)
    d["model_applies"] = d["applies"] = raw["applies"] == "yes"
    d.update(confidence=raw["confidence"], level=raw["level"], reason=" ".join(raw["reason"].split()),
             supersession_note=" ".join(raw["supersession_note"].split()))
    # depends_on: keep the profile's own feature names; report names the profile does not contain
    matched, unmatched = [], []
    for name in raw["depends_on"]:
        (matched if name.lower() in statuses else unmatched).append(name)
    d["depends_on"] = [n for n in matched] + unmatched
    if unmatched:
        d["notes"].append(f"depends_on names not in the profile: {unmatched}")
    model_flag = bool(raw["depends_only_on_uncertain"])
    if raw["depends_on"] and not unmatched:
        # every supporting fact must be uncertain under every status it appears with
        flag = all(statuses[n.lower()] <= UNCERTAIN for n in matched)
        if flag != model_flag:
            d["notes"].append(f"depends_only_on_uncertain: model said {model_flag}, profile says {flag}; profile used")
    else:
        flag = model_flag and bool(raw["depends_on"])
    d["depends_only_on_uncertain"] = flag
    d["supersedes"] = [n for n in raw["supersedes"] if n in known_names and n != reg["regulation"]]
    d["superseded_by"] = [n for n in raw["superseded_by"] if n in known_names and n != reg["regulation"]]
    bad = [n for n in raw["supersedes"] + raw["superseded_by"] if n not in known_names or n == reg["regulation"]]
    if bad:
        d["notes"].append(f"ignored supersession names that are not regulations in the index: {bad}")
    return d


def apply_unknown_policy(decisions: list[dict], policy: str) -> None:
    """A decision that rests only on uncertain facts is settled by the policy ("include" keeps the regulation in,
    "exclude" drops it). Failed calls follow the same policy."""
    keep = policy == "include"
    for d in decisions:
        if d["failure"]:
            d.update(applies=keep, decision_source="policy_after_failure")
            d["notes"].append(f"no answer from the model; UNKNOWN_POLICY={policy}")
        elif d["depends_only_on_uncertain"] and d["applies"] != keep:
            d.update(applies=keep, decision_source="unknown_policy")
            d["notes"].append(f"model said {'yes' if d['model_applies'] else 'no'} on uncertain facts only; "
                              f"UNKNOWN_POLICY={policy} makes it {'yes' if keep else 'no'}")


def apply_supersession(decisions: list[dict]) -> None:
    """If two applying regulations cover the same device and one's scope says it replaces the other, keep the newer
    (the replacing one) and drop the replaced one, with the reason. A claim in both directions is ignored."""
    by_name = {d["regulation"]: d for d in decisions}
    claims = {(d["regulation"], old) for d in decisions for old in d["supersedes"]}
    claims |= {(new, d["regulation"]) for d in decisions for new in d["superseded_by"]}   # (newer, older)
    for newer, older in sorted(claims):
        if (older, newer) in claims:
            by_name[newer]["notes"].append(f"conflicting supersession claims with {older}; both kept")
            continue
        n, o = by_name[newer], by_name[older]
        if n["applies"] and o["applies"]:
            o.update(applies=False, decision_source="superseded")
            o["notes"].append(f"dropped: replaced by {newer} for the same devices "
                              f"({n['supersession_note'] or o['supersession_note'] or 'scope says so'})")
            n["notes"].append(f"kept over {older}, which it replaces")


def order(decisions: list[dict]) -> list[dict]:
    """Applying regulations first, then by confidence, then by regulation number."""
    return sorted(decisions, key=lambda d: (not d["applies"], CONFIDENCE_RANK[d["confidence"]],
                                            natural_key(d["regulation"])))


# ---------------------------------------------------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------------------------------------------------

def screen(profile: dict, regs: list[dict], scopes: dict, policy: str = config.UNKNOWN_POLICY) -> list[dict]:
    """Run the per-regulation calls (thread pool) and the post-processing; returns ordered decision records."""
    known = {r["regulation"] for r in regs}

    def one(reg: dict) -> dict:
        scope = scopes.get(reg["regulation"], {}).get("text", "")
        try:
            raw, _ = call_json(config.SCREEN_MODEL, "screen_system.md", decision_content(profile, reg, scope, regs),
                               DECISION_SCHEMA, label=f"screen:{reg['regulation']}", max_tokens=2000)
            return clean_decision(reg, raw, None, profile, known)
        except LLMFailure as exc:
            return clean_decision(reg, None, str(exc), profile, known)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        decisions = list(pool.map(one, regs))      # map keeps the input order
    apply_unknown_policy(decisions, policy)
    apply_supersession(decisions)
    return order(decisions)


def screen_case(case_id: str, out_dir: Path | None = None) -> dict:
    out_dir = out_dir or config.CASES_BUILD / case_id
    profile = json.loads((out_dir / "profile.json").read_text(encoding="utf-8"))
    model_profile = {k: v for k, v in profile.items() if k != "meta"}   # the model never sees our bookkeeping
    scopes = json.loads(config.SCOPES_JSON.read_text(encoding="utf-8"))
    decisions = screen(model_profile, load_regulations(), scopes)
    result = {
        "case_id": case_id, "cite_role": config.CITE_ROLE, "unknown_policy": config.UNKNOWN_POLICY,
        "model": config.SCREEN_MODEL,
        "counts": {"regulations": len(decisions), "applies": sum(d["applies"] for d in decisions),
                   "failures": sum(1 for d in decisions if d["failure"])},
        "decisions": decisions,
    }
    (out_dir / "regulations.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                                              encoding="utf-8")
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("case_dir", help="a case folder, e.g. cases/dev-rear-lamp (only its name is used)")
    args = ap.parse_args(argv)
    try:
        result = screen_case(Path(args.case_dir).name)
    except (OSError, LLMFailure) as exc:
        print(f"error: screening failed: {exc}", file=sys.stderr)
        return 1
    c = result["counts"]
    print(f"screened {c['regulations']} regulations: {c['applies']} apply, {c['failures']} failed calls")
    print(usage_summary())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
