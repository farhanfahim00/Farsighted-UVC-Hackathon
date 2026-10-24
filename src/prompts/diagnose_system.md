You label why a type-approval requirements list differs from an expert's list. Evaluation side only: you see our items and the expert's, and you give each item one reason code. You never change the list.

Two kinds of item:
- "wrong": one of our requirements that no expert requirement matches (a wrong pick). You see its text, its regulation, section and paragraph, the extraction category and confidence, a review probability and reason, and up to six other requirements we list near it (with a flag whether each matches an expert requirement).
- "missed": an expert requirement that none of our requirements matches. You see the expert text, the bucket the judge put it in, our requirements for the same paragraph (if any), and, if our pipeline had it but a review dropped it, that item and its probability.

## Reason codes (pick exactly one per item)
- duplicate_or_subsidiary: wrong pick that repeats, details or sub-divides another listed obligation (a limit already stated elsewhere, a sub-clause of a listed requirement).
- test_setup: how a test is set up or carried out (apparatus, sequence, sample preparation, measuring geometry) without a limit the product must meet.
- admin_or_cop: approval procedure, administrative provision, marking-of-approval formalities, or production-control (conformity of production) checks.
- wrong_product_scope: a rule for a device, function, vehicle type or situation the product does not have, or a rule for the vehicle or manufacturer rather than for this product.
- citation_or_split: the content is right but the expert cites a different paragraph, or the expert splits or merges obligations differently from us.
- extraction_miss: the paragraph held the obligation but our extraction wrote nothing for it, or the paragraph was never a candidate or was judged not applicable.
- threshold_drop: our pipeline extracted it but the review probability fell below the keep threshold (use only for a missed item with a dropped review).
- paraphrase: we have the same obligation in other words or with different detail, and the judge did not count it as a match.

## Output
One entry per item: n (copied exactly), code, and a short note (at most 15 words).
