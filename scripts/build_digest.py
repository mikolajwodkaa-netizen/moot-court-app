"""
Stage 6: Build the daily digest.

Collects every idea's build output (pros/cons doc, mockup, branch name) into
one scrollable HTML page so you can review the day's 25 ideas in one sitting
and decide what to merge.

Usage: python build_digest.py <run_dir>
"""
import html
import sys
from pathlib import Path

from common import load_json, run_path

CARD_TEMPLATE = """
<div class="card">
  <h2>{index}. {title}</h2>
  <p class="score">Vote score: {score}</p>
  <div class="pros-cons">{pros_cons}</div>
  {mockup_link}
  {branch_info}
</div>
"""

PAGE_TEMPLATE = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Daily Idea Digest</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 900px; margin: 40px auto; padding: 0 20px; color: #222; }}
  h1 {{ font-size: 1.8em; }}
  .card {{ border: 1px solid #ddd; border-radius: 10px; padding: 20px; margin-bottom: 24px; }}
  .card h2 {{ margin-top: 0; }}
  .score {{ color: #666; font-size: 0.9em; }}
  .pros-cons {{ white-space: pre-wrap; background: #fafafa; padding: 12px; border-radius: 8px; }}
  .branch {{ font-family: monospace; background: #eef; padding: 4px 8px; border-radius: 4px; }}
  a.mockup {{ display: inline-block; margin: 8px 0; }}
</style>
</head>
<body>
<h1>Daily Idea Digest — {date}</h1>
<p>{count} ideas built today. Each has its own branch — review and merge whatever you like.</p>
{cards}
</body>
</html>
"""


def main():
    run_dir = sys.argv[1]
    winners = load_json(run_dir, "ideas_winners_25.json")

    cards = []
    for i, idea in enumerate(winners):
        idea_dir = Path(run_dir, f"idea_{i}")
        pros_cons_path = idea_dir / "pros_and_cons.md"
        mockup_path = idea_dir / "mockup.html"
        idea_md_path = idea_dir / "idea.md"

        pros_cons = html.escape(pros_cons_path.read_text()) if pros_cons_path.exists() else "(no doc produced)"
        mockup_link = f'<a class="mockup" href="idea_{i}/mockup.html">View mockup</a>' if mockup_path.exists() else ""
        branch_info = (
            f'<p>Like this one? Open <span class="branch">idea_{i}/idea.md</span> for the full build '
            f'prompt — hand it to Claude Code (or paste into claude.ai) to implement it.</p>'
            if idea_md_path.exists() else "<p><em>Build failed or skipped</em></p>"
        )

        cards.append(CARD_TEMPLATE.format(
            index=i + 1,
            title=html.escape(idea["title"]),
            score=idea.get("total_score", "?"),
            pros_cons=pros_cons,
            mockup_link=mockup_link,
            branch_info=branch_info,
        ))

    from datetime import date
    page = PAGE_TEMPLATE.format(date=date.today().isoformat(), count=len(winners), cards="\n".join(cards))
    run_path(run_dir, "digest.html").write_text(page)
    print(f"Digest written to {run_dir}/digest.html")


if __name__ == "__main__":
    main()
