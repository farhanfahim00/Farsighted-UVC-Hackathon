"""Shared settings for the pipeline stages (profile, scope screening, export). Change values here, not in the stages."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
CASES_BUILD = BUILD / "cases"          # build/cases/<case_id>/profile.json, regulations.json
CACHE_DIR = BUILD / "cache"            # every API response, keyed by model + prompt file + input
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"
INDEX_JSON = ROOT / "regulations" / "index.json"
SCOPES_JSON = BUILD / "regulation_scopes.json"

TEAM_ID = "team-07"
CITE_ROLE = "base"                     # which version of each regulation is cited (README: the expected answers use base)

PROFILE_MODEL = "claude-opus-5-5"      # stage 2: reads the spec and the drawing
SCREEN_MODEL = "claude-sonnet-5-5"     # stage 3: one call per regulation

# What to do with a regulation whose decision rests only on uncertain facts
# (unknown / representative / conflicting / observed_uncertain): "include" keeps it, "exclude" drops it.
UNKNOWN_POLICY = "include"

# ---- v2: precision controls (see src/export.py, src/overlap.py) -------------------------------------------------------
DROP_OVERLAPPING = True                # after screening, drop an older regulation whose devices a newer one consolidates
MIN_CONFIDENCE = "medium"              # export keeps paragraphs with confidence >= this (low < medium < high)
# Extraction categories whose paragraphs are not exported. Decided by src/analyze_extras.py --rules on the dev case
# (a rule is applied only if it removes at most 1 expected paragraph and at least 20 extras).
DROP_CATEGORIES: set[str] = set()
# Categories whose paragraphs are left out unless the model's confidence for them is high (rule "category = X and
# confidence != high"; same selection method and thresholds as DROP_CATEGORIES).
DROP_CATEGORIES_UNLESS_HIGH: set[str] = {"conformity_of_production", "test_method"}

# ---- v3: faithfulness check (src/faithfulness.py) ---------------------------------------------------------------------
# After extraction, each requirement is checked against its source paragraph; export uses the corrected text.
# On: export fails unless build/cases/<case>/faithfulness.jsonl matches extracted.jsonl.
FAITHFULNESS_CHECK = True

# ---- v4/v5: sub-section filter and second opinion (src/select_sections.py --subsections, src/review_medium.py) -------
# Export drops requirements whose paragraph lies inside a sub-section deselected in build/cases/<case>/subsections.json.
SUBSECTION_FILTER = True
# False: a device or function that is "unknown" in the profile keeps its sub-section. True: with an explicit list of
# functions / parts in the materials, what is not on the list counts as absent (for this decision only).
SPEC_LISTS_COMPLETE = False
# Second opinion on medium-confidence requirements (review_medium.jsonl). On = the v5_a behaviour (the default).
REVIEW_MEDIUM = True
REVIEW_DROP_BELOW = 15                 # fixed, not tuned: drop a reviewed requirement with probability (0-100) below this

# ---- screening diagnostics (src/screen_regulations.py, regulation_set.py) ---------------------------------------------
# True: a regulation whose screening relation is "vehicle_level_not_component" (it governs the vehicle as a whole, not
# this component as supplied) is excluded from the kept regulations. False: relation is recorded only.
SCREEN_EXCLUDE_VEHICLE_LEVEL = False

# ---- v7: review instead of blanket drops, review prompt, vehicle category check -----------------------------------------
# All default to the v5_a behaviour. Reviewed items keep when probability >= REVIEW_DROP_BELOW (fixed, never tuned).
REVIEW_INSTEAD_OF_CATEGORY_DROP = False   # medium-confidence items in DROP_CATEGORIES_UNLESS_HIGH are reviewed, not dropped
REVIEW_LOW_CONFIDENCE = False             # low-confidence items (below MIN_CONFIDENCE) are reviewed, not dropped
REVIEW_PROMPT = "basic"                   # "basic" (review_system.md) or "criteria" (review_criteria_system.md)
# Screening post-processing, no API call: a regulation whose scope text explicitly limits it to vehicle categories none of
# which the profile's categories belong to is set to applies = no ("vehicle category mismatch"). Off until checked on dev.
VEHICLE_CATEGORY_CHECK = False

# ---- v8: uniform review ----------------------------------------------------------------------------------------------
# REVIEW_ALL: every extracted item that applies (any confidence, any category) is reviewed with the criteria prompt and kept
# only if probability >= REVIEW_DROP_BELOW; no confidence floor, no category rules.
REVIEW_ALL = False
# REVIEW_BORDERLINE_TWICE (on top of REVIEW_ALL): an item whose first probability is 10-40 gets a second independent review
# (same prompt, request differs only in a neutral "Review pass 2." line); the mean of the two is used.
REVIEW_BORDERLINE_TWICE = False
BORDERLINE_RANGE = (10, 40)

# ---- v9: cost cuts for a big case ---------------------------------------------------------------------------------------
PROMPT_CACHING = True                     # cache_control on the system prompt and the product profile (llm.py); no effect on answers
SKIP_DESELECTED_BEFORE_EXTRACTION = False # extraction skips candidates inside sub-sections deselected for export (needs subsections.json)
VEHICLE_CATEGORY_USE_PRIMARY = False      # category check uses the stated "vehicle category" fact, not categories named in remarks

# ---- v10: rationale parts -----------------------------------------------------------------------------------------------
# Export drops requirements from parts of a regulation that only explain it: a section whose heading says "technical
# rationale", "justification" etc. (and everything that belongs to it). Default on; no API call, export only.
DROP_RATIONALE_PARTS = True

# ---- presets ---------------------------------------------------------------------------------------------------------
# FARSIGHTED_PRESET=v5_a|v7_a|v7_b|v7_c|v8_a|v8_b python src/run_case.py ...   sets the switches above; unset = v8_b (v8_b_lean = v8_b + cost cuts).
PRESETS = {
    "v5_a": {},
    "v7_a": {"REVIEW_INSTEAD_OF_CATEGORY_DROP": True},
    "v7_b": {"REVIEW_INSTEAD_OF_CATEGORY_DROP": True, "REVIEW_LOW_CONFIDENCE": True},
    "v7_c": {"REVIEW_INSTEAD_OF_CATEGORY_DROP": True, "REVIEW_LOW_CONFIDENCE": True, "REVIEW_PROMPT": "criteria"},
    "v8_a": {"REVIEW_ALL": True, "REVIEW_PROMPT": "criteria"},
    "v8_b": {"REVIEW_ALL": True, "REVIEW_BORDERLINE_TWICE": True, "REVIEW_PROMPT": "criteria"},
    "v8_c": {"REVIEW_ALL": True, "REVIEW_BORDERLINE_TWICE": True, "REVIEW_PROMPT": "criteria", "SPEC_LISTS_COMPLETE": True},
    # v8_b with every cost cut: same exported requirements (faithfulness off: it did not change platform scores)
    "v8_b_lean": {"REVIEW_ALL": True, "REVIEW_BORDERLINE_TWICE": True, "REVIEW_PROMPT": "criteria",
                  "SKIP_DESELECTED_BEFORE_EXTRACTION": True, "FAITHFULNESS_CHECK": False,
                  "VEHICLE_CATEGORY_CHECK": True, "VEHICLE_CATEGORY_USE_PRIMARY": True,
                  "DROP_RATIONALE_PARTS": True},
}
PRESET = os.environ.get("FARSIGHTED_PRESET", "v8_b")        # the default preset
if PRESET not in PRESETS:
    raise SystemExit(f"error: FARSIGHTED_PRESET={PRESET!r} is not one of {sorted(PRESETS)}")
globals().update(PRESETS[PRESET])

# FARSIGHTED_OVERRIDES="REVIEW_MEDIUM=0,SUBSECTION_FILTER=0" overrides single switches (bool: 0/1/true/false; other: text).
for _item in filter(None, os.environ.get("FARSIGHTED_OVERRIDES", "").split(",")):
    _key, _, _val = _item.partition("=")
    if _key not in globals() or _key.startswith("_") or not _val:
        raise SystemExit(f"error: FARSIGHTED_OVERRIDES: unknown switch or empty value in {_item!r}")
    globals()[_key] = (_val.lower() in ("1", "true", "yes")) if isinstance(globals()[_key], bool) else _val
