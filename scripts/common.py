"""Shared helpers for the daily idea pipeline scripts."""
import json
import os
import re
from pathlib import Path

import anthropic

IDEATION_MODEL = "claude-sonnet-4-6"   # cheap/fast: ideation, debate, prompt-writing
NUM_IDEAS = 40
NUM_WINNERS = 3

client = anthropic.Anthropic()  # reads ANTHROPIC_API_KEY from env


def ask(prompt: str, system: str = "", model: str = IDEATION_MODEL, max_tokens: int = 4000) -> str:
    """One-shot call to the Claude API, returns concatenated text blocks."""
    msg = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system or anthropic.NOT_GIVEN,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in msg.content if b.type == "text")


def ask_json(prompt: str, system: str = "", model: str = IDEATION_MODEL, max_tokens: int = 4000):
    """Call the API and parse a JSON response, stripping markdown fences if present."""
    text = ask(
        prompt + "\n\nRespond with ONLY valid JSON. No markdown fences, no preamble.",
        system=system,
        model=model,
        max_tokens=max_tokens,
    )
    cleaned = re.sub(r"^```(json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    return json.loads(cleaned)


def run_path(run_dir: str, *parts) -> Path:
    p = Path(run_dir, *parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def save_json(run_dir: str, name: str, data) -> None:
    run_path(run_dir, name).write_text(json.dumps(data, indent=2))


def load_json(run_dir: str, name: str):
    return json.loads(run_path(run_dir, name).read_text())


def slugify(text: str, max_len: int = 40) -> str:
    slug = re.sub(r"[^a-z0-9-]+", "-", text.lower()).strip("-")
    return slug[:max_len] or "idea"
