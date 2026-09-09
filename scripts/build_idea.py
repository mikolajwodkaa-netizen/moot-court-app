"""
Stage 5: Produce docs + mockup for ONE winning idea (no code implementation).

This version does NOT require Claude Code or repo access. It makes a single
API call per idea, asking Claude to write:
  - pros_and_cons.md: a written analysis of the tradeoffs
  - mockup.html: a standalone visual mockup (if the idea has a UI component)

There is no automatic implementation and no git branch. When you decide you
like an idea, hand its build_prompt (saved alongside the docs) to Claude Code
yourself, or paste it into claude.ai, to actually build it.

Usage: python build_idea.py <run_dir> <idea_index>
"""
import sys

from common import ask_json, load_json, run_path


def main():
    run_dir, idea_index = sys.argv[1], int(sys.argv[2])
    prompts = load_json(run_dir, "build_prompts.json")

    if idea_index >= len(prompts):
        print(f"No idea at index {idea_index}, skipping.")
        return

    idea = prompts[idea_index]
    out_dir = run_path(run_dir, f"idea_{idea_index}")

    result = ask_json(
        f"""Here is a build brief for a proposed app feature:

{idea['build_prompt']}

Produce two deliverables based on this brief alone (you do not have access
to the actual codebase):

1. A pros_and_cons.md style analysis: a clear, honest weighing of the
   tradeoffs of building this — user value, implementation complexity/risk,
   maintenance burden, and anything that could go wrong. 300-500 words,
   written in markdown.

2. A standalone mockup.html: a single self-contained HTML file (inline CSS,
   no external dependencies) that visually illustrates the UI/UX for this
   idea. If the idea is purely backend/invisible to users, instead write a
   short HTML page that diagrams or explains the change conceptually.

Return a JSON object: {{"pros_and_cons_md": "...", "mockup_html": "..."}}
with the FULL content of each file as a string (escape appropriately for
JSON).""",
        max_tokens=6000,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "pros_and_cons.md").write_text(result.get("pros_and_cons_md", ""))
    (out_dir / "mockup.html").write_text(result.get("mockup_html", ""))
    (out_dir / "idea.md").write_text(
        f"# {idea['title']}\n\n"
        f"## Build prompt (hand this to Claude Code to actually implement it)\n\n"
        f"{idea['build_prompt']}\n"
    )

    print(f"Idea {idea_index} ({idea['title']}): docs + mockup written to {out_dir}")


if __name__ == "__main__":
    main()
