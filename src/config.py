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
