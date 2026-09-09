"""
Stage 4: Write a detailed build prompt for each of the 25 winning ideas.

Each prompt is what gets handed to the build stage (a Claude Code instance
with real repo access). It needs to be self-contained: what to build, likely
files affected, constraints, and a definition of done — since the build step
runs in a fresh matrix job with no memory of the ideation conversation.

Usage: python make_build_prompts.py <run_dir>
"""
import sys

from common import ask, run_path, load_json, save_json


def main():
    run_dir = sys.argv[1]
    winners = load_json(run_dir, "ideas_winners_25.json")
    context = run_path(run_dir, "context_summary.md").read_text()

    prompts = []
    for idea in winners:
        prompt = ask(
            f"""You are writing a build brief for another AI coding agent
(Claude Code) that will implement ONE feature in a real codebase. The agent
will have repo access but no memory of this conversation, so the brief must
be fully self-contained.

APP CONTEXT:
{context}

IDEA TO IMPLEMENT:
Title: {idea['title']}
Description: {idea['description']}
Reviewer notes: {idea.get('cutoff_debate', '')}

Write a build brief with these sections:
1. **Goal** - one paragraph, what to build and why
2. **Scope** - what's in scope for a first working version vs explicitly out
   of scope (keep it buildable in one focused session)
3. **Likely files/areas affected** - best guess based on the app context
4. **Approach** - a short suggested implementation approach
5. **Definition of done** - a bullet checklist
6. **Deliverables required** - this brief will be used to produce, WITHOUT
   repo access: (a) a written pros_and_cons.md weighing tradeoffs of this
   change, (b) a simple standalone mockup.html illustrating the UI/UX if
   this idea has a visual component. There is no automatic implementation
   step, so the brief should be detailed enough that a human (or a coding
   agent, later, by hand) could hand this exact brief to Claude Code and get
   a working implementation with minimal back-and-forth.

Keep the whole brief under 500 words.""",
        )
        prompts.append({"id": idea["id"], "title": idea["title"], "build_prompt": prompt})

    save_json(run_dir, "build_prompts.json", prompts)
    print(f"Wrote {len(prompts)} build prompts to {run_dir}/build_prompts.json")


if __name__ == "__main__":
    main()
