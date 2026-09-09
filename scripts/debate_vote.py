"""
Stage 3: Debate and vote.

Three reviewer personas independently score every idea 1-10 with a one-line
justification (batched in chunks so prompts stay a manageable size and JSON
stays parseable). Scores are summed; the top NUM_WINNERS ideas survive. For
the ideas right on the cutoff line, we run one extra "debate" call so the
reasoning for close calls is recorded in the output, not just a number.

Usage: python debate_vote.py <run_dir>
"""
import sys

from common import ask_json, ask, run_path, save_json, load_json, NUM_WINNERS

REVIEWERS = [
    ("Skeptical engineer", "You weight feasibility, maintenance cost, and risk heavily. "
                            "You're wary of scope creep and ideas that sound good but are vague."),
    ("Product optimist", "You weight user value and impact on growth/retention heavily. "
                          "You're willing to accept some execution risk for a big potential upside."),
    ("Design purist", "You weight coherence with a clean, focused product experience. "
                       "You penalize ideas that would clutter the UI or dilute the app's identity."),
]

CHUNK_SIZE = 20


def chunk(lst, n):
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def score_all(ideas, persona_name, persona_brief, context):
    scores = {}
    for batch in chunk(ideas, CHUNK_SIZE):
        payload = [{"id": i["id"], "title": i["title"], "description": i["description"]} for i in batch]
        result = ask_json(
            f"""You are reviewing improvement ideas for an app as the
"{persona_name}" persona: {persona_brief}

APP CONTEXT (for grounding, don't repeat it back):
{context[:1500]}

IDEAS TO SCORE:
{payload}

For each idea, give a score 1-10 (10 = strongly implement this) and a
one-sentence justification from your persona's point of view.

Return a JSON array: [{{"id": <id>, "score": <int>, "note": "..."}}, ...]
one entry per idea, same ids as given.""",
        )
        for entry in result:
            scores[entry["id"]] = entry
    return scores


def main():
    run_dir = sys.argv[1]
    ideas = load_json(run_dir, "ideas_100.json")
    context = run_path(run_dir, "context_summary.md").read_text()

    all_scores = {}  # id -> {persona_name: {score, note}}
    for persona_name, persona_brief in REVIEWERS:
        print(f"Scoring with persona: {persona_name}")
        persona_scores = score_all(ideas, persona_name, persona_brief, context)
        for idea_id, entry in persona_scores.items():
            all_scores.setdefault(idea_id, {})[persona_name] = entry

    ranked = []
    for idea in ideas:
        per_persona = all_scores.get(idea["id"], {})
        total = sum(p.get("score", 0) for p in per_persona.values())
        ranked.append({**idea, "persona_scores": per_persona, "total_score": total})

    ranked.sort(key=lambda x: x["total_score"], reverse=True)

    # Extra debate round for ideas near the cutoff line (close calls), so the
    # reasoning behind borderline decisions is recorded, not just a number.
    border_lo = max(0, NUM_WINNERS - 5)
    border_hi = min(len(ranked), NUM_WINNERS + 5)
    for idea in ranked[border_lo:border_hi]:
        debate = ask(
            f"""Three reviewers scored this idea for an app:
Idea: {idea['title']} - {idea['description']}
Scores: {idea['persona_scores']}

In 3-4 sentences, write a short debate summary: where the reviewers agreed,
where they disagreed, and a final call on whether it's a "keep" or "cut"
right at this cutoff line.""",
        )
        idea["cutoff_debate"] = debate

    winners = ranked[:NUM_WINNERS]
    save_json(run_dir, "ideas_ranked_full.json", ranked)
    save_json(run_dir, "ideas_winners_25.json", winners)
    print(f"Top {NUM_WINNERS} winners written to {run_dir}/ideas_winners_25.json")


if __name__ == "__main__":
    main()
