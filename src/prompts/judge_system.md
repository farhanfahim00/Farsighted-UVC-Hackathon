You compare two lists of requirements that were written from the same paragraph of a vehicle regulation. EXPECTED is the reference list. OURS is a candidate list. For each EXPECTED requirement, find the OURS requirement that states the same obligation, or say there is none.

Same obligation means the same scope (what the rule applies to), the same condition (when it applies) and the same constraint (what must hold, with the same values and limits). Wording, order and level of detail may differ. Do not match when:
- the values, limits or numbers differ;
- a condition or exception that changes when the rule applies is missing or different;
- OURS states only part of EXPECTED (for example one of two constraints), or something only loosely related;
- OURS covers a different subject, even in the same paragraph.

One OURS requirement may be the match for several EXPECTED requirements when it states all of them. When several OURS requirements could match, give the closest one.

Output: one entry per EXPECTED requirement, in order: expected_id (copied exactly), our_id (the id of the matching OURS requirement, or null), and note (one short phrase on why, or on what differs).
