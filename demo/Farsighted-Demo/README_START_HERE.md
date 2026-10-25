# Farsighted - demo kit (25 October 2026)

## What is included

- `index.html`: polished, offline, read-only viewer. No server, cloud service, framework or API key is required.
- `VIDEO_SCRIPT.md`: timed screen-recording plan and narration for a video under 2 minutes.
- `TECHNICAL_EXPLANATION.md`: concise text for the final hackathon form.
- `examples/rear-lamp-spec.md` and `examples/exploded_view.png`: the organizer's development-case inputs.
- `examples/subgoal-v3-submission.json`: the team's original automated V3 development output, not organizer expected answers.

## Use it now (before 08:00)

1. Extract this ZIP anywhere, for example `C:\UVC2026\Farsighted-Demo`.
2. Double-click `index.html` to open in Edge or Chrome. The content works offline.
3. Ask Farhan for his **actual generated** main-case JSON. On his machine it should be `build/cases/dev-rear-lamp/submission.json` (or his validated scored variant).
4. On **Executive overview**, press **Load main submission JSON** and select that file.
5. Go to **Requirements** and search a few examples. Click a row to show the full source and citation.
6. Go to **Change intelligence**. It opens with **the real, automated V3 development output** already loaded. Click an added or modified entry to show the rationale and source. The V3 output has 43 proposals; not all are true positives.
7. Open **Architecture** to show the five main processing stages.
8. Record the demo using `VIDEO_SCRIPT.md`, aim for 1 minute 50 seconds, and upload as an unlisted video. Verify the share link outside your signed-in session.

If opening a local HTML file is blocked by corporate security settings, in PowerShell run:

```powershell
Set-Location C:\UVC2026\Farsighted-Demo
$Py = 'C:\Users\haqs\AppData\Local\Python\pythoncore-3.14-64\python.exe'
& $Py -m http.server 8765
```

Then open `http://localhost:8765/index.html` in Edge. This only hosts the page locally on the laptop. Press Ctrl+C when finished.

## If the held-out case arrives at 08:00

The interface can load new `submission.json` and `submission_subgoal.json` without code changes. On **Product & inputs**, use **Load product spec (.md)** and **Load product image** to replace the rear-lamp development example before presenting a held-out case. Do not show the rear-lamp picture as though it were a bumper.

The page does not submit anything to Certivity, change source files or run the AI pipeline. It only reads the JSON selected in the browser. Use the official platform for competition uploads.

## Important safeguards

- Do not put API tokens in the viewer or video.
- Do not use `expected.json` as a demonstration result.
- Do not claim all displayed requirements or version changes are correct; they are pipeline proposals for engineering review.
- Your development leaderboard score is **not** the held-out competition score.
- The held-out portal uses the **last valid** upload. Preserve safe copies before replacing them.
