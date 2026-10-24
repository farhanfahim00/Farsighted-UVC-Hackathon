"""Shared settings for the pipeline stages (profile, scope screening, export). Change values here, not in the stages."""

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
DROP_CATEGORIES_UNLESS_HIGH: set[str] = {"conformity_of_production"}
