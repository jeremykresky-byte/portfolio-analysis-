---
description: Rescan the economy for investable trends, rebuild the retirement dashboard, and write a brief
argument-hint: "[optional focus, e.g. 'agriculture' or 'rates']"
---

Refresh the economic trend scan for this portfolio.

1. Run `python3 -m retirement trends` and read the newest `reports/trends-*.md`.
2. Run `python3 -m retirement report` to rebuild `reports/retirement-dashboard.html` with the new scan.
3. Use WebSearch to check the top 3 ranked themes and any theme whose rank moved by 3 or more since the
   previous scan. Look for news from the last 30 days that confirms or contradicts the signal.
   $ARGUMENTS
4. Write `reports/trends-brief-<today>.md` with:
   - Macro regime in 3 bullets (which flags turned on/off since last scan, and why it matters)
   - Top themes: signal, the evidence, and 1-2 Canadian-listed ETFs to research
   - Themes to avoid right now, and why
   - Any recommendation in `python3 -m retirement recommend --quick` whose trigger has fired
   - Sources as markdown links
5. Summarise the brief in chat in under 10 lines. Treat these as screening signals, not advice.
