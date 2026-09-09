"""
Stage 2: Generate 100 ideas.

Runs several "generator persona" calls, each asked for a slice of the 100
ideas from a different angle (growth, retention, tech debt/quality, delight,
monetization). This gets more variety than one call asked for 100 ideas at
once, which tends to repeat itself after ~20.

Usage: python generate_ideas.py <run_dir>
"""
import sys

from common import ask_json, run_path, save_json, NUM_IDEAS

PERSONAS = [
    ("Growth", "New features or changes that would attract new users or increase usage."),
    ("Retention", "Changes that make existing users stick around / come back / churn less."),
    ("Quality & tech debt", "Improvements to reliability, performance, code health, or maintainability."),
    ("Delight & polish", "Small UX/UI touches that make the app feel more thoughtful or fun to use."),
    ("Monetization & growth-adjacent", "Ideas around pricing, upsells, sharing/virality, or business model."),
]


def ideas_per_persona(total, n_personas):
    base = total // n_personas
    counts = [base] * n_personas
    counts[-1] += total - base * n_personas
    return counts


def main():
    run_dir = sys.argv[1]
    context = run_path(run_dir, "context_summary.md").read_text()

    counts = ideas_per_persona(NUM_IDEAS, len(PERSONAS))
    all_ideas = []

    for (persona_name, persona_brief), count in zip(PERSONAS, counts):
        result = ask_json(
            f"""You are brainstorming improvement ideas for an app, from the
"{persona_name}" angle: {persona_brief}

APP CONTEXT:
{context}

Generate exactly {count} distinct, concrete ideas from this angle. Avoid
generic filler ("improve UX") — each idea should be specific enough that
someone could start building it. Avoid duplicating obvious ideas any
reasonable person would already have thought of unless you have a genuinely
different angle on it.

Return a JSON array of objects, each: {{"title": "...", "description": "...",
"persona": "{persona_name}"}}. "description" should be 2-3 sentences.""",
            max_tokens=4000,
        )
        all_ideas.extend(result)
        print(f"{persona_name}: {len(result)} ideas generated")

    for i, idea in enumerate(all_ideas):
        idea["id"] = i

    save_json(run_dir, "ideas_100.json", all_ideas)
    print(f"Total ideas: {len(all_ideas)} -> {run_dir}/ideas_100.json")


if __name__ == "__main__":
    main()
