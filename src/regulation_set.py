"""Which regulations are kept for a case, derived without any API call from the stage 3 decisions (regulations.json)
and the overlap decisions (overlap.json). The same function serves the pipeline and the export variants:

    final_decisions(case_id)                                   # config defaults
    final_decisions(case_id, unknown_policy="exclude")         # uncertain-only decisions are dropped instead of kept
    final_decisions(case_id, drop_overlap=False)

Order of the rules: UNKNOWN_POLICY, then supersession (both from stage 3), then overlap (stage 3b).
"""

from __future__ import annotations

import copy
import json

import config
from screen_regulations import apply_supersession, apply_unknown_policy, order

# notes added by the post-processing steps; they are rebuilt, so they are removed before re-applying
DERIVED_NOTES = ("model said", "no answer from the model", "dropped:", "kept over", "conflicting supersession",
                 "dropped by overlap", "kept; replaces")


def final_decisions(case_id: str, unknown_policy: str | None = None, drop_overlap: bool | None = None) -> list[dict]:
    unknown_policy = unknown_policy or config.UNKNOWN_POLICY
    drop_overlap = config.DROP_OVERLAPPING if drop_overlap is None else drop_overlap
    case_dir = config.CASES_BUILD / case_id
    decisions = copy.deepcopy(json.loads((case_dir / "regulations.json").read_text(encoding="utf-8"))["decisions"])
    for d in decisions:                                     # back to the model's own answer
        d["applies"] = None if d["failure"] else d["model_applies"]
        d["decision_source"] = "model"
        d["notes"] = [n for n in d["notes"] if not n.startswith(DERIVED_NOTES)]
    apply_unknown_policy(decisions, unknown_policy)
    apply_supersession(decisions)
    overlap_path = case_dir / "overlap.json"
    if drop_overlap and overlap_path.is_file():
        by_name = {d["regulation"]: d for d in decisions}
        for older, info in json.loads(overlap_path.read_text(encoding="utf-8"))["dropped"].items():
            d = by_name.get(older)
            if d is not None and d["applies"]:
                d.update(applies=False, decision_source="overlap")
                d["notes"].append(f"dropped by overlap: replaced by {info['newer']} ({info['reason']})")
    return order(decisions)


def kept_regulations(case_id: str, **kw) -> list[str]:
    return [d["regulation"] for d in final_decisions(case_id, **kw) if d["applies"]]
