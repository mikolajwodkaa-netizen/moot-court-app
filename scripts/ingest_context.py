"""
Stage 1: Ingest codebase context.

Walks the repo, builds a file tree, and pulls in the README plus the heads of
a handful of "entry point" files, then asks Claude to write a short plain-
English summary of what the app does and how it's structured. This summary
(not the raw repo) is what gets fed into idea generation, so it stays small
and cheap regardless of repo size.

Usage: python ingest_context.py <repo_path> <run_dir>
"""
import re
import sys
from pathlib import Path

from common import ask, run_path

IGNORE_DIRS = {".git", "node_modules", "venv", ".venv", "__pycache__", "dist", "build", ".next"}
INTERESTING_EXT = {".py", ".js", ".ts", ".tsx", ".jsx", ".rb", ".go", ".java", ".md", ".spec", ".txt"}
MAX_FILE_CHARS = 3000       # full-text cap: files under this are read in full
MAX_OUTLINE_CHARS = 12000   # outline cap for large files (see extract_outline)
MAX_FILES_SAMPLED = 25

# Regexes for a free, no-API-call structural outline of large single files —
# a 3000-char head-truncation loses everything past the docstring on a file
# like a 3500-line monolithic app, so large files get this outline instead.
# Ordered by priority: structure (classes/functions/methods) always wins
# budget over decorative constants (e.g. a long block of hex color codes),
# since structure is what actually tells another Claude what the app does.
_OUTLINE_PATTERNS_BY_PRIORITY = [
    (re.compile(r'^class\s+\w+'), "class"),
    (re.compile(r'^def\s+\w+'), "top-level function"),
    (re.compile(r'^\s{4}def\s+\w+'), "method"),
    (re.compile(r'^#\s*[─=]{5,}'), "section marker"),
    (re.compile(r'^[A-Z][A-Z0-9_]*\s*='), "constant"),
]


def extract_outline(text: str) -> str:
    """Cheap regex-based structural map of a large file: every class, every
    top-level function/method signature, and section-header/constant lines,
    each with its line number. No API call needed — this is meant to
    accompany (not replace) a head-of-file text sample so large single-file
    apps aren't reduced to just their first few thousand chars. Structural
    lines (class/def/method) are budgeted first so a long run of low-value
    constants near the top of the file can't crowd out the actual code
    structure further down."""
    buckets = {label: [] for _, label in _OUTLINE_PATTERNS_BY_PRIORITY}
    for i, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.rstrip()
        for pattern, label in _OUTLINE_PATTERNS_BY_PRIORITY:
            if pattern.match(stripped):
                buckets[label].append(f"L{i}: {stripped.strip()}")
                break

    out_lines, used = [], 0
    for _pattern, label in _OUTLINE_PATTERNS_BY_PRIORITY:
        for line in buckets[label]:
            if used + len(line) + 1 > MAX_OUTLINE_CHARS:
                out_lines.append(f"... ({label} list truncated for space) ...")
                break
            out_lines.append(line)
            used += len(line) + 1
    return "\n".join(out_lines)


def build_file_tree(repo: Path) -> str:
    lines = []
    for path in sorted(repo.rglob("*")):
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        rel = path.relative_to(repo)
        depth = len(rel.parts) - 1
        if path.is_dir():
            lines.append("  " * depth + f"{rel.name}/")
        else:
            lines.append("  " * depth + rel.name)
    return "\n".join(lines[:2000])  # cap for very large repos


def sample_files(repo: Path) -> str:
    samples = []
    count = 0
    # Prioritize READMEs and common entry points first
    priority_names = {"README.md", "package.json", "requirements.txt", "main.py",
                       "app.py", "index.js", "index.ts", "server.js"}
    all_files = sorted(repo.rglob("*"), key=lambda p: p.name not in priority_names)
    for path in all_files:
        if count >= MAX_FILES_SAMPLED:
            break
        if not path.is_file():
            continue
        if any(part in IGNORE_DIRS for part in path.parts):
            continue
        if path.suffix not in INTERESTING_EXT and path.name not in priority_names:
            continue
        try:
            full_text = path.read_text(errors="ignore")
        except Exception:
            continue

        if len(full_text) <= MAX_FILE_CHARS:
            samples.append(f"--- {path.relative_to(repo)} (full file) ---\n{full_text}")
        else:
            head = full_text[:MAX_FILE_CHARS]
            outline = extract_outline(full_text)
            samples.append(
                f"--- {path.relative_to(repo)} ({len(full_text)} chars total, "
                f"showing head + structural outline of the rest) ---\n"
                f"{head}\n\n[STRUCTURAL OUTLINE of the full file — every class, "
                f"function, constant, and section header with line numbers, so "
                f"nothing past the head is invisible even though the full body "
                f"isn't included]:\n{outline}"
            )
        count += 1
    return "\n\n".join(samples)


def main():
    repo_path, run_dir = sys.argv[1], sys.argv[2]
    repo = Path(repo_path)

    tree = build_file_tree(repo)
    samples = sample_files(repo)

    summary = ask(
        f"""Here is a file tree and sample file contents from a codebase.

FILE TREE:
{tree}

SAMPLE FILES:
{samples}

Write a concise (400-600 word) plain-English summary covering:
1. What the app does / who it's for
2. Its main components and how they fit together
3. The tech stack
4. Anything that looks like a known limitation, TODO, or rough edge

This summary will be used by other Claude instances to brainstorm improvement
ideas, so prioritize the information that would help someone suggest good,
buildable ideas.""",
    )

    run_path(run_dir, "context_summary.md").write_text(summary)
    print(f"Wrote context summary ({len(summary)} chars) to {run_dir}/context_summary.md")


if __name__ == "__main__":
    main()
