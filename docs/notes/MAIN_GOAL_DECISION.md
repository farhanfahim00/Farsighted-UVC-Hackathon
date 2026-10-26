# Main challenge: threshold decision (2026-10-25 dev)

The team's **official development-platform** measurements as reported by the team:

| Export | Requirements | Precision | Recall | F2 |
|---|---:|---:|---:|---:|
| `v8_a`, threshold 15 | 407 | 39.8% | 85.3% | **69.4%** |
| `v8_a_t25`, threshold 25 | 319 | 45.8% | 76.8% | 67.7% |

- Threshold 25 *does* improve precision, but loses more recall than F2 rewards.
- The default first held-out upload should use the established threshold-15 review configuration with the team's safe dedup variant (per their latest runbook).
- Conservative dedup has modest upside; don't merge distinct functions merely because their text resembles each other (e.g. stop vs rear-position lamp).
- An unseen bumper is not the rear-lamp development set, so this is a starting choice, not a guarantee.
- If the portal visibly scores each held-out upload, compare validated variants and reupload the best last. If individual held-out scores aren't available, **do not blindly upload multiple variants**. The held-out case uses the **last valid upload**, not the best one.
- Do not use the public `expected.json` to manually edit the hidden submission or hardcode regulations.

**Demo angle:** show the quality mechanism rather than quoting the leaderboard. “We tune the review stage for the competition's recall-weighted F2, and keep source evidence and validation throughout.”
