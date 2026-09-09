"""
MOOT COURT TRAINER
A standalone courtroom-simulation app.

This is the "Moot Court" feature lifted out of a larger assistant app
(JARVIS) and repackaged as its own program. The original app is untouched;
this is a new, separate file that only contains the courtroom-simulation
piece plus the minimal window/canvas/API scaffolding it needs to run on
its own.

Requires an Anthropic API key (https://console.anthropic.com/). On first
run it will ask for one and save it locally so you don't have to re-enter
it every time.
"""

import os, sys, re, math, random, threading
import json as _json
import tkinter as tk
import tkinter.font as tkfont
import tkinter.simpledialog as simpledialog
import tkinter.messagebox as messagebox
from datetime import datetime

import anthropic

# ── Local storage (config + exports + case history) ─────────────────────────
APP_DIR = os.path.join(os.path.expanduser("~"), ".moot_court_trainer")
os.makedirs(APP_DIR, exist_ok=True)
CONFIG_FILE = os.path.join(APP_DIR, "config.json")
COURT_EXPORTS_DIR = os.path.join(APP_DIR, "exports")
os.makedirs(COURT_EXPORTS_DIR, exist_ok=True)
COURT_HISTORY_FILE = os.path.join(APP_DIR, "history.json")


def _load_config():
    try:
        with open(CONFIG_FILE, "r") as f:
            return _json.load(f)
    except Exception:
        return {}


def _save_config(cfg):
    try:
        with open(CONFIG_FILE, "w") as f:
            _json.dump(cfg, f)
    except Exception:
        pass


def _load_court_history():
    try:
        with open(COURT_HISTORY_FILE, "r") as f:
            return _json.load(f)
    except Exception:
        return []


def _save_court_history(data):
    try:
        with open(COURT_HISTORY_FILE, "w") as f:
            _json.dump(data, f)
    except Exception:
        pass


# ── Lesson-layer persistence (separate file, same load/save pattern) ────────
LESSON_PROGRESS_FILE = os.path.join(APP_DIR, "lesson_progress.json")


def _default_lesson_progress():
    return {
        "xp": 0,
        "streak": 0,
        "last_practice_date": None,
        "lessons": {},          # lesson_id -> {"status": "passed"|"failed", "last_attempt": iso date}
        "missed_drills": [],    # [{"lesson": lesson_id, "drill_id": drill_id}], de-duped on insert
    }


def _load_lesson_progress():
    try:
        with open(LESSON_PROGRESS_FILE, "r") as f:
            data = _json.load(f)
            defaults = _default_lesson_progress()
            defaults.update(data)
            return defaults
    except Exception:
        return _default_lesson_progress()


def _save_lesson_progress(data):
    try:
        with open(LESSON_PROGRESS_FILE, "w") as f:
            _json.dump(data, f)
    except Exception:
        pass


def _record_lesson_practice(progress):
    """Updates streak/last_practice_date in place — call once per completed
    lesson (pass or fail; showing up counts). A streak continues if the last
    practice was yesterday or today, and resets to 1 otherwise."""
    today = datetime.now().strftime("%Y-%m-%d")
    last = progress.get("last_practice_date")
    if last == today:
        pass  # already practiced today, streak unchanged
    elif last is not None:
        try:
            gap = (datetime.strptime(today, "%Y-%m-%d") - datetime.strptime(last, "%Y-%m-%d")).days
        except Exception:
            gap = 999
        progress["streak"] = progress.get("streak", 0) + 1 if gap == 1 else 1
    else:
        progress["streak"] = 1
    progress["last_practice_date"] = today


def _queue_missed_drill(progress, lesson_id, drill_id):
    entry = {"lesson": lesson_id, "drill_id": drill_id}
    if entry not in progress["missed_drills"]:
        progress["missed_drills"].append(entry)


def _clear_missed_drill(progress, lesson_id, drill_id):
    progress["missed_drills"] = [
        e for e in progress["missed_drills"]
        if not (e["lesson"] == lesson_id and e["drill_id"] == drill_id)
    ]


def court_export_transcript(case, transcript, verdict, character_name):
    """Writes the full trial (case facts, every phase's exchange, verdict,
    and rubric scores) to a plain-text file. Returns the file path, or None
    on failure — exporting should never crash the app if disk I/O fails."""
    try:
        safe_title = re.sub(r'[^\w\s-]', '', case["title"]).strip().replace(" ", "_")[:50]
        fname = f"{safe_title}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        path = os.path.join(COURT_EXPORTS_DIR, fname)
        lines = [
            f"MOOT COURT TRANSCRIPT — {case['title']}", "=" * 60,
            f"Case type: {case['case_type']}", f"Opponent: {character_name}",
            f"You argued for: {case['your_side']}", f"Opposing/presiding: {case['jarvis_side']}", "",
            "FACTS:", case["facts"], "", "LEGAL ISSUE:", case["issue"], "",
        ]
        if case.get("amendment"):
            lines += [f"CONSTITUTIONAL BASIS: {case['amendment']}", ""]
        for phase in ("opening", "arguments", "closing"):
            phase_lines = [t for t in transcript if t["phase"] == phase]
            if not phase_lines:
                continue
            lines += [f"--- {phase.upper()} ---"]
            for t in phase_lines:
                lines += [f"{t['speaker']}: {t['text']}", ""]
        if verdict:
            lines += ["=" * 60, "VERDICT", verdict.get("winner", ""), "",
                      "Reasoning:", verdict.get("reasoning", ""), "",
                      "Feedback:", verdict.get("feedback", "")]
            if verdict.get("rubric"):
                lines += ["", "RUBRIC SCORES:"]
                for k, v in verdict["rubric"].items():
                    lines += [f"  {k}: {v}/10"]
        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        return path
    except Exception:
        return None


# ── Colours (holographic glass HUD palette) ──────────────────────────────────
ABYSS = "#030814"
GLASS = "#0b1830"
GLASS_EDGE = "#2c5d8a"
CORE = "#4fd8ff"
CORE_DIM = "#0f2a3d"
HOT = "#eafcff"

BG = ABYSS
CYAN = CORE
CYAN2 = HOT
CDIM = CORE_DIM
CMID = GLASS_EDGE
GREEN = "#5dffb0"
RED = "#ff5d7a"
ORANGE = "#ffb454"
GOLD = "#ffd76a"
PURPLE = "#b48aff"
ACCENT2 = PURPLE
TEXT = "#bfe6f5"
TDIM = "#264258"

FONT_DISPLAY = "Segoe UI"
FONT_DATA = "Consolas"

W, H = 980, 760


def _blend_hex(c1, c2, t):
    """Linear-interpolate two '#rrggbb' colours; t=0 -> c1, t=1 -> c2."""
    t = max(0.0, min(1.0, t))
    r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
    r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
    r = int(r1 + (r2 - r1) * t); g = int(g1 + (g2 - g1) * t); b = int(b1 + (b2 - b1) * t)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── Moot Court reference content / bot roster / experience settings ─────────
# Ordered easiest to hardest. "label" is the short all-caps name used for
# transcript speaker tags and in-scene captions (replaces the old "JARVIS").
# "accent" is drawn from the palette constants defined above.
BOT_CHARACTERS = {
    "hutz": {
        "name": "Lionel Hutz", "source": "The Simpsons", "elo": 400, "label": "HUTZ", "accent": "#264258",
        "teaser": "Cites case law that doesn't exist — and means it.",
        "pose_mod": "slouch",
        "figure": {
            "height": 0.94, "build": 1.12, "posture_bias": 6,
            "prop": "battered_briefcase", "prop_side": -1,
            "gesture": {"amp": 0.8, "speed": 0.75},
        },
        "catchphrases": {
            "entrance": "I've argued cases you wouldn't believe. Well — one, technically.",
            "objection_won": "Ha! Even a stopped clock, my friend.",
            "objection_lost": "That's, uh, still under appeal. In my head.",
            "closing_open": "Let me walk you through some caselaw. Some of it's real.",
            "verdict_win": "I told you I went to law school! Mostly!",
            "verdict_loss": "I'll finish my case after this brief recess. Of my career.",
        },
        "style_prompt": (
            "Argue like Lionel Hutz: disorganized and prone to rambling, confidently cite legal precedent "
            "that sounds real and is formatted like a real citation (a plausible-sounding case name, a "
            "plausible court and year, e.g. 'Blackwell v. Meridian Trust, 9th Cir. 1987') but is actually "
            "fabricated, misapplied, or misremembered — the comedy is that it sounds authoritative and "
            "citation-shaped, not that it's obviously nonsense. Lose your train of thought under any real "
            "pressure, and occasionally blurt out something startlingly unprofessional. Despite the "
            "incompetence, still genuinely try to make a real legal argument — just badly and on shaky "
            "authority, not a nonsense one."
        ),
    },
    "woods": {
        "name": "Elle Woods (early career)", "source": "Legally Blonde", "elo": 700, "label": "ELLE", "accent": "#5dffb0",
        "teaser": "Underestimated, earnest, occasionally brilliant.",
        "pose_mod": "hair_flip",
        "figure": {
            "height": 0.98, "build": 0.86, "posture_bias": -2,
            "prop": "blazer_pin", "prop_side": 1,
            "gesture": {"amp": 1.15, "speed": 1.2},
        },
        "catchphrases": {
            "entrance": "What, like it's hard?",
            "objection_won": "See, bend and snap works in the courtroom too.",
            "objection_lost": "Okay, that one stung. Regrouping.",
            "closing_open": "I know I don't look like I belong up here. Watch this.",
            "verdict_win": "I knew this blazer was a lucky one.",
            "verdict_loss": "I still think I made a great point about the perm.",
        },
        "style_prompt": (
            "Argue like an early-career Elle Woods: earnest, warm, and easy to underestimate, lean on "
            "unconventional reasoning and analogies rather than deep doctrine, make the occasional rookie "
            "procedural slip, but land a genuinely sharp, insightful point every so often that catches the "
            "user off guard."
        ),
    },
    "gambini": {
        "name": "Vinny Gambini", "source": "My Cousin Vinny", "elo": 1000, "label": "VINNY", "accent": "#ffd76a",
        "teaser": "No idea about procedure. Great instincts anyway.",
        "pose_mod": "point",
        "figure": {
            "height": 1.0, "build": 1.05, "posture_bias": 3,
            "prop": "loose_tie", "prop_side": 0,
            "gesture": {"amp": 1.3, "speed": 1.35},
        },
        "catchphrases": {
            "entrance": "Everything this guy's about to say is bulls#!t.",
            "objection_won": "That's what I'm talkin' about!",
            "objection_lost": "Ah, come on, that's — fine. Fine!",
            "closing_open": "Look, I'm gonna keep this simple, alright?",
            "verdict_win": "Yeah, you're da man, but I'm da man now.",
            "verdict_loss": "This isn't over. My cousin's gonna hear about this.",
        },
        "style_prompt": (
            "Argue like Vinny Gambini: shaky on formal courtroom procedure and prone to fumbling objections "
            "or formalities, but sharp, streetwise, and instinctively good at spotting the one detail that "
            "doesn't add up. Be blunt, a little combative, and unpredictable — sometimes stumble, sometimes "
            "land a surprisingly devastating point."
        ),
    },
    "ross": {
        "name": "Mike Ross (early seasons)", "source": "Suits", "elo": 1300, "label": "MIKE", "accent": "#4fd8ff",
        "teaser": "Quick and well-read — until you push back hard.",
        "pose_mod": "poised",
        "figure": {
            "height": 1.0, "build": 0.92, "posture_bias": -1,
            "prop": "legal_pad", "prop_side": -1,
            "gesture": {"amp": 0.95, "speed": 1.0},
        },
        "catchphrases": {
            "entrance": "I've read every case on point. Try me.",
            "objection_won": "Photographic memory has its uses.",
            "objection_lost": "...give me a second, I had this.",
            "closing_open": "Here's what the record actually shows.",
            "verdict_win": "Turns out you don't need the degree to be right.",
            "verdict_loss": "I need to go double check something. Several somethings.",
        },
        "style_prompt": (
            "Argue like an early-seasons Mike Ross: quick-witted, well-read, and able to cite relevant law "
            "fluently, presenting confident, polished arguments. But you rattle under sustained aggressive "
            "pushback — if the user presses hard and repeatedly, let your confidence crack a little and your "
            "responses grow more hesitant."
        ),
    },
    "mccoy": {
        "name": "Jack McCoy", "source": "Law & Order", "elo": 1600, "label": "MCCOY", "accent": "#ff5d7a",
        "teaser": "Moralistic and merciless on every technicality.",
        "pose_mod": "rigid",
        "figure": {
            "height": 1.06, "build": 1.0, "posture_bias": -4,
            "prop": "pointer_finger", "prop_side": 1,
            "gesture": {"amp": 0.7, "speed": 0.85},
        },
        "catchphrases": {
            "entrance": "Let's establish what actually happened here.",
            "objection_won": "The law isn't a technicality. It's the point.",
            "objection_lost": "Noted. It doesn't change what's true.",
            "closing_open": "This isn't about sympathy. It's about the law.",
            "verdict_win": "Justice doesn't need your applause.",
            "verdict_loss": "The jury's wrong. That happens. Rarely.",
        },
        "style_prompt": (
            "Argue like Jack McCoy: aggressive, moralistic, and treats the courtroom like a crusade. Exploit "
            "technicalities and procedural openings ruthlessly, punish any weak reasoning or unsupported claim "
            "the user makes, and don't hesitate to make it personal or emotionally charged when it strengthens "
            "your position."
        ),
    },
    "goodman": {
        "name": "Saul Goodman", "source": "Better Call Saul", "elo": 1900, "label": "SAUL", "accent": "#b48aff",
        "teaser": "Slippery, theatrical, morally... flexible.",
        "pose_mod": "showy",
        "figure": {
            "height": 0.96, "build": 1.0, "posture_bias": 2,
            "prop": "pinky_ring", "prop_side": 1,
            "gesture": {"amp": 1.4, "speed": 1.3},
        },
        "catchphrases": {
            "entrance": "Better call — well, you know how it goes.",
            "objection_won": "See, there's always a way around it.",
            "objection_lost": "Technicality. We'll circle back.",
            "closing_open": "Let me paint you a very different picture.",
            "verdict_win": "It's all good, man!",
            "verdict_loss": "I know a guy who can appeal this.",
        },
        "style_prompt": (
            "Argue like Saul Goodman: slippery, theatrical, and morally flexible, willing to reframe facts in "
            "a self-serving light, deploy showmanship and misdirection, and lean on loopholes or technical "
            "outs whenever they're available, dodging direct questions with charm rather than answering them "
            "head-on."
        ),
    },
    "specter": {
        "name": "Harvey Specter", "source": "Suits", "elo": 2200, "label": "HARVEY", "accent": "#eafcff",
        "teaser": "Turns your own argument against you.",
        "pose_mod": "crossed_arms",
        "figure": {
            "height": 1.08, "build": 1.08, "posture_bias": -3,
            "prop": "cufflink", "prop_side": 1,
            "gesture": {"amp": 0.55, "speed": 0.7},
        },
        "catchphrases": {
            "entrance": "I don't get lucky. I get prepared.",
            "objection_won": "That's called being three steps ahead.",
            "objection_lost": "Enjoy that one. It's the only one you get.",
            "closing_open": "Let me tell you what actually happened.",
            "verdict_win": "Never doubted it for a second.",
            "verdict_loss": "Careful. I remember everything.",
        },
        "style_prompt": (
            "Argue like Harvey Specter: dominant, composed, and always several steps ahead. Stay calm under "
            "pressure, subtly reframe the user's own argument or words and use them against them, project "
            "total confidence even when conceding a minor point, and close with a decisive, memorable line."
        ),
    },
    "keating": {
        "name": "Annalise Keating", "source": "How to Get Away with Murder", "elo": 2500, "label": "ANNALISE", "accent": "#ffb454",
        "teaser": "Finds the one weak thread and pulls until it unravels.",
        "pose_mod": "lean_forward",
        "figure": {
            "height": 1.02, "build": 0.96, "posture_bias": 5,
            "prop": "red_thread_folder", "prop_side": -1,
            "gesture": {"amp": 0.9, "speed": 0.9},
        },
        "catchphrases": {
            "entrance": "Let's talk about the thread you don't want me to pull.",
            "objection_won": "Thank you. That's exactly the point.",
            "objection_lost": "Fine. I have others.",
            "closing_open": "There is one thread left. Watch it unravel.",
            "verdict_win": "I told you where this was going.",
            "verdict_loss": "This isn't finished. Not by a long way.",
        },
        "style_prompt": (
            "Argue like Annalise Keating: methodical, ruthless, and unnervingly perceptive. Instead of "
            "attacking everything, identify the single weakest thread in the user's argument and relentlessly "
            "pull at it until it unravels, ask pointed, uncomfortable questions, and remain composed and "
            "controlled throughout."
        ),
    },
}
DEFAULT_BOT = "ross"
CATCHPHRASE_FRAMES = 80  # how long a fired catchphrase banner stays up (pop-in, hold, fade)


def _bot_case_complexity(elo):
    """Maps a bot's ELO rating to a case-complexity instruction, replacing
    the old flat easy/medium/hard difficulty buckets."""
    if elo < 1200:
        return "Keep the facts and legal issue relatively straightforward — good for someone new to moot court."
    elif elo < 2000:
        return "Use realistic, moderately complex facts with at least one genuine point of legal ambiguity."
    else:
        return ("Make the facts and legal issue genuinely complex and tightly contested, with multiple "
                 "plausible interpretations — appropriate for a competitive moot court tournament.")

US_AMENDMENTS = [
    (1, "Freedom of speech, religion, press, assembly, and petition."),
    (2, "Right to keep and bear arms."),
    (3, "No quartering of soldiers in private homes without consent."),
    (4, "Protection against unreasonable searches and seizures; warrant requirements."),
    (5, "Grand jury indictment, double jeopardy, self-incrimination, due process, eminent domain compensation."),
    (6, "Right to a speedy public trial, impartial jury, counsel, and to confront witnesses."),
    (7, "Right to a jury trial in civil cases."),
    (8, "No excessive bail or fines; no cruel and unusual punishment."),
    (9, "Rights not listed in the Constitution are retained by the people."),
    (10, "Powers not given to the federal government are reserved to the states or the people."),
    (11, "States have immunity from certain lawsuits in federal court."),
    (12, "Revised procedure for electing the President and Vice President."),
    (13, "Abolished slavery and involuntary servitude."),
    (14, "Citizenship rights, equal protection, and due process for all persons."),
    (15, "Right to vote regardless of race, color, or previous condition of servitude."),
    (16, "Allows Congress to levy an income tax."),
    (17, "Direct election of U.S. Senators by popular vote."),
    (18, "Prohibition of alcohol (later repealed by the 21st)."),
    (19, "Women's right to vote."),
    (20, "Sets the terms and dates for Congress and presidential transitions ('lame duck' amendment)."),
    (21, "Repeal of Prohibition (the 18th Amendment)."),
    (22, "Limits the President to two elected terms."),
    (23, "Grants Washington D.C. electoral votes for President."),
    (24, "Bans poll taxes as a voting requirement."),
    (25, "Presidential succession and procedures for incapacity."),
    (26, "Lowers the voting age to 18."),
    (27, "Congressional pay raises take effect only after the next election."),
]

COURT_EXPERIENCE_LEVELS = {
    "beginner": "Write for someone completely new to law — plain everyday language, avoid jargon where "
                "possible, explain any necessary legal term in parentheses the first time it's used.",
    "experienced": "Write for a law student with some background — standard legal terminology is fine "
                   "without hand-holding, but keep sentence structure reasonably clear.",
    "advanced": "Write for an experienced law student or practitioner — full legal terminology, precise "
                "doctrinal language, no simplification.",
}

OBJECTION_TYPES = ["Relevance", "Hearsay", "Speculation", "Argumentative",
                    "Lack of foundation", "Assumes facts not in evidence", "Calls for a legal conclusion"]

# One real evidentiary/procedural standard per ground, given to the judge at
# ruling time so SUSTAINED/OVERRULED tracks what the ground actually
# requires instead of a coin flip. Note what's deliberately NOT on the list
# above: classic examination-only objections like "Leading question,"
# "Asked and answered," "Badgering the witness," or "Compound question" are
# real grounds, but in this app's structure only the user ever questions a
# witness (during cross-exam) and only the user ever raises objections —
# there's no scenario where the bot examines a witness in front of the user,
# so those grounds would never have a valid target to point at. Rather than
# offer buttons that can't correspond to anything that actually happened,
# the list is limited to grounds that can always be raised against a
# spoken argument or assertion, which is the only thing the bot ever does.
OBJECTION_STANDARDS = {
    "Relevance": "Sustain only if the statement has no tendency to make a disputed fact more or less probable "
                 "(akin to FRE 401/402) — a weak or unpersuasive point is not the same as an irrelevant one.",
    "Hearsay": "Sustain only if the statement relays someone else's out-of-court assertion offered for its truth, "
               "and no exception plausibly applies (e.g., a party's own admission, present-sense impression, "
               "or a statement offered only to show its effect on the listener rather than for its truth).",
    "Speculation": "Sustain only if the speaker asserts something beyond their own knowledge or the case record — "
                   "a reasonable inference clearly drawn from stated facts is argument, not speculation.",
    "Argumentative": "Sustain only if the statement is phrased to badger, editorialize, or argue with the other "
                      "side rather than to make a substantive legal point — forceful or blunt is not the same as "
                      "argumentative.",
    "Lack of foundation": "Sustain only if the speaker asserts a fact without anything in the record establishing "
                          "how they'd know it or that it's actually part of the case.",
    "Assumes facts not in evidence": "Sustain only if the statement treats a genuinely disputed or unestablished "
                                      "fact as already proven, rather than characterizing a fact that's actually "
                                      "in the record.",
    "Calls for a legal conclusion": "Sustain only if the statement improperly announces what the law requires or "
                                     "concludes in a way reserved for the court, rather than arguing from facts "
                                     "and law toward a conclusion, which is what closing/argument is for.",
}

# ── Lesson layer: curriculum data ────────────────────────────────────────────
# Deliberately separate from BOT_CHARACTERS/OBJECTION_STANDARDS in *shape*
# (lessons are their own dict, not folded into the trial data), but lessons
# reference those dicts by key (bot ids, objection-ground names) rather than
# duplicating any content, so a lesson on hearsay and the in-trial hearsay
# ruling always agree on what the standard actually is.
LESSON_XP = 20
LESSON_XP_REVIEW = 10  # awarded for passing a drill pulled from the review queue

SKILL_TREE = [
    {
        "id": "objections_basics",
        "title": "Objections I",
        "blurb": "Relevance and hearsay — the two grounds you'll raise most.",
        "unlocks_bot": "hutz",
        "lessons": ["relevance_intro", "hearsay_intro"],
    },
    {
        "id": "objections_ii",
        "title": "Objections II",
        "blurb": "Speculation and lack of foundation — knowing what a witness can vouch for.",
        "unlocks_bot": "woods",
        "lessons": ["speculation_intro", "foundation_intro"],
    },
    {
        "id": "objections_iii",
        "title": "Objections III",
        "blurb": "Argumentative questions and facts assumed but never proven.",
        "unlocks_bot": "gambini",
        "lessons": ["argumentative_intro", "assumed_facts_intro"],
    },
    {
        "id": "argument_structure",
        "title": "Argument Structure",
        "blurb": "Building a persuasive argument, and answering the other side's best point.",
        "unlocks_bot": "ross",
        "lessons": ["irac_intro", "counterargument_intro"],
    },
    {
        "id": "evidence_basics",
        "title": "Evidence Basics",
        "blurb": "Getting an exhibit in properly, and challenging a witness's credibility.",
        "unlocks_bot": "mccoy",
        "lessons": ["exhibit_authentication_intro", "witness_credibility_intro"],
    },
]

# Each lesson: a concept blurb, 2-3 short drills (statically authored, scored
# client-side — no API call needed for a multiple-choice check), and one
# mini-argument (1-2 exchanges against a bot pulled from BOT_CHARACTERS,
# scored pass/fail by a single lightweight API call). This is intentionally
# lighter than the trial engine's rubric/verdict machinery — see
# lesson_score_mini_argument below for why.
LESSONS = {
    "relevance_intro": {
        "skill": "objections_basics",
        "title": "Relevance",
        "objection_ref": "Relevance",
        "concept": (
            "Relevance means a statement has some tendency to make a disputed fact more or less probable. "
            "It's a low bar — weak or unpersuasive is not the same as irrelevant. Sustain a relevance "
            "objection only when the statement genuinely has no bearing on anything at issue in the case."
        ),
        "drills": [
            {
                "id": "rel_d1", "type": "multiple_choice",
                "prompt": "Witness: \"The defendant has always been a snappy dresser.\" This is offered in a "
                          "breach-of-contract case about a missed delivery deadline. Objection?",
                "options": ["Relevance", "Hearsay", "Speculation", "No objection"],
                "answer": "Relevance",
                "feedback_correct": "Right — a defendant's fashion sense has no bearing on whether a delivery "
                                     "deadline was missed.",
                "feedback_wrong": "Not quite. Ask what disputed fact this statement could possibly make more "
                                   "or less probable — here, nothing about the missed deadline.",
            },
            {
                "id": "rel_d2", "type": "multiple_choice",
                "prompt": "Same case. Witness: \"The defendant emailed me the week before, saying he was behind "
                          "schedule on three other jobs.\" Objection?",
                "options": ["Relevance", "Hearsay", "No objection"],
                "answer": "No objection",
                "feedback_correct": "Correct — this bears directly on whether the defendant could meet the "
                                     "deadline, so it's relevant (and it's the defendant's own statement, not "
                                     "hearsay excluded for its truth).",
                "feedback_wrong": "Reconsider: does being behind on other jobs make it more or less probable "
                                   "the deadline was missed? It does — that's relevant, not objectionable.",
            },
        ],
        "mini_argument": {
            "bot_id": "hutz",
            "fact_pattern": (
                "Contract case: your client says a shipment arrived late. Opposing counsel is trying to "
                "introduce testimony that your client 'has filed complaints like this before.'"
            ),
            "bot_opens": "Your Honor, this pattern of complaints goes to credibility — the jury should hear it.",
            "coach_goal": "Argue that this is irrelevant to whether THIS shipment was late, not a character contest.",
        },
    },
    "hearsay_intro": {
        "skill": "objections_basics",
        "title": "Hearsay",
        "objection_ref": "Hearsay",
        "concept": (
            "Hearsay is an out-of-court statement offered to prove the truth of what it says. It's excluded "
            "unless an exception applies — most often, the statement is a party's own admission, or it's "
            "offered for some purpose other than proving it's true (e.g., its effect on the listener)."
        ),
        "drills": [
            {
                "id": "hs_d1", "type": "multiple_choice",
                "prompt": "Witness: \"My neighbor told me she saw the defendant leaving at midnight,\" offered "
                          "to prove the defendant left at midnight. Objection?",
                "options": ["Hearsay", "Speculation", "Lack of foundation", "No objection"],
                "answer": "Hearsay",
                "feedback_correct": "Right — an out-of-court statement offered for its truth, and no exception "
                                     "applies here.",
                "feedback_wrong": "Look again at who's actually making the underlying claim — it's the "
                                   "neighbor's out-of-court statement, offered to prove it's true.",
            },
            {
                "id": "hs_d2", "type": "multiple_choice",
                "prompt": "Witness: \"The defendant told me, 'I did it.'\" Objection?",
                "options": ["Hearsay", "No objection — admission", "Speculation"],
                "answer": "No objection — admission",
                "feedback_correct": "Correct — a party's own statement, offered against them, is excluded from "
                                     "the hearsay bar as an admission.",
                "feedback_wrong": "This is the defendant's own statement, offered against the defendant — that's "
                                   "the admission exception, not hearsay.",
            },
            {
                "id": "hs_d3", "type": "fill_in_blank",
                "prompt": "Complete a plausible case citation format for a hearsay precedent:",
                "template": "{blank1} v. {blank2}, {blank3} Cir. {blank4}",
                "answer_shape": "A party name, another party name, a circuit number (1-11), and a plausible year.",
                "feedback_correct": "That's the right shape — party v. party, circuit, year. (The actual "
                                     "case doesn't need to be real for this drill; it's about citation form.)",
            },
        ],
        "mini_argument": {
            "bot_id": "ross",
            "fact_pattern": (
                "Same contract case. Opposing counsel wants to introduce: 'A bystander told my client the "
                "delivery truck looked like it was speeding.'"
            ),
            "bot_opens": "This bystander statement corroborates our timeline — it should come in.",
            "coach_goal": "Argue hearsay: it's an out-of-court statement offered for its truth, and no "
                          "exception fits — the bystander isn't a party, and this isn't about effect on a listener.",
        },
    },
    "speculation_intro": {
        "skill": "objections_ii",
        "title": "Speculation",
        "objection_ref": "Speculation",
        "concept": (
            "Speculation objections stop a witness from asserting something beyond their own knowledge or "
            "the case record. But a reasonable inference clearly drawn from a fact the witness actually "
            "observed is argument, not speculation — that distinction is the whole drill."
        ),
        "drills": [
            {
                "id": "spec_d1", "type": "multiple_choice",
                "prompt": "Witness: \"I think the driver must have been on his phone — that's usually why "
                          "people run lights.\" Objection?",
                "options": ["Speculation", "Lack of foundation", "No objection"],
                "answer": "Speculation",
                "feedback_correct": "Right — nothing in the record shows this driver was on his phone; that's "
                                     "a guess dressed as testimony.",
                "feedback_wrong": "This witness is asserting something about a stranger's private conduct with "
                                   "no basis in what they actually observed — that's speculation.",
            },
            {
                "id": "spec_d2", "type": "multiple_choice",
                "prompt": "Witness: \"The skid marks started ten feet before the crosswalk, so the car was "
                          "already braking when it hit him.\" Objection?",
                "options": ["Speculation", "No objection"],
                "answer": "No objection",
                "feedback_correct": "Correct — this is a reasonable inference drawn from a fact the witness "
                                     "actually observed (the skid marks), which is fine.",
                "feedback_wrong": "Look at what the witness actually saw — the skid marks are an observed "
                                   "fact, and an inference drawn from an observed fact isn't speculation.",
            },
        ],
        "mini_argument": {
            "bot_id": "woods",
            "fact_pattern": (
                "Contract case. Opposing counsel's witness says: \"The delivery driver definitely knew the "
                "shipment was fragile.\""
            ),
            "bot_opens": "Our witness's statement about what the driver knew should stand as-is.",
            "coach_goal": "Argue speculation: the witness can't know another person's internal knowledge "
                          "without any stated basis for it in the record.",
        },
    },
    "foundation_intro": {
        "skill": "objections_ii",
        "title": "Lack of Foundation",
        "objection_ref": "Lack of foundation",
        "concept": (
            "A witness can't vouch for a fact — like identifying a signature or handwriting — until someone "
            "establishes how they'd actually know it. Once that basis (foundation) is laid, the same "
            "testimony becomes perfectly fine."
        ),
        "drills": [
            {
                "id": "found_d1", "type": "multiple_choice",
                "prompt": "Witness, with no prior questions about how they know his handwriting, says: "
                          "\"That's John's signature on the contract.\" Objection?",
                "options": ["Lack of foundation", "Speculation", "No objection"],
                "answer": "Lack of foundation",
                "feedback_correct": "Right — before identifying a signature, the witness needs to first "
                                     "establish they're actually familiar with it.",
                "feedback_wrong": "Nothing yet establishes how this witness would recognize John's "
                                   "handwriting — that gap is what makes it lack foundation.",
            },
            {
                "id": "found_d2", "type": "multiple_choice",
                "prompt": "Witness first says: \"I've seen John sign dozens of documents over the years.\" "
                          "Then: \"That's John's signature.\" Objection?",
                "options": ["Lack of foundation", "No objection"],
                "answer": "No objection",
                "feedback_correct": "Correct — the foundation was laid first (familiarity with his "
                                     "handwriting), so the identification is now proper.",
                "feedback_wrong": "The witness already explained how they know his handwriting — that's "
                                   "exactly what foundation means, so this is fine now.",
            },
        ],
        "mini_argument": {
            "bot_id": "gambini",
            "fact_pattern": (
                "Opposing counsel's witness, without ever being asked how they recognize it, says: \"That's "
                "definitely his handwriting on the note.\""
            ),
            "bot_opens": "Our witness recognizes the handwriting — that settles it.",
            "coach_goal": "Object that no foundation was laid for this witness's ability to recognize the "
                          "handwriting before they identified it.",
        },
    },
    "argumentative_intro": {
        "skill": "objections_iii",
        "title": "Argumentative",
        "objection_ref": "Argumentative",
        "concept": (
            "An argumentative objection stops a question phrased to badger or editorialize rather than make "
            "a substantive point. Forceful or blunt isn't the same thing — a real impeachment question, even "
            "a sharp one, is fine."
        ),
        "drills": [
            {
                "id": "arg_d1", "type": "multiple_choice",
                "prompt": "Cross-examination: \"So you expect this jury to believe your client, who's changed "
                          "his story three times?\" Objection?",
                "options": ["Argumentative", "Speculation", "No objection"],
                "answer": "Argumentative",
                "feedback_correct": "Right — this is phrased to badger and editorialize about credibility, "
                                     "not to elicit a substantive answer.",
                "feedback_wrong": "Notice this isn't really asking anything — it's arguing with the witness "
                                   "under the guise of a question.",
            },
            {
                "id": "arg_d2", "type": "multiple_choice",
                "prompt": "Cross-examination: \"You said the light was green — but you also said you weren't "
                          "looking at the light, didn't you?\" Objection?",
                "options": ["Argumentative", "No objection"],
                "answer": "No objection",
                "feedback_correct": "Correct — blunt, but this is a legitimate impeachment question pointing "
                                     "at an actual inconsistency, not badgering.",
                "feedback_wrong": "This is pointed at a real prior inconsistency, which is fair game — forceful "
                                   "isn't the same as argumentative.",
            },
        ],
        "mini_argument": {
            "bot_id": "mccoy",
            "fact_pattern": (
                "Opposing counsel, cross-examining your client, says: \"You want this jury to believe someone "
                "who's already lied under oath once, right?\""
            ),
            "bot_opens": "Answer honestly — haven't you lied under oath before?",
            "coach_goal": "Object that this is argumentative — it's phrased to badger and editorialize about "
                          "credibility rather than ask a real question.",
        },
    },
    "assumed_facts_intro": {
        "skill": "objections_iii",
        "title": "Assumes Facts Not in Evidence",
        "objection_ref": "Assumes facts not in evidence",
        "concept": (
            "This objection stops a question or statement that treats a genuinely disputed fact as already "
            "proven. If the fact is actually established in the record, characterizing it isn't objectionable "
            "— the test is whether it's real proven, not just convenient."
        ),
        "drills": [
            {
                "id": "assume_d1", "type": "multiple_choice",
                "prompt": "\"Since you already admitted the contract was breached, why did you still ship the "
                          "goods?\" — breach is still disputed. Objection?",
                "options": ["Assumes facts not in evidence", "Argumentative", "No objection"],
                "answer": "Assumes facts not in evidence",
                "feedback_correct": "Right — breach hasn't been established, so treating it as already "
                                     "admitted is the problem.",
                "feedback_wrong": "The word \"admitted\" is doing a lot of work here for a fact that's still "
                                   "genuinely disputed — that's the objectionable part.",
            },
            {
                "id": "assume_d2", "type": "multiple_choice",
                "prompt": "\"Since you personally signed the invoice, why does it list a different name?\" — "
                          "the signing is already an established, undisputed fact. Objection?",
                "options": ["Assumes facts not in evidence", "No objection"],
                "answer": "No objection",
                "feedback_correct": "Correct — this characterizes a fact that's actually in the record, so "
                                     "there's nothing to object to.",
                "feedback_wrong": "Check whether the underlying fact (that they signed it) is actually "
                                   "established — here it is, so referencing it is fine.",
            },
        ],
        "mini_argument": {
            "bot_id": "ross",
            "fact_pattern": (
                "Opposing counsel asks: \"Since your client clearly missed the deadline on purpose, why "
                "should the jury be lenient?\" — intent has never been established."
            ),
            "bot_opens": "Since your client clearly missed the deadline on purpose, why should the jury be lenient?",
            "coach_goal": "Object that this assumes a disputed fact (intent) that's never been proven in the record.",
        },
    },
    "irac_intro": {
        "skill": "argument_structure",
        "title": "IRAC Structure",
        "concept": (
            "A persuasive legal argument states the Issue, the Rule that governs it, Applies that rule to "
            "the actual facts, and only then reaches a Conclusion. Skipping straight from a fact to a "
            "conclusion — without ever stating the rule that connects them — is the single most common "
            "structural weakness in a closing."
        ),
        "drills": [
            {
                "id": "irac_d1", "type": "multiple_choice",
                "prompt": "Closing line: \"My client obviously wins — the shipment was late.\" What's missing?",
                "options": ["The rule connecting lateness to winning", "The issue", "A citation", "Nothing — it's complete"],
                "answer": "The rule connecting lateness to winning",
                "feedback_correct": "Right — it jumps from a fact (late shipment) straight to a conclusion "
                                     "(wins) without ever stating what rule makes that fact decisive.",
                "feedback_wrong": "Look at what's missing between \"the shipment was late\" and \"my client "
                                   "wins\" — nothing tells the jury what rule connects the two.",
            },
            {
                "id": "irac_d2", "type": "multiple_choice",
                "prompt": "\"The contract required delivery by the 1st. A shipment after the agreed date "
                          "breaches the contract's own terms. It arrived on the 5th. So this was a breach.\" "
                          "Which structure does this reflect?",
                "options": ["Rule, then Application, then Conclusion", "Conclusion first, then facts", "Rule only, no application"],
                "answer": "Rule, then Application, then Conclusion",
                "feedback_correct": "Correct — the rule is stated first, applied to the specific facts, and "
                                     "only then does the conclusion follow.",
                "feedback_wrong": "Trace it in order: rule stated first, then the facts are measured against "
                                   "it, then the conclusion — that's what makes it land.",
            },
        ],
        "mini_argument": {
            "bot_id": "gambini",
            "fact_pattern": "Closing argument moment on the same breach-of-contract case.",
            "bot_opens": "The jury doesn't need a legal lecture — just tell them who wins.",
            "coach_goal": "Push back by actually stating the rule (a firm-date contract is breached by late "
                          "delivery) and applying it to the facts — don't just announce a winner.",
        },
    },
    "counterargument_intro": {
        "skill": "argument_structure",
        "title": "Answering Counterarguments",
        "concept": (
            "Naming and defeating the other side's strongest likely point — before they raise it, or in "
            "direct reply — is more persuasive than only restating your own case. A small, controlled "
            "concession followed by why it doesn't matter is a real technique, not a weakness."
        ),
        "drills": [
            {
                "id": "counter_d1", "type": "multiple_choice",
                "prompt": "Opponent's strongest likely point: the contract's deadline was \"approximate,\" "
                          "not firm. Which response best pre-empts it?",
                "options": [
                    "Point to contract language showing the date wasn't stated as approximate",
                    "Ignore it and hope the jury doesn't think of it",
                    "Attack opposing counsel's tone instead",
                ],
                "answer": "Point to contract language showing the date wasn't stated as approximate",
                "feedback_correct": "Right — naming the opponent's best point and directly defeating it with "
                                     "the actual contract language beats hoping it doesn't come up.",
                "feedback_wrong": "Ask which option actually neutralizes the \"approximate deadline\" claim, "
                                   "rather than sidestepping it.",
            },
            {
                "id": "counter_d2", "type": "multiple_choice",
                "prompt": "\"It's true the deadline language is a bit informal, but even so, both parties' "
                          "emails treated it as firm.\" Is this a good technique?",
                "options": [
                    "Yes — a small, controlled concession followed by why it doesn't matter",
                    "No — never mention the other side's argument at all",
                ],
                "answer": "Yes — a small, controlled concession followed by why it doesn't matter",
                "feedback_correct": "Correct — conceding a minor point on your own terms, then showing why it "
                                     "doesn't change the outcome, reads as more credible than denying everything.",
                "feedback_wrong": "Reconsider — refusing to ever acknowledge the other side's point can read "
                                   "as evasive; a controlled concession is often stronger.",
            },
        ],
        "mini_argument": {
            "bot_id": "specter",
            "fact_pattern": "Opposing counsel is about to argue the delivery deadline was only approximate.",
            "bot_opens": "Let's be honest — nothing in this contract nails down a hard date.",
            "coach_goal": "Respond by directly naming and rebutting that exact point — with specific contract "
                          "language — rather than ignoring it or only restating your own claim.",
        },
    },
    "exhibit_authentication_intro": {
        "skill": "evidence_basics",
        "title": "Authenticating Exhibits",
        "objection_ref": "Lack of foundation",
        "concept": (
            "An exhibit isn't evidence just because someone holds it up. A witness with actual knowledge has "
            "to establish what it is and that it's genuine — authentication — before either side can rely on "
            "it. This is the same foundation requirement from Objections II, applied to documents instead of "
            "testimony."
        ),
        "drills": [
            {
                "id": "auth_d1", "type": "multiple_choice",
                "prompt": "Opposing counsel holds up a printed email and says \"This proves the deadline was "
                          "extended,\" without asking any witness whether they recognize or received it. "
                          "Best response?",
                "options": [
                    "Object — no foundation/authentication for the exhibit",
                    "No objection — it's clearly relevant",
                    "Object — hearsay only",
                ],
                "answer": "Object — no foundation/authentication for the exhibit",
                "feedback_correct": "Right — relevance isn't the issue; no one has established this document "
                                     "is what counsel claims it is.",
                "feedback_wrong": "The content might well be relevant — the problem is that no witness has "
                                   "established what this document actually is.",
            },
            {
                "id": "auth_d2", "type": "multiple_choice",
                "prompt": "Witness testifies: \"I recognize this as the email I personally sent on March 3rd,\" "
                          "then counsel offers it. Objection?",
                "options": ["No objection — properly authenticated", "Lack of foundation", "Hearsay"],
                "answer": "No objection — properly authenticated",
                "feedback_correct": "Correct — the witness just laid the foundation themselves by identifying "
                                     "it from personal knowledge.",
                "feedback_wrong": "The witness just explained exactly how they know what this document is — "
                                   "that's the foundation being laid, not missing.",
            },
        ],
        "mini_argument": {
            "bot_id": "woods",
            "fact_pattern": (
                "Opposing counsel tries to rely on a text message screenshot without any witness identifying "
                "who sent it or how it was obtained."
            ),
            "bot_opens": "This text message speaks for itself — it shows the extension.",
            "coach_goal": "Argue it hasn't been authenticated — no witness has established who sent it or "
                          "that it's accurate, so it can't be relied on yet.",
        },
    },
    "witness_credibility_intro": {
        "skill": "evidence_basics",
        "title": "Challenging Credibility",
        "concept": (
            "Impeaching a witness — through bias, motive, or a real inconsistency — is a persuasive tool "
            "separate from formal objections. The skill is picking the actual weak thread (a stated fact "
            "that undercuts the testimony) rather than an irrelevant detail that doesn't."
        ),
        "drills": [
            {
                "id": "cred_d1", "type": "multiple_choice",
                "prompt": "Which fact about a witness is fair game for impeachment?",
                "options": [
                    "The witness is the plaintiff's business partner and stands to gain from the verdict",
                    "The witness has an unrelated hobby",
                    "The witness's name is unusual",
                ],
                "answer": "The witness is the plaintiff's business partner and stands to gain from the verdict",
                "feedback_correct": "Right — a financial stake in the outcome is a textbook bias angle.",
                "feedback_wrong": "Ask which fact actually gives this witness a reason to shade their "
                                   "testimony — only one of these does.",
            },
            {
                "id": "cred_d2", "type": "multiple_choice",
                "prompt": "Witness said \"around noon\" in one interview and \"a little after noon\" in "
                          "another. Worth raising as an inconsistency?",
                "options": [
                    "No — this is a trivial variation, not a real inconsistency",
                    "Yes — any variation at all destroys credibility",
                ],
                "answer": "No — this is a trivial variation, not a real inconsistency",
                "feedback_correct": "Correct — chasing trivial wording differences can make your own "
                                     "cross-examination look weak; save it for a real contradiction.",
                "feedback_wrong": "This difference doesn't actually contradict anything the witness said — "
                                   "raising it risks looking like you don't have a real point.",
            },
        ],
        "mini_argument": {
            "bot_id": "keating",
            "fact_pattern": (
                "The opposing witness testified their view of the crash was unobstructed, but earlier said "
                "they were \"looking at their phone\" at the moment it happened."
            ),
            "bot_opens": "Our witness saw everything clearly — their testimony should stand as reliable.",
            "coach_goal": "Point out the actual inconsistency — claiming a clear view while admitting they "
                          "were looking at their phone — rather than an irrelevant detail.",
        },
    },
}


_SENTENCE_ABBREVS = ["Mr.", "Mrs.", "Ms.", "Dr.", "Inc.", "Corp.", "Ltd.", "Co.",
                     "vs.", "etc.", "e.g.", "i.e.", "U.S.", "U.K.", "Jr.", "Sr.",
                     "Prof.", "St.", "No."]


def _estimate_syllable_count(text):
    """Rough syllable estimate (vowel-group heuristic) used only to pace the
    courtroom speaking animation — not linguistically exact, just enough to
    make mouth/gesture beats roughly track how long a line 'takes to say'."""
    words = re.findall(r"[A-Za-z']+", text or "")
    total = 0
    for w in words:
        w = w.lower()
        groups = re.findall(r"[aeiouy]+", w)
        n = len(groups)
        if w.endswith("e") and n > 1:
            n -= 1
        total += max(1, n)
    return max(1, total)


def court_split_sentences(text):
    """Splits case text into sentences for click-to-analyze Sentence mode."""
    placeholder = text
    for ab in _SENTENCE_ABBREVS:
        placeholder = placeholder.replace(ab, ab.replace(".", "\x00"))
    parts = re.split(r'(?<=[.!?])\s+', placeholder.strip())
    return [p.replace("\x00", ".") for p in parts if p.strip()]


# ── Claude client ────────────────────────────────────────────────────────────
_client = None


def init_claude(api_key):
    global _client
    _client = anthropic.Anthropic(api_key=api_key)


# ── Moot Court engine (background-thread functions operating on `app`) ──────
def generate_court_case(app, source, case_type):
    """Generates a case for the moot court simulation — either a fictional
    hypo (law-school style) or a simplified real landmark case — and
    randomly assigns which side the user argues."""
    def _run():
        try:
            area_clauses = {
                "civil": "a civil case", "criminal": "a criminal case",
                "corporate": "a corporate law case (e.g. shareholder disputes, mergers, fiduciary duty, securities)",
                "constitutional": "a constitutional law case (cite the specific U.S. constitutional amendment or clause at issue)",
                "contract": "a contract law case (formation, breach, remedies, or interpretation disputes)",
                "tort": "a tort law case (negligence, product liability, defamation, or similar)",
                "ip": "an intellectual property / patent law case (infringement, validity, or licensing disputes)",
                "random": "any practice area of your choosing — civil, criminal, corporate, constitutional, contract, tort, or IP",
            }
            type_clause = area_clauses.get(case_type, "a civil case")
            bot = BOT_CHARACTERS.get(app._court_character, BOT_CHARACTERS[DEFAULT_BOT])
            complexity_clause = _bot_case_complexity(bot["elo"])
            experience_clause = COURT_EXPERIENCE_LEVELS.get(app._court_experience, COURT_EXPERIENCE_LEVELS["experienced"])
            json_fields = (
                "{\"title\": \"short case name like 'Smith v. Jones'\", \"case_type\": \"the practice area\", "
                "\"facts\": \"a substantial, detailed factual background — at least 6-10 sentences covering "
                "the parties, what happened, key dates/events, and any disputed facts, written like a real "
                "case file summary, not a one-line gist\", \"issue\": \"the core legal question in one or "
                "two sentences\", \"amendment\": \"the specific U.S. constitutional amendment or article at "
                "issue if this is a constitutional case, else null\", \"side_a\": \"label for one side e.g. "
                "Plaintiff/Prosecution\", \"side_b\": \"label for the other side e.g. Defendant\", "
                "\"exhibits\": [{\"label\": \"Exhibit A\", \"description\": \"what it is, e.g. a contract, "
                "text message, photo, or document, and what it shows — 1-2 sentences\"}, "
                "{\"label\": \"Exhibit B\", \"description\": \"a second exhibit, same format\"}], "
                "\"witness\": {\"name\": \"witness full name\", \"role\": \"their relationship to the case, "
                "e.g. 'eyewitness' or 'the defendant's business partner'\", \"summary\": \"2-3 sentences on "
                "what they would testify to and any bias or credibility issue a cross-examiner could probe\"}}"
            )
            if source == "fictional":
                prompt = (
                    f"Create a realistic, detailed law-school-style hypothetical for {type_clause}. "
                    f"{complexity_clause} {experience_clause} "
                    f"Return ONLY JSON, no markdown fences: {json_fields}."
                )
            else:
                prompt = (
                    f"Pick a well-known real landmark case in the area of {type_clause} "
                    f"(e.g. something a law student would study) and summarize it in detail for a moot "
                    f"court practice exercise. {complexity_clause} {experience_clause} "
                    f"Return ONLY JSON, no markdown fences: {json_fields}. "
                    f"Note this is a simplified teaching version, not a verbatim case brief."
                )
            resp = _client.messages.create(model="claude-sonnet-4-5", max_tokens=1400,
                                            messages=[{"role": "user", "content": prompt}])
            raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
            data = _json.loads(raw)
            your_side, opp_side = random.choice([(data["side_a"], data["side_b"]), (data["side_b"], data["side_a"])])
            raw_exhibits = data.get("exhibits")
            exhibits = []
            if isinstance(raw_exhibits, list):
                for ex in raw_exhibits[:3]:
                    if isinstance(ex, dict) and ex.get("label") and ex.get("description"):
                        exhibits.append({"label": str(ex["label"]).strip(), "description": str(ex["description"]).strip()})
            raw_witness = data.get("witness")
            witness = None
            if isinstance(raw_witness, dict) and raw_witness.get("name"):
                witness = {"name": str(raw_witness.get("name", "")).strip(),
                           "role": str(raw_witness.get("role", "")).strip(),
                           "summary": str(raw_witness.get("summary", "")).strip()}
            app._court_case = {
                "title": data.get("title", "Untitled Case"),
                "case_type": data.get("case_type", case_type if case_type != "random" else "civil"),
                "facts": data.get("facts", ""),
                "issue": data.get("issue", ""),
                "amendment": data.get("amendment") or None,
                "your_side": your_side,
                "jarvis_side": opp_side,
                "source": source,
                "exhibits": exhibits,
                "witness": witness,
            }
            app._court_transcript = []
            app._court_verdict = None
            app._court_objections = []
            app._court_state = "briefing"
        except Exception as e:
            app._court_state = "setup"
            app._court_loading = False
            app.set_status(f"Error generating case: {e}")
            return
        app._court_loading = False
        app.set_status("STANDBY")
    threading.Thread(target=_run, daemon=True).start()


def court_phase_respond(app, user_text, phase):
    """The selected bot character responds in character for the current
    phase, playing either opposing counsel (argues against the user's side)
    or judge (questions/challenges, stays neutral), using the case facts and
    the transcript so far for continuity."""
    def _run():
        case = app._court_case
        bot = BOT_CHARACTERS.get(app._court_character, BOT_CHARACTERS[DEFAULT_BOT])
        role_desc = ("You are opposing counsel, arguing FOR " + case["jarvis_side"] + " and against the "
                     "user's side (" + case["your_side"] + "). Be persuasive and adversarial, but fair — "
                     "this is a practice exercise. If the user's last argument is genuinely objectionable "
                     "(e.g. clearly irrelevant, speculative, or argumentative), you may open your response "
                     "with a brief in-character objection like 'Objection — relevance, Your Honor' before "
                     "continuing your argument, but don't do this every turn — only when it's realistic." if app._court_jarvis_role == "opposing" else
                     "You are the presiding judge. Stay neutral, ask probing questions about the user's "
                     "argument, point out weaknesses or unclear reasoning, but do not argue for either side.")
        role_desc = f"You are playing {bot['name']}. {role_desc}"
        transcript_text = "\n".join(f"{t['speaker']} ({t['phase']}): {t['text']}" for t in app._court_transcript[-10:])
        realism_clause = (
            "Stay inside real trial procedure: don't introduce new facts, evidence, or exhibits that aren't "
            "already in the case file or transcript, don't reference anything happening outside this simulated "
            "courtroom, and keep the underlying legal argument coherent even when the character's competence "
            "or honesty is the joke — the personality should color how the argument is delivered, not whether "
            "it's a real argument at all."
        )
        prompt = (
            f"MOOT COURT SIMULATION\nCase: {case['title']}\nFacts: {case['facts']}\nLegal issue: {case['issue']}\n"
            f"User is arguing for: {case['your_side']}. You represent/preside over: {case['jarvis_side']}.\n"
            f"Current phase: {phase}.\n{role_desc}\nOpponent style: {bot['style_prompt']}\n{realism_clause}\n\n"
            f"Transcript so far:\n{transcript_text}\n\nUSER ({phase}): {user_text}\n\n"
            f"Respond in character, 2-4 sentences, staying strictly in the courtroom simulation."
        )
        try:
            resp = _client.messages.create(model="claude-sonnet-4-5", max_tokens=400,
                                            messages=[{"role": "user", "content": prompt}])
            reply = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        except Exception as e:
            reply = f"(Error generating response: {e})"
        app._court_transcript.append({"phase": phase, "speaker": bot["label"], "text": reply})
        app._court_loading = False
        app.set_status("STANDBY")
    threading.Thread(target=_run, daemon=True).start()


def court_explain_term(app, word, context_text):
    """Explains a clicked word/phrase from the case briefing, scaled to the
    current experience level."""
    def _run():
        level = app._court_experience
        depth = {
            "beginner": "Explain it simply, 2-3 sentences, as if to someone with zero legal background. "
                        "Avoid using other jargon in your explanation.",
            "experienced": "Explain it in 1-2 sentences, standard legal-education depth — assume basic "
                           "familiarity with legal concepts.",
            "advanced": "Give a terse, precise, technical explanation in one sentence — assume the reader "
                        "already knows adjacent concepts.",
        }.get(level, "Explain it in 1-2 sentences.")
        prompt = (
            f"In the context of this case text: \"{context_text[:300]}\"\n\n"
            f"The reader clicked on: \"{word}\"\n\n"
            f"Explain what this term/word means in this legal context. {depth} "
            f"If it's not actually a legal or technical term (e.g. it's just an ordinary word like 'the' "
            f"or a name), say briefly what role it plays in the sentence instead of inventing a legal "
            f"definition for it."
        )
        try:
            resp = _client.messages.create(model="claude-sonnet-4-5", max_tokens=180,
                                            messages=[{"role": "user", "content": prompt}])
            explanation = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        except Exception as e:
            explanation = f"Couldn't explain that: {e}"
        app._court_term_popup = {**app._court_term_popup, "explanation": explanation} if app._court_term_popup else None
        app._court_term_loading = None
        app.set_status("STANDBY")
    threading.Thread(target=_run, daemon=True).start()


def court_analyze_sentence(app, sentence, full_text):
    """Analyzes the legal significance of a whole sentence the user clicked
    into (Sentence mode)."""
    def _run():
        level = app._court_experience
        depth = {
            "beginner": "Explain in plain language, 3-4 sentences, why this matters for the case — "
                        "avoid jargon, explain any legal term you do need to use.",
            "experienced": "Explain in 2-3 sentences what this establishes or implies legally, at "
                           "standard law-student depth.",
            "advanced": "Give a terse, precise 1-2 sentence analysis of this sentence's doctrinal "
                        "significance — assume full familiarity with legal reasoning.",
        }.get(level, "Explain in 2-3 sentences.")
        prompt = (
            f"Full case text for context: \"{full_text[:500]}\"\n\n"
            f"The reader selected this specific sentence: \"{sentence}\"\n\n"
            f"Analyze the legal significance of this sentence — what fact, element, or implication it "
            f"establishes, and why it matters for the case. {depth}"
        )
        try:
            resp = _client.messages.create(model="claude-sonnet-4-5", max_tokens=260,
                                            messages=[{"role": "user", "content": prompt}])
            explanation = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        except Exception as e:
            explanation = f"Couldn't analyze that: {e}"
        app._court_term_popup = {**app._court_term_popup, "explanation": explanation} if app._court_term_popup else None
        app._court_term_loading = None
        app.set_status("STANDBY")
    threading.Thread(target=_run, daemon=True).start()


def court_generate_hint(app, phase):
    """Generates an on-demand strategy tip for the current phase."""
    def _run():
        case = app._court_case
        transcript_text = "\n".join(f"{t['speaker']} ({t['phase']}): {t['text']}" for t in app._court_transcript[-6:])
        prompt = (
            f"MOOT COURT — STRATEGY TIP\nCase: {case['title']}\nFacts: {case['facts']}\nIssue: {case['issue']}\n"
            f"User argues for: {case['your_side']}. Current phase: {phase}.\n"
            f"Recent exchange:\n{transcript_text}\n\n"
            f"Give the user one concrete, specific tip (2-3 sentences) for what to argue or how to "
            f"strengthen their position right now in this phase. Be a helpful coach, not a participant "
            f"in the trial — speak to the user directly, not in character as opposing counsel or judge."
        )
        try:
            resp = _client.messages.create(model="claude-sonnet-4-5", max_tokens=200,
                                            messages=[{"role": "user", "content": prompt}])
            hint = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        except Exception as e:
            hint = f"Couldn't generate a tip: {e}"
        app._court_hint = hint
        app._court_hint_loading = False
        app.set_status("STANDBY")
    threading.Thread(target=_run, daemon=True).start()


def court_rule_objection(app, objection_type, by):
    """The judge rules SUSTAINED or OVERRULED on the most recent argument in
    the transcript, applying the real evidentiary/procedural standard for
    the specific ground raised, with brief reasoning."""
    def _run():
        case = app._court_case
        bot = BOT_CHARACTERS.get(app._court_character, BOT_CHARACTERS[DEFAULT_BOT])
        recent = app._court_transcript[-3:] if app._court_transcript else []
        recent_text = "\n".join(f"{t['speaker']} ({t['phase']}): {t['text']}" for t in recent) or "(no argument yet this phase)"
        standard = OBJECTION_STANDARDS.get(objection_type, "Apply the real evidentiary standard for this ground.")
        prompt = (
            f"MOOT COURT — OBJECTION RULING\nCase: {case['title']}\nIssue: {case['issue']}\n"
            f"For context, the opposing side is being played as {bot['name']}, who argues in this style: "
            f"{bot['style_prompt']}\n"
            f"Most recent argument:\n{recent_text}\n\n"
            f"{by} objects on grounds of: {objection_type}.\n"
            f"The real standard for this ground: {standard}\n\n"
            f"As the presiding judge, apply that standard to what was actually just said — don't rule on vibes "
            f"or on how the objection type sounds in isolation. If the standard's conditions aren't met, "
            f"OVERRULE even if the objection is understandable or the argument was weak; weak argument is not "
            f"the same as an evidentiary violation. Give a one-sentence reason that names what the standard "
            f"required and whether it was met. Format exactly:\n"
            f"RULING: <SUSTAINED|OVERRULED>\nREASON: <one sentence>"
        )
        try:
            resp = _client.messages.create(model="claude-sonnet-4-5", max_tokens=150,
                                            messages=[{"role": "user", "content": prompt}])
            raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
            ruling_m = re.search(r'RULING:\s*(SUSTAINED|OVERRULED)', raw, re.IGNORECASE)
            reason_m = re.search(r'REASON:\s*(.+)', raw, re.IGNORECASE | re.DOTALL)
            ruling = ruling_m.group(1).upper() if ruling_m else "OVERRULED"
            reason = reason_m.group(1).strip() if reason_m else raw
        except Exception as e:
            ruling, reason = "OVERRULED", f"(Error: {e})"
        app._court_objections.append({"phase": app._court_state, "by": by, "type": objection_type,
                                       "ruling": ruling, "reason": reason})
        app._court_objection_loading = False
        app.set_status("STANDBY")
    threading.Thread(target=_run, daemon=True).start()


def court_cross_exam_respond(app, user_question):
    """The AI plays the case's witness (a persona separate from the selected
    bot opponent), answering the user's question in character."""
    def _run():
        case = app._court_case
        witness = case.get("witness") or {"name": "the witness", "role": "a witness", "summary": ""}
        transcript_text = "\n".join(f"{t['speaker']} ({t['phase']}): {t['text']}"
                                     for t in app._court_transcript[-6:] if t.get("cross_exam"))
        prompt = (
            f"MOOT COURT — WITNESS CROSS-EXAMINATION\nCase: {case['title']}\n"
            f"You are {witness['name']}, {witness['role']}. Background: {witness['summary']}\n\n"
            f"Prior questioning:\n{transcript_text}\n\nQUESTION: {user_question}\n\n"
            f"Answer in character as the witness, 1-3 sentences. Stay consistent with your stated "
            f"background and any bias/credibility issue — you can be evasive, defensive, or candid as "
            f"fits the witness, but don't break character or comment on the simulation."
        )
        try:
            resp = _client.messages.create(model="claude-sonnet-4-5", max_tokens=250,
                                            messages=[{"role": "user", "content": prompt}])
            reply = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
        except Exception as e:
            reply = f"(Error generating response: {e})"
        app._court_transcript.append({"phase": app._court_state, "speaker": f"WITNESS ({witness['name']})",
                                       "text": reply, "cross_exam": True})
        app._court_loading = False
        app.set_status("STANDBY")
    threading.Thread(target=_run, daemon=True).start()


def court_generate_verdict(app):
    """Generates the final verdict + detailed feedback + rubric scores once
    the closing phase is done, based on the full transcript. Also records
    the finished trial in case history."""
    def _run():
        case = app._court_case
        transcript_text = "\n".join(f"{t['speaker']} ({t['phase']}): {t['text']}" for t in app._court_transcript)
        verdict_style = (
            "This is a JURY verdict, not a judge's ruling — give the winner as a vote split out of 9 jurors, "
            "e.g. 'Plaintiff wins, 7-2' or '6-3 in favor of the Defendant', then 2-3 sentences explaining "
            "the jury's reasoning (it's fine if it reads as a close, human, slightly messy decision rather "
            "than a clean legal ruling)." if app._court_jury_mode else
            "This is the presiding judge's ruling — give a clear, reasoned legal verdict."
        )
        prompt = (
            f"MOOT COURT SIMULATION — FINAL VERDICT\nCase: {case['title']}\nIssue: {case['issue']}\n"
            f"User argued for: {case['your_side']}. Opposing/presiding: {case['jarvis_side']}.\n{verdict_style}\n\n"
            f"Full transcript:\n{transcript_text}\n\n"
            f"Return ONLY JSON, no markdown fences: {{\"winner\": \"who won — phrased per the verdict style "
            f"above\", \"reasoning\": \"2-3 sentences on why\", "
            f"\"feedback\": \"a detailed, substantial post-trial breakdown (8-12 sentences) covering: "
            f"what the user argued well, specific weaknesses in their reasoning or use of facts, how "
            f"well they responded to challenges/rebuttals, concrete suggestions for what to argue "
            f"differently next time, and one specific legal skill to work on (e.g. citing authority, "
            f"anticipating counterarguments, structuring an argument). Be specific and reference actual "
            f"moments from the transcript, not generic advice.\", "
            f"\"rubric\": {{\"clarity\": <1-10>, \"use_of_facts\": <1-10>, \"responsiveness\": <1-10>, "
            f"\"persuasiveness\": <1-10>}}}}."
        )
        try:
            resp = _client.messages.create(model="claude-sonnet-4-5", max_tokens=1000,
                                            messages=[{"role": "user", "content": prompt}])
            raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
            verdict = _json.loads(raw)
            rubric = verdict.get("rubric") or {}
            verdict["rubric"] = {k: max(1, min(10, int(v))) for k, v in rubric.items() if isinstance(v, (int, float))}
            app._court_verdict = verdict
            # best-effort read of who won, for the bot's verdict catchphrase only —
            # the winner text is free-form English from the model, so this is a
            # substring heuristic, not authoritative (see notes to the user).
            winner_text = (verdict.get("winner") or "").lower()
            your_side = (case.get("your_side") or "").lower()
            opp_side = (case.get("jarvis_side") or "").lower()
            bot_won = None
            if your_side and your_side in winner_text and not (opp_side and opp_side in winner_text):
                bot_won = False
            elif opp_side and opp_side in winner_text and not (your_side and your_side in winner_text):
                bot_won = True
            app._court_verdict_bot_won = bot_won
        except Exception as e:
            app._court_verdict = {"winner": "Unknown", "reasoning": f"Error: {e}", "feedback": "", "rubric": {}}
            app._court_verdict_bot_won = None
        bot = BOT_CHARACTERS.get(app._court_character, BOT_CHARACTERS[DEFAULT_BOT])
        history = _load_court_history()
        history.append({
            "title": case["title"], "case_type": case["case_type"], "opponent": bot["name"],
            "your_side": case["your_side"], "winner": app._court_verdict.get("winner", ""),
            "rubric": app._court_verdict.get("rubric", {}), "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        })
        if len(history) > 200:
            history = history[-200:]
        _save_court_history(history)
        app._court_state = "verdict"
        app._court_loading = False
        app.set_status("STANDBY")
    threading.Thread(target=_run, daemon=True).start()


def lesson_score_mini_argument(app, user_text):
    """Scores a 1-exchange lesson mini-argument: pass/fail on whether the
    user applied the lesson's concept correctly, plus one sentence of
    feedback. Deliberately not court_generate_verdict's 4-axis rubric — a
    single exchange doesn't carry enough signal for four separate 1-10
    scores, and a lesson needs a fast, unambiguous pass/fail to drive
    streaks/XP/review, not a nuanced verdict."""
    def _run():
        lesson = LESSONS[app._lesson_current]
        arg = lesson["mini_argument"]
        bot = BOT_CHARACTERS.get(arg["bot_id"], BOT_CHARACTERS[DEFAULT_BOT])
        standard = OBJECTION_STANDARDS.get(lesson.get("objection_ref", ""), "")
        prompt = (
            f"MOOT COURT LESSON — MINI-ARGUMENT CHECK\n"
            f"Concept being taught: {lesson['title']}\n"
            f"Fact pattern: {arg['fact_pattern']}\n"
            f"Opposing counsel ({bot['name']}) opened with: \"{arg['bot_opens']}\"\n"
            f"What a correct response should do: {arg['coach_goal']}\n"
            + (f"The real standard for this ground: {standard}\n" if standard else "") +
            f"\nStudent's response: \"{user_text}\"\n\n"
            f"Did the student correctly apply the concept? Judge the legal substance, not writing "
            f"quality or length. Return ONLY JSON, no markdown fences: "
            f"{{\"passed\": true or false, \"feedback\": \"one sentence, specific to what they said\"}}."
        )
        try:
            resp = _client.messages.create(model="claude-sonnet-4-5", max_tokens=200,
                                            messages=[{"role": "user", "content": prompt}])
            raw = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()
            raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
            result = _json.loads(raw)
            passed = bool(result.get("passed"))
            feedback = str(result.get("feedback", "")).strip()
        except Exception as e:
            passed, feedback = False, f"Couldn't score that: {e}"

        progress = _load_lesson_progress()
        _record_lesson_practice(progress)
        progress["lessons"][app._lesson_current] = {
            "status": "passed" if passed else "failed",
            "last_attempt": datetime.now().strftime("%Y-%m-%d"),
        }
        if passed:
            progress["xp"] = progress.get("xp", 0) + (
                LESSON_XP_REVIEW if app._lesson_from_review else LESSON_XP
            )
        _save_lesson_progress(progress)
        app._lesson_progress = progress  # keep the in-memory copy (header, map) in sync without a reload

        app._lesson_result = {"passed": passed, "feedback": feedback}
        app._lesson_state = "scored"
        app._lesson_loading = False
        app.set_status("STANDBY")
    threading.Thread(target=_run, daemon=True).start()


# ── App ───────────────────────────────────────────────────────────────────
class MootCourtApp:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Moot Court Trainer")
        self.root.geometry(f"{W}x{H}")
        self.root.configure(bg=BG)
        self.root.resizable(True, True)
        self._tkfont_cache = {}

        self._pulse = 0
        self._status_text = "STANDBY"
        self._bursts = []

        # ── Moot Court state (identical to the source feature) ──
        self._court_state = "setup"
        self._court_source = None
        self._court_case_type = None
        self._court_jarvis_role = None
        self._court_character = None
        self._court_case = None
        self._court_transcript = []
        self._court_verdict = None
        self._court_setup_hits = {}
        self._court_phase_hit = None
        self._court_loading = False
        self._court_scroll = 0
        self._court_hint = None
        self._court_hint_loading = False
        self._court_hint_hit = None
        self._court_show_amendments = False
        self._court_amendments_hit = None
        self._court_amendments_scroll = 0
        self._court_export_hit = None
        self._court_export_msg = None
        self._court_objections = []
        self._court_objection_loading = False
        self._court_objection_hit = None
        self._court_objection_menu_open = False
        self._court_objection_type_hits = {}
        self._court_jury_mode = None
        self._court_jury_hit = None
        self._court_cross_exam_active = False
        self._court_cross_exam_hit = None
        self._court_evidence_hit = None
        self._court_show_evidence = False
        self._court_show_log = False
        self._court_log_hit = None
        self._court_transition_frame = 0
        self._court_transition_burst_fired = False
        self._court_bubble_state = {"phase": None, "user": None, "jarvis": None, "judge": None, "witness": None}
        self._court_bubble_pop = {"user": 0, "jarvis": 0, "judge": 0, "witness": 0}
        self._court_witness_enter = 0

        # ── animation state added for the "real trial" pass ──
        # per-role speaking pace: {"ticks": frames left, "total": frames the
        # line takes, "syll": estimated syllable count} — drives the mouth/
        # gesture cycle in _draw_court_figure so it tracks line length
        # instead of pulsing forever.
        self._court_talk = {r: {"ticks": 0, "total": 1, "syll": 1} for r in ("user", "jarvis", "judge", "witness")}
        self._court_ruling_anim = 0          # frames left in the gavel-strike-then-freeze ruling beat
        self._court_ruling_burst_fired = False
        self._court_last_objection_count = 0  # detects a newly-appended ruling in self._court_objections
        self._court_jury_reaction = 0        # frames left of a jury lean-in/murmur reaction beat
        self._court_cam_push = 0.0           # 0..1 subtle "push in" toward a focal point on key beats
        self._court_cam_focus = (0.5, 0.12)  # normalized (x, y) within the scene rect to push toward
        self._court_judge_lean = 0.0         # 0..1, decays each frame; judge leans in on a sharp objection
        self._court_witness_unease = 0        # frames spent under active cross-exam; grows a slow posture shift
        self._court_transition_kind = "gavel"  # "gavel" (trial start / default) or "shuffle" (closing transition)
        self._court_verdict_enter = 0        # frames since the verdict screen first had data, for its reveal beat
        self._court_verdict_bot_won = None    # best-effort win/loss read of the verdict text, for the bot's verdict catchphrase

        cfg = _load_config()
        self._court_catchphrases_enabled = cfg.get("catchphrases_enabled", True)
        self._court_catchphrase_toggle_hit = None
        self._court_catchphrase = None        # currently-displayed banner text (UI flourish only, never in the transcript)
        self._court_catchphrase_frames = 0
        self._court_catchphrase_fired = set()  # trigger names already used this trial, so e.g. entrance only fires once

        self._court_experience = "experienced"
        self._court_experience_hits = {}
        self._court_word_hits = []
        self._court_term_popup = None
        self._court_term_loading = None
        self._court_click_mode = "word"
        self._court_mode_hits = {}

        # ── Lesson layer state (parallel to _court_* state, not merged into
        # it — see design note above lesson_score_mini_argument) ──
        self._app_mode = "trial"          # "trial" or "lessons" — top-level switch
        self._mode_toggle_hit = None
        self._lesson_state = "map"        # "map" -> "concept" -> "drills" -> "mini_argument" -> "scored"
        self._lesson_current = None       # lesson id from LESSONS
        self._lesson_from_review = False  # True if this attempt came from the missed-drills queue
        self._lesson_map_hits = {}
        self._lesson_map_scroll = 0
        self._lesson_phase_hit = None
        self._lesson_drill_index = 0
        self._lesson_drill_hits = {}
        self._lesson_drill_result = None  # {"correct": bool, "feedback": str} for the current drill, or None
        self._lesson_fill_blanks = {}     # for fill_in_blank drills: {blank_key: typed text} (kept simple, not graded strictly)
        self._lesson_loading = False
        self._lesson_result = None        # {"passed": bool, "feedback": str} after mini-argument scoring
        self._lesson_review_hit = None
        self._lesson_scored_burst_fired = False  # gates the pass-celebration burst to once per attempt
        self._lesson_progress = _load_lesson_progress()

        self._build_input()
        self._draw_loop()

    # ── window chrome / input bar ──
    def _build_input(self):
        inp = tk.Frame(self.root, bg="#030810")
        inp.place(x=10, rely=1.0, y=-55, relwidth=1, width=-20, height=44)
        tk.Label(inp, text=">_", font=(FONT_DATA, 12, "bold"), fg=CYAN, bg="#030810").pack(side="left", padx=(10, 6))
        self.ev = tk.StringVar()
        self.entry = tk.Entry(inp, textvariable=self.ev, bg="#040c16", fg=TEXT,
                               insertbackground=CYAN, font=(FONT_DISPLAY, 11), relief="flat",
                               highlightthickness=1, highlightcolor=CYAN, highlightbackground=CMID)
        self.entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))
        self.entry.bind("<Return>", self.send)
        tk.Button(inp, text="SEND", command=self.send, bg=CYAN, fg=BG,
                  font=(FONT_DISPLAY, 9, "bold"), relief="flat", padx=14, pady=2, cursor="hand2").pack(side="right", padx=(0, 4))

        self.canvas = tk.Canvas(self.root, bg=BG, highlightthickness=0, cursor="arrow")
        self.canvas.place(x=0, y=0, relwidth=1, relheight=1, height=-60)
        self.canvas.bind("<MouseWheel>", self._on_scroll)
        self.canvas.bind("<Button-1>", self._on_click)

    def set_status(self, t):
        self._status_text = t.upper()

    # ── input routing ──
    def send(self, event=None):
        text = self.ev.get().strip()
        if not text:
            return
        self.ev.set("")
        cw = self.canvas.winfo_width() or W
        ch = self.canvas.winfo_height() or H
        self._spawn_burst(cw // 2, ch - 33, n=10)
        if self._app_mode == "lessons":
            if self._lesson_state == "mini_argument" and not self._lesson_loading:
                self._lesson_loading = True
                lesson_score_mini_argument(self, text)
                return
            self.set_status("Nothing to respond to right now.")
            return
        if self._court_state in ("opening", "arguments", "closing") and not self._court_loading:
            if self._court_state == "arguments" and self._court_cross_exam_active:
                self._court_transcript.append({"phase": self._court_state, "speaker": "YOU (questioning witness)", "text": text})
                self._court_loading = True
                court_cross_exam_respond(self, text)
                return
            self._court_transcript.append({"phase": self._court_state, "speaker": "YOU", "text": text})
            self._court_loading = True
            court_phase_respond(self, text, self._court_state)
            return
        self.set_status("Nothing to respond to right now — advance the case first.")

    # ══════════════════════════════════════════════════════════════════════
    # Lesson layer — click handling + drawing. Parallel to the _court_*
    # equivalents above rather than merged into them (see the design note
    # above lesson_score_mini_argument for why).
    # ══════════════════════════════════════════════════════════════════════
    def _on_lesson_click(self, event):
        if self._lesson_state == "map":
            for lid, (x1, y1, x2, y2) in self._lesson_map_hits.items():
                if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                    self._enter_lesson(lid, from_review=False)
                    return
            review_hit = getattr(self, "_lesson_review_hit", None)
            missed = self._lesson_progress.get("missed_drills", [])
            if review_hit and missed and review_hit[0] <= event.x <= review_hit[2] and review_hit[1] <= event.y <= review_hit[3]:
                self._enter_lesson(missed[0]["lesson"], from_review=True)
                return
            return

        if self._lesson_state == "concept":
            hit = self._lesson_phase_hit
            if hit and hit[0] <= event.x <= hit[2] and hit[1] <= event.y <= hit[3]:
                self._lesson_state = "drills"
                self._lesson_drill_index = 0
                self._lesson_drill_result = None
                return
            return

        if self._lesson_state == "drills":
            lesson = LESSONS[self._lesson_current]
            drill = lesson["drills"][self._lesson_drill_index]
            if self._lesson_drill_result is not None:
                hit = self._lesson_phase_hit
                if hit and hit[0] <= event.x <= hit[2] and hit[1] <= event.y <= hit[3]:
                    self._advance_drill()
                return
            if drill["type"] == "multiple_choice":
                for opt, (x1, y1, x2, y2) in self._lesson_drill_hits.items():
                    if x1 <= event.x <= x2 and y1 <= event.y <= y2:
                        self._answer_drill(opt)
                        return
            elif drill["type"] == "fill_in_blank":
                # kept deliberately simple: any non-empty submission via the
                # phase button counts as "attempted" and passes, since the
                # point of this drill is citation *shape*, not verifying a
                # specific fabricated case name is well-formed.
                hit = self._lesson_phase_hit
                if hit and hit[0] <= event.x <= hit[2] and hit[1] <= event.y <= hit[3]:
                    self._answer_drill(drill["answer_shape"])
                return
            return

        if self._lesson_state == "mini_argument":
            return  # user responds via the text input bar, not a click

        if self._lesson_state == "scored":
            hit = self._lesson_phase_hit
            if hit and hit[0] <= event.x <= hit[2] and hit[1] <= event.y <= hit[3]:
                self._lesson_state = "map"
                self._lesson_current = None
                self._lesson_result = None
                self._lesson_progress = _load_lesson_progress()
                return
            return

    def _lesson_flat_order(self):
        """Every lesson across every skill, in curriculum order — the spine
        the winding path is drawn along, and the sequence lock-gating walks."""
        order = []
        for skill in SKILL_TREE:
            for lid in skill["lessons"]:
                order.append((skill, lid))
        return order

    def _is_lesson_locked(self, idx, lesson_ids, progress):
        """A lesson unlocks once the previous one is passed. Already-attempted
        lessons (even failed ones) stay unlocked so retrying doesn't re-lock
        you out of your own in-progress lesson."""
        if idx == 0:
            return False
        prev_status = progress["lessons"].get(lesson_ids[idx - 1], {}).get("status")
        if prev_status == "passed":
            return False
        return progress["lessons"].get(lesson_ids[idx], {}).get("status") is None

    def _enter_lesson(self, lesson_id, from_review):
        self._lesson_current = lesson_id
        self._lesson_from_review = from_review
        self._lesson_state = "concept"
        self._lesson_drill_index = 0
        self._lesson_drill_result = None
        self._lesson_result = None
        self._lesson_scored_burst_fired = False

    def _answer_drill(self, chosen):
        lesson = LESSONS[self._lesson_current]
        drill = lesson["drills"][self._lesson_drill_index]
        correct = (chosen == drill.get("answer") or drill["type"] == "fill_in_blank")
        feedback = drill.get("feedback_correct" if correct else "feedback_wrong", "")
        self._lesson_drill_result = {"correct": correct, "feedback": feedback}
        progress = self._lesson_progress
        if correct:
            _clear_missed_drill(progress, self._lesson_current, drill["id"])
        else:
            _queue_missed_drill(progress, self._lesson_current, drill["id"])
        _save_lesson_progress(progress)

    def _advance_drill(self):
        lesson = LESSONS[self._lesson_current]
        self._lesson_drill_index += 1
        self._lesson_drill_result = None
        if self._lesson_drill_index >= len(lesson["drills"]):
            self._lesson_state = "mini_argument"

    def _draw_lessons(self, c, ox, y1, cw, ch):
        if self._lesson_state == "map":
            self._draw_lesson_map(c, ox, y1, cw, ch)
        elif self._lesson_state == "concept":
            self._draw_lesson_concept(c, ox, y1, cw, ch)
        elif self._lesson_state == "drills":
            self._draw_lesson_drill(c, ox, y1, cw, ch)
        elif self._lesson_state == "mini_argument":
            self._draw_lesson_mini_argument(c, ox, y1, cw, ch)
        elif self._lesson_state == "scored":
            self._draw_lesson_scored(c, ox, y1, cw, ch)

    def _draw_lesson_map(self, c, ox, y1, cw, ch):
        cx = ox + (cw - ox) // 2
        progress = self._lesson_progress
        c.create_text(cx, y1 + 18, text="Practice", font=(FONT_DISPLAY, 11, "bold"), fill=TEXT, tags="dynamic")
        c.create_text(cx, y1 + 37, text=f"🔥 {progress.get('streak', 0)} day streak    ⭐ {progress.get('xp', 0)} XP",
                      font=(FONT_DISPLAY, 8), fill=GOLD, tags="dynamic")

        missed = progress.get("missed_drills", [])
        review_h = 44 if missed else 0
        header_h = 56
        content_y1 = y1 + header_h
        content_y2 = ch - 14 - review_h
        visible_h = max(1, content_y2 - content_y1)

        flat = self._lesson_flat_order()
        lesson_ids = [lid for _, lid in flat]

        # lay out node positions along a winding path, in local (unscrolled)
        # coordinates: nodes snake left/right of center via a sine offset,
        # with a gap inserted whenever the skill changes to make room for
        # that skill's banner.
        radius = 26
        node_gap = 96
        amp = min(90, (cw - ox) / 2 - radius - 30)
        positions = []
        y = 34
        last_skill_id = None
        for i, (skill, lid) in enumerate(flat):
            if skill["id"] != last_skill_id:
                y += 52
                last_skill_id = skill["id"]
            x_off = amp * math.sin(i * 0.9)
            positions.append({"skill": skill, "lid": lid, "idx": i, "x_off": x_off, "y": y})
            y += node_gap
        total_h = y + 30

        max_scroll = max(0, total_h - visible_h)
        self._lesson_map_scroll = max(0, min(self._lesson_map_scroll, max_scroll))
        scroll = self._lesson_map_scroll
        clip_top, clip_bottom = content_y1, content_y2

        def screen_xy(pos):
            return cx + pos["x_off"], content_y1 + (pos["y"] - scroll)

        # path line, drawn first so nodes sit on top of it
        for a, b in zip(positions, positions[1:]):
            ax, ay = screen_xy(a)
            bx, by = screen_xy(b)
            if max(ay, by) < clip_top - 40 or min(ay, by) > clip_bottom + 40:
                continue
            c.create_line(ax, ay, bx, by, fill=CORE_DIM, width=5, capstyle="round", tags="dynamic")

        self._lesson_map_hits = {}
        last_skill_drawn = None
        for pos in positions:
            skill, lid, idx = pos["skill"], pos["lid"], pos["idx"]
            sx, sy = screen_xy(pos)

            if skill["id"] != last_skill_drawn:
                header_y = sy - 62
                if clip_top - 30 <= header_y <= clip_bottom + 30:
                    bw = min(300, cw - 60)
                    bx1 = cx - bw / 2
                    pts = self._rounded_rect_points(bx1, header_y - 15, bx1 + bw, header_y + 15, 10)
                    c.create_polygon(pts, smooth=True, outline=CYAN, fill="#0a1622", tags="dynamic")
                    c.create_text(cx, header_y - 4, text=skill["title"].upper(),
                                  font=(FONT_DISPLAY, 8, "bold"), fill=CYAN, tags="dynamic")
                    c.create_text(cx, header_y + 9, text=skill["blurb"],
                                  font=(FONT_DISPLAY, 6), fill=TDIM, tags="dynamic")
                last_skill_drawn = skill["id"]

            if sy < clip_top - radius - 30 or sy > clip_bottom + radius + 30:
                continue

            lesson = LESSONS[lid]
            status = progress["lessons"].get(lid, {}).get("status")
            locked = self._is_lesson_locked(idx, lesson_ids, progress)
            if locked:
                outline, fill, icon, label_col = CORE_DIM, "#0a1420", "🔒", TDIM
            elif status == "passed":
                outline, fill, icon, label_col = GREEN, "#0f2b1e", "✓", GREEN
            elif status == "failed":
                outline, fill, icon, label_col = ORANGE, "#2a1c0a", "↻", ORANGE
            else:
                outline, fill, icon, label_col = CORE, "#0f2b42", "▶", HOT

            c.create_oval(sx - radius, sy - radius, sx + radius, sy + radius,
                          outline=outline, fill=fill, width=3, tags="dynamic")
            c.create_text(sx, sy, text=icon, font=(FONT_DISPLAY, 15, "bold"), fill=outline, tags="dynamic")
            label_y = sy + radius + 13
            c.create_text(sx, label_y, text=lesson["title"], font=(FONT_DISPLAY, 7, "bold"),
                          fill=label_col, tags="dynamic")

            if not locked:
                self._lesson_map_hits[lid] = (sx - radius, sy - radius, sx + radius, sy + radius)

        if max_scroll > 0:
            bar_h = max(20, visible_h * (visible_h / total_h))
            bar_y = content_y1 + (visible_h - bar_h) * (scroll / max_scroll)
            c.create_rectangle(cw - 14, bar_y, cw - 10, bar_y + bar_h, fill=CMID, outline="", tags="dynamic")

        if missed:
            bw, bh = 220, 32
            bx1 = cx - bw // 2
            by1 = content_y2 + 6
            self._lesson_review_hit = (bx1, by1, bx1 + bw, by1 + bh)
            pts = self._rounded_rect_points(bx1, by1, bx1 + bw, by1 + bh, 8)
            c.create_polygon(pts, smooth=True, outline=ORANGE, fill="#2a1c0a", tags="dynamic")
            c.create_text(bx1 + bw // 2, by1 + bh // 2, text=f"↻ REVIEW ({len(missed)} missed)",
                          font=(FONT_DISPLAY, 8, "bold"), fill=ORANGE, tags="dynamic")
        else:
            self._lesson_review_hit = None

    def _draw_lesson_concept(self, c, ox, y1, cw, ch):
        cx = ox + (cw - ox) // 2
        lesson = LESSONS[self._lesson_current]
        c.create_text(cx, y1 + 24, text=lesson["title"], font=(FONT_DISPLAY, 12, "bold"), fill=TEXT, tags="dynamic")
        self._draw_wrapped_text(c, ox + 40, y1 + 60, cw - 40, lesson["concept"], font=(FONT_DISPLAY, 9), fill=TEXT)
        self._draw_phase_button(c, cx, ch - 46, "START DRILLS")

    def _draw_lesson_drill(self, c, ox, y1, cw, ch):
        cx = ox + (cw - ox) // 2
        lesson = LESSONS[self._lesson_current]
        drill = lesson["drills"][self._lesson_drill_index]
        c.create_text(cx, y1 + 20, text=f"Drill {self._lesson_drill_index + 1}/{len(lesson['drills'])}",
                      font=(FONT_DISPLAY, 8), fill=TDIM, tags="dynamic")
        self._draw_wrapped_text(c, ox + 40, y1 + 45, cw - 40, drill["prompt"], font=(FONT_DISPLAY, 9), fill=TEXT)

        self._lesson_drill_hits = {}
        if drill["type"] == "multiple_choice":
            y = y1 + 45 + 70
            for opt in drill["options"]:
                bw = len(opt) * 7 + 30
                self._lesson_drill_hits[opt] = (ox + 40, y, ox + 40 + bw, y + 30)
                active = self._lesson_drill_result is not None and opt == drill["answer"]
                pts = self._rounded_rect_points(ox + 40, y, ox + 40 + bw, y + 30, 8)
                outline = GREEN if active else CORE_DIM
                c.create_polygon(pts, smooth=True, outline=outline, fill="#0a1622", tags="dynamic")
                c.create_text(ox + 40 + bw / 2, y + 15, text=opt, font=(FONT_DISPLAY, 8, "bold"), fill=TEXT, tags="dynamic")
                y += 38
        elif drill["type"] == "fill_in_blank":
            c.create_text(ox + 40, y1 + 45 + 70, text=drill["template"], font=(FONT_DATA, 10, "bold"),
                          fill=GOLD, anchor="w", tags="dynamic")
            c.create_text(cx, y1 + 45 + 100, text="(this drill checks shape, not a specific answer — hit continue when ready)",
                          font=(FONT_DISPLAY, 7), fill=TDIM, tags="dynamic")

        if self._lesson_drill_result is not None:
            col = GREEN if self._lesson_drill_result["correct"] else RED
            self._draw_wrapped_text(c, ox + 40, ch - 100, cw - 40, self._lesson_drill_result["feedback"],
                                    font=(FONT_DISPLAY, 8), fill=col)
            self._draw_phase_button(c, cx, ch - 46, "CONTINUE")
        else:
            self._lesson_phase_hit = None

    def _draw_lesson_mini_argument(self, c, ox, y1, cw, ch):
        cx = ox + (cw - ox) // 2
        lesson = LESSONS[self._lesson_current]
        arg = lesson["mini_argument"]
        bot = BOT_CHARACTERS.get(arg["bot_id"], BOT_CHARACTERS[DEFAULT_BOT])
        c.create_text(cx, y1 + 20, text="Mini-argument", font=(FONT_DISPLAY, 9, "bold"), fill=TDIM, tags="dynamic")
        self._draw_wrapped_text(c, ox + 40, y1 + 45, cw - 40, arg["fact_pattern"], font=(FONT_DISPLAY, 8), fill=TDIM)
        c.create_text(ox + 40, y1 + 100, text=f"{bot['label']} ›", font=(FONT_DISPLAY, 8, "bold"), fill=bot["accent"], anchor="w", tags="dynamic")
        self._draw_wrapped_text(c, ox + 100, y1 + 100, cw - 40, arg["bot_opens"], font=(FONT_DISPLAY, 9), fill=TEXT)
        c.create_text(cx, ch - 120, text="Respond in the box below.", font=(FONT_DISPLAY, 7), fill=TDIM, tags="dynamic")
        if self._lesson_loading:
            c.create_text(cx, ch - 100, text="Scoring your response…", font=(FONT_DISPLAY, 8), fill=ORANGE, tags="dynamic")
        self._lesson_phase_hit = None

    def _draw_lesson_scored(self, c, ox, y1, cw, ch):
        cx = ox + (cw - ox) // 2
        result = self._lesson_result or {"passed": False, "feedback": ""}
        headline = "✓ Concept applied" if result["passed"] else "Not quite yet"
        col = GREEN if result["passed"] else RED
        if result["passed"] and not self._lesson_scored_burst_fired:
            self._spawn_burst(cx, y1 + 30, colors=[GOLD, GREEN, CYAN])
            self._lesson_scored_burst_fired = True
        c.create_text(cx, y1 + 30, text=headline, font=(FONT_DISPLAY, 13, "bold"), fill=col, tags="dynamic")
        self._draw_wrapped_text(c, ox + 40, y1 + 70, cw - 40, result["feedback"], font=(FONT_DISPLAY, 9), fill=TEXT)
        if result["passed"]:
            xp = LESSON_XP_REVIEW if self._lesson_from_review else LESSON_XP
            c.create_text(cx, y1 + 110, text=f"+{xp} XP", font=(FONT_DISPLAY, 10, "bold"), fill=GOLD, tags="dynamic")
        self._draw_phase_button(c, cx, ch - 46, "BACK TO MAP")

    def _draw_phase_button(self, c, cx, btn_y, label, bw=180, bh=36):
        bx1 = cx - bw // 2
        self._lesson_phase_hit = (bx1, btn_y, bx1 + bw, btn_y + bh)
        pts = self._rounded_rect_points(bx1, btn_y, bx1 + bw, btn_y + bh, 8)
        c.create_polygon(pts, smooth=True, outline=CORE, fill="#0f2b42", tags="dynamic")
        c.create_text(bx1 + bw // 2, btn_y + bh // 2, text=label, font=(FONT_DISPLAY, 9, "bold"), fill=HOT, tags="dynamic")

    def _fire_catchphrase(self, trigger, once=True):
        """Fires a pre-written, deterministic flavor line for the current
        bot — never generated by the API and never added to the transcript.
        No-op if the toggle is off or the bot has no line for this trigger.
        `once=True` triggers (entrance/closing/verdict) fire at most once
        per trial; objection triggers are allowed to repeat since a trial
        can have several rulings."""
        if not self._court_catchphrases_enabled:
            return
        if once and trigger in self._court_catchphrase_fired:
            return
        bot = BOT_CHARACTERS.get(self._court_character, BOT_CHARACTERS[DEFAULT_BOT])
        line = (bot.get("catchphrases") or {}).get(trigger)
        if not line:
            return
        if once:
            self._court_catchphrase_fired.add(trigger)
        self._court_catchphrase = line
        self._court_catchphrase_frames = CATCHPHRASE_FRAMES

    def _draw_catchphrase_banner(self, c, cx, bottom_y, text, accent):
        """Small pill banner above a figure's head — pops in, holds, fades
        out — distinct from _draw_court_bubble so it never gets confused
        with an actual (API-generated) argument line."""
        total = CATCHPHRASE_FRAMES
        t = self._court_catchphrase_frames / total
        if t > 0.8:
            prog = 1.0 - (t - 0.8) / 0.2
        elif t < 0.2:
            prog = t / 0.2
        else:
            prog = 1.0
        prog = max(0.0, min(1.0, prog))
        eb = self._ease_out_back(prog)
        rise = (1.0 - eb) * 12
        w = min(220, len(text) * 5.1 + 26)
        h = 22
        bx1, by1, bx2, by2 = cx - w / 2, bottom_y - h - rise, cx + w / 2, bottom_y - rise
        pts = self._rounded_rect_points(bx1, by1, bx2, by2, 10)
        c.create_polygon(pts, smooth=True, outline=accent, fill=_blend_hex(accent, ABYSS, 0.72), tags="dynamic")
        c.create_text((bx1 + bx2) / 2, (by1 + by2) / 2, text=text, font=(FONT_DISPLAY, 7, "italic"),
                      fill=TEXT, width=w - 10, tags="dynamic")

    def _spawn_burst(self, x, y, colors=None, n=16):
        colors = colors or [CORE, HOT, ACCENT2]
        for _ in range(n):
            a = random.uniform(0, 2 * math.pi)
            speed = random.uniform(1.5, 4.5)
            life = random.randint(14, 24)
            self._bursts.append([x, y, speed * math.cos(a), speed * math.sin(a), life, life, random.choice(colors)])

    def _on_scroll(self, event):
        if self._court_show_amendments:
            self._court_amendments_scroll = max(0, self._court_amendments_scroll - int(event.delta / 40))
            return
        if self._app_mode == "lessons":
            if self._lesson_state == "map":
                self._lesson_map_scroll = max(0, self._lesson_map_scroll - int(event.delta / 40))
            return
        if self._court_state in ("setup", "briefing", "opening", "arguments", "closing", "verdict"):
            self._court_scroll = max(0, self._court_scroll - int(event.delta / 40))

    def _on_click(self, event):
        toggle_hit = getattr(self, "_mode_toggle_hit", None)
        if toggle_hit and toggle_hit[0] <= event.x <= toggle_hit[2] and toggle_hit[1] <= event.y <= toggle_hit[3]:
            midpoint = (toggle_hit[0] + toggle_hit[2]) / 2
            self._app_mode = "trial" if event.x < midpoint else "lessons"
            return
        if self._app_mode == "lessons":
            self._on_lesson_click(event)
            return
        hit = getattr(self, "_court_amendments_hit", None)
        if hit and hit[0] <= event.x <= hit[2] and hit[1] <= event.y <= hit[3]:
            self._court_show_amendments = not self._court_show_amendments
            self._court_amendments_scroll = 0
            return
        if self._court_show_amendments:
            return
        if self._court_state == "setup":
            toggle_hit = getattr(self, "_court_catchphrase_toggle_hit", None)
            if toggle_hit and toggle_hit[0] <= event.x <= toggle_hit[2] and toggle_hit[1] <= event.y <= toggle_hit[3]:
                self._court_catchphrases_enabled = not self._court_catchphrases_enabled
                cfg = _load_config()
                cfg["catchphrases_enabled"] = self._court_catchphrases_enabled
                _save_config(cfg)
                return
            for (key, val), (tx1, ty1, tx2, ty2) in getattr(self, "_court_setup_hits", {}).items():
                if tx1 <= event.x <= tx2 and ty1 <= event.y <= ty2:
                    if key == "source": self._court_source = val
                    elif key == "case_type": self._court_case_type = val
                    elif key == "jarvis_role": self._court_jarvis_role = val
                    elif key == "character": self._court_character = val
                    elif key == "jury": self._court_jury_mode = (val == "yes")
                    return
            hit = getattr(self, "_court_phase_hit", None)
            if hit and hit[0] <= event.x <= hit[2] and hit[1] <= event.y <= hit[3] and not self._court_loading:
                self._court_loading = True
                self._court_scroll = 0
                generate_court_case(self, self._court_source, self._court_case_type)
                return
        elif self._court_state == "briefing":
            if self._court_term_popup:
                close_hit = getattr(self, "_court_term_popup_close_hit", None)
                if close_hit and close_hit[0] <= event.x <= close_hit[2] and close_hit[1] <= event.y <= close_hit[3]:
                    self._court_term_popup = None
                    return
                self._court_term_popup = None
                return
            for val, (tx1, ty1, tx2, ty2) in getattr(self, "_court_experience_hits", {}).items():
                if tx1 <= event.x <= tx2 and ty1 <= event.y <= ty2:
                    self._court_experience = val
                    return
            for val, (tx1, ty1, tx2, ty2) in getattr(self, "_court_mode_hits", {}).items():
                if tx1 <= event.x <= tx2 and ty1 <= event.y <= ty2:
                    self._court_click_mode = val
                    return
            for (wx1, wy1, wx2, wy2, word, sentence) in getattr(self, "_court_word_hits", []):
                if wx1 <= event.x <= wx2 and wy1 <= event.y <= wy2:
                    if self._court_click_mode == "sentence":
                        self._court_term_popup = {"word": sentence[:40] + ("…" if len(sentence) > 40 else ""),
                                                   "explanation": None, "x": event.x, "y": event.y}
                        self._court_term_loading = sentence[:40]
                        court_analyze_sentence(self, sentence, self._court_case.get("facts", ""))
                    else:
                        self._court_term_popup = {"word": word, "explanation": None, "x": event.x, "y": event.y}
                        self._court_term_loading = word
                        court_explain_term(self, word, self._court_case.get("facts", ""))
                    return
            hit = getattr(self, "_court_phase_hit", None)
            if hit and hit[0] <= event.x <= hit[2] and hit[1] <= event.y <= hit[3]:
                self._court_state = "opening"
                self._court_scroll = 0
                self._court_transition_kind = "gavel"
                self._court_transition_frame = 26
                self._court_transition_burst_fired = False
                self._fire_catchphrase("entrance")
                return
        elif self._court_state in ("opening", "arguments", "closing"):
            if self._court_objection_menu_open:
                for otype, (tx1, ty1, tx2, ty2) in getattr(self, "_court_objection_type_hits", {}).items():
                    if tx1 <= event.x <= tx2 and ty1 <= event.y <= ty2:
                        self._court_objection_menu_open = False
                        self._court_objection_loading = True
                        self._court_hint = None
                        self._court_cam_push = 1.0
                        court_rule_objection(self, otype, "YOU")
                        return
                self._court_objection_menu_open = False
                return
            obj_hit = getattr(self, "_court_objection_hit", None)
            if (self._court_state == "arguments" and obj_hit
                    and obj_hit[0] <= event.x <= obj_hit[2] and obj_hit[1] <= event.y <= obj_hit[3]
                    and not self._court_objection_loading):
                self._court_objection_menu_open = True
                return
            cross_hit = getattr(self, "_court_cross_exam_hit", None)
            if (self._court_state == "arguments" and cross_hit
                    and cross_hit[0] <= event.x <= cross_hit[2] and cross_hit[1] <= event.y <= cross_hit[3]):
                self._court_cross_exam_active = not self._court_cross_exam_active
                return
            evidence_hit = getattr(self, "_court_evidence_hit", None)
            if (self._court_state == "arguments" and evidence_hit
                    and evidence_hit[0] <= event.x <= evidence_hit[2] and evidence_hit[1] <= event.y <= evidence_hit[3]):
                self._court_show_evidence = not self._court_show_evidence
                self._court_show_log = False
                return
            if self._court_show_evidence:
                self._court_show_evidence = False
                return
            log_hit = getattr(self, "_court_log_hit", None)
            if log_hit and log_hit[0] <= event.x <= log_hit[2] and log_hit[1] <= event.y <= log_hit[3]:
                self._court_show_log = not self._court_show_log
                self._court_show_evidence = False
                return
            if self._court_show_log:
                self._court_show_log = False
                return
            hint_hit = getattr(self, "_court_hint_hit", None)
            if hint_hit and hint_hit[0] <= event.x <= hint_hit[2] and hint_hit[1] <= event.y <= hint_hit[3] and not self._court_hint_loading:
                self._court_hint_loading = True
                self._court_hint = None
                court_generate_hint(self, self._court_state)
                return
            hit = getattr(self, "_court_phase_hit", None)
            if hit and hit[0] <= event.x <= hit[2] and hit[1] <= event.y <= hit[3] and not self._court_loading:
                if self._court_state == "opening":
                    self._court_state = "arguments"
                elif self._court_state == "arguments":
                    self._court_state = "closing"
                    self._court_transition_kind = "shuffle"
                    self._court_transition_frame = 26
                    self._court_transition_burst_fired = False
                    self._fire_catchphrase("closing_open")
                else:
                    self._court_loading = True
                    court_generate_verdict(self)
                self._court_scroll = 0
                self._court_hint = None
                return
        elif self._court_state == "verdict":
            export_hit = getattr(self, "_court_export_hit", None)
            if export_hit and export_hit[0] <= event.x <= export_hit[2] and export_hit[1] <= event.y <= export_hit[3]:
                bot = BOT_CHARACTERS.get(self._court_character, BOT_CHARACTERS[DEFAULT_BOT])
                path = court_export_transcript(self._court_case, self._court_transcript,
                                                self._court_verdict, bot["name"])
                self._court_export_msg = "✓ SAVED" if path else "✗ FAILED"
                return
            hit = getattr(self, "_court_phase_hit", None)
            if hit and hit[0] <= event.x <= hit[2] and hit[1] <= event.y <= hit[3]:
                self._court_state = "setup"
                self._court_case = None
                self._court_transcript = []
                self._court_verdict = None
                self._court_source = None
                self._court_case_type = None
                self._court_jarvis_role = None
                self._court_character = None
                self._court_scroll = 0
                self._court_export_msg = None
                self._court_jury_mode = None
                self._court_objections = []
                self._court_cross_exam_active = False
                self._court_show_evidence = False
                self._court_show_log = False
                self._court_transition_frame = 0
                self._court_bubble_state = {"phase": None, "user": None, "jarvis": None, "judge": None, "witness": None}
                self._court_bubble_pop = {"user": 0, "jarvis": 0, "judge": 0, "witness": 0}
                self._court_witness_enter = 0
                self._court_talk = {r: {"ticks": 0, "total": 1, "syll": 1} for r in ("user", "jarvis", "judge", "witness")}
                self._court_ruling_anim = 0
                self._court_ruling_burst_fired = False
                self._court_last_objection_count = 0
                self._court_jury_reaction = 0
                self._court_cam_push = 0.0
                self._court_judge_lean = 0.0
                self._court_witness_unease = 0
                self._court_transition_kind = "gavel"
                self._court_verdict_enter = 0
                self._court_verdict_bot_won = None
                self._court_catchphrase = None
                self._court_catchphrase_frames = 0
                self._court_catchphrase_fired = set()
                return

    # ── draw loop ──
    def _draw_loop(self):
        c = self.canvas
        c.delete("dynamic")
        cw = c.winfo_width() or W
        ch = c.winfo_height() or H

        grid_step = 46
        for x in range(0, cw, grid_step):
            c.create_line(x, 0, x, ch, fill="#0a1626", width=1, tags="dynamic")
        for y in range(0, ch, grid_step):
            c.create_line(0, y, cw, y, fill="#0a1626", width=1, tags="dynamic")

        alive = []
        for px, py, vx, vy, life, maxlife, col in self._bursts:
            px += vx; py += vy; vx *= 0.94; vy *= 0.94; life -= 1
            if life > 0:
                t = life / maxlife
                fcol = _blend_hex(ABYSS, col, t)
                rr = 1 + 2 * t
                c.create_oval(px - rr, py - rr, px + rr, py + rr, fill=fcol, outline="", tags="dynamic")
                alive.append([px, py, vx, vy, life, maxlife, col])
        self._bursts = alive

        c.create_text(16, 16, text="⚖ MOOT COURT TRAINER", font=(FONT_DISPLAY, 11, "bold"), fill=CYAN, anchor="w", tags="dynamic")
        c.create_text(cw - 16, 16, text=self._status_text, font=(FONT_DISPLAY, 8), fill=TDIM, anchor="e", tags="dynamic")

        # top-level TRIAL / LESSONS switch — sits left of the status text
        tw, th = 150, 20
        tx2, ty1 = cw - 16 - 130, 6
        tx1 = tx2 - tw
        self._mode_toggle_hit = (tx1, ty1, tx2, ty1 + th)
        pts = self._rounded_rect_points(tx1, ty1, tx2, ty1 + th, 6)
        c.create_polygon(pts, smooth=True, outline=CORE_DIM, fill="#0a1622", tags="dynamic")
        half = (tx1 + tx2) / 2
        c.create_text((tx1 + half) / 2, ty1 + th / 2, text="TRIAL",
                      font=(FONT_DISPLAY, 7, "bold" if self._app_mode == "trial" else "normal"),
                      fill=HOT if self._app_mode == "trial" else TDIM, tags="dynamic")
        c.create_text((half + tx2) / 2, ty1 + th / 2, text="LESSONS",
                      font=(FONT_DISPLAY, 7, "bold" if self._app_mode == "lessons" else "normal"),
                      fill=HOT if self._app_mode == "lessons" else TDIM, tags="dynamic")
        c.create_line(half, ty1 + 3, half, ty1 + th - 3, fill=CORE_DIM, tags="dynamic")

        # persistent streak/XP pill — same numbers as the lesson map header,
        # but visible everywhere so trial mode doesn't hide your progress
        lp = self._lesson_progress
        pill_txt = f"🔥{lp.get('streak', 0)}  ⭐{lp.get('xp', 0)}"
        pw = 16 + len(pill_txt) * 6
        px2 = tx1 - 12
        px1 = px2 - pw
        pts = self._rounded_rect_points(px1, ty1, px2, ty1 + th, 6)
        c.create_polygon(pts, smooth=True, outline=CORE_DIM, fill="#0a1622", tags="dynamic")
        c.create_text((px1 + px2) / 2, ty1 + th / 2, text=pill_txt, font=(FONT_DISPLAY, 8, "bold"), fill=GOLD, tags="dynamic")

        self._draw_hline(c, 10, cw - 10, 30)

        if self._app_mode == "lessons":
            self._draw_lessons(c, 0, 40, cw, ch)
        else:
            self._draw_moot_court(c, 0, 40, cw, ch)

        self._pulse += 1
        self.root.after(28, self._draw_loop)

    def run(self):
        self.root.mainloop()

    # ══════════════════════════════════════════════════════════════════════
    # Everything below is the Moot Court feature itself (drawing code),
    # copied over unchanged from the source app.
    # ══════════════════════════════════════════════════════════════════════

    def _draw_moot_court(self, c, ox, y1, cw, ch):
        if self._court_state == "setup":
            self._draw_court_setup(c, ox, y1, cw, ch)
        elif self._court_state == "briefing":
            self._draw_court_briefing(c, ox, y1, cw, ch)
        elif self._court_state == "verdict":
            self._draw_court_verdict(c, ox, y1, cw, ch)
        else:
            self._draw_court_proceeding(c, ox, y1, cw, ch)

        bw, bh = 110, 22
        bx1, by1 = cw - 20 - bw, y1 + 2
        self._court_amendments_hit = (bx1, by1, bx1 + bw, by1 + bh)
        pts = self._rounded_rect_points(bx1, by1, bx1 + bw, by1 + bh, 6)
        c.create_polygon(pts, smooth=True, outline=CORE_DIM, fill="#0a1622", tags="dynamic")
        c.create_text(bx1 + bw // 2, by1 + bh // 2, text="📜 AMENDMENTS" if not self._court_show_amendments else "✕ CLOSE",
                      font=(FONT_DISPLAY, 7, "bold"), fill=TDIM if not self._court_show_amendments else RED, tags="dynamic")

        if self._court_show_amendments:
            self._draw_amendments_overlay(c, ox, y1, cw, ch)

    def _draw_amendments_overlay(self, c, ox, y1, cw, ch):
        ox1, oy1, ox2, oy2 = ox + 30, y1 + 30, cw - 30, ch - 15
        c.create_rectangle(ox1, oy1, ox2, oy2, fill=ABYSS, outline=CORE, width=1, tags="dynamic")
        c.create_text(ox1 + 16, oy1 + 16, text="U.S. CONSTITUTIONAL AMENDMENTS", font=(FONT_DISPLAY, 10, "bold"), fill=CYAN, anchor="w", tags="dynamic")
        self._draw_hline(c, ox1 + 16, ox2 - 16, oy1 + 30)

        row_h = 34
        total_h = len(US_AMENDMENTS) * row_h
        visible_h = oy2 - (oy1 + 40) - 10
        max_scroll = max(0, total_h - visible_h)
        self._court_amendments_scroll = max(0, min(self._court_amendments_scroll, max_scroll))

        y = oy1 + 44 - (max_scroll - self._court_amendments_scroll if max_scroll else 0)
        clip_top, clip_bottom = oy1 + 34, oy2 - 6
        for num, desc in US_AMENDMENTS:
            if clip_top <= y <= clip_bottom:
                if 11 <= num % 100 <= 13:
                    ordinal = f"{num}th"
                else:
                    ordinal = f"{num}{ {1:'st',2:'nd',3:'rd'}.get(num % 10, 'th') }"
                c.create_text(ox1 + 16, y, text=ordinal,
                              font=(FONT_DATA, 8, "bold"), fill=GOLD, anchor="w", tags="dynamic")
                self._draw_wrapped_text(c, ox1 + 60, y - 5, ox2 - 16, desc, font=(FONT_DISPLAY, 7), fill=TEXT)
            y += row_h
        if max_scroll > 0:
            c.create_text(ox2 - 16, oy1 + 40, text="scroll for more ▾", font=(FONT_DISPLAY, 6), fill=TDIM, anchor="e", tags="dynamic")

    def _draw_court_setup(self, c, ox, y1, cw, ch):
        cx = ox + (cw - ox) // 2
        c.create_text(cx, y1 + 20, text="New Moot Court Session", font=(FONT_DISPLAY, 11, "bold"), fill=TEXT, tags="dynamic")

        # global catchphrases toggle — a UI flavor setting, not a game
        # option, so it lives off to the side rather than in the rows below.
        tw, th = 118, 24
        tx2, ty1 = cw - 20, y1 - 2
        tx1 = tx2 - tw
        self._court_catchphrase_toggle_hit = (tx1, ty1, tx2, ty1 + th)
        on = self._court_catchphrases_enabled
        pts = self._rounded_rect_points(tx1, ty1, tx2, ty1 + th, 8)
        c.create_polygon(pts, smooth=True, outline=CORE if on else CORE_DIM,
                          fill="#123552" if on else "#0a1622", tags="dynamic")
        c.create_text((tx1 + tx2) / 2, ty1 + th / 2, text=f"Catchphrases: {'ON' if on else 'OFF'}",
                      font=(FONT_DISPLAY, 7, "bold" if on else "normal"), fill=HOT if on else TDIM, tags="dynamic")

        self._court_setup_hits = {}
        rows = [
            ("source", "Case source", [("fictional", "Fictional hypo"), ("real", "Real landmark case")]),
            ("case_type", "Practice area", [("civil", "Civil"), ("criminal", "Criminal"), ("corporate", "Corporate"),
                ("constitutional", "Constitutional"), ("contract", "Contract"), ("tort", "Tort"),
                ("ip", "IP / Patent"), ("random", "Random")]),
            ("jarvis_role", "Opponent plays", [("opposing", "Opposing counsel"), ("judge", "Judge")]),
            ("jury", "Verdict by", [("no", "Judge alone"), ("yes", "Jury")]),
        ]
        y = y1 + 55
        current = {"source": self._court_source, "case_type": self._court_case_type, "jarvis_role": self._court_jarvis_role,
                   "jury": None if self._court_jury_mode is None else ("yes" if self._court_jury_mode else "no"),
                   "character": self._court_character}
        max_x = cw - 30
        for key, label, options in rows:
            c.create_text(ox + 30, y, text=label, font=(FONT_DISPLAY, 8), fill=TDIM, anchor="w", tags="dynamic")
            bx = ox + 30
            row_start_y = y + 12
            for val, opt_label in options:
                active = current[key] == val
                bw = len(opt_label) * 6.5 + 24
                if bx + bw > max_x:
                    bx = ox + 30
                    row_start_y += 32
                self._court_setup_hits[(key, val)] = (bx, row_start_y, bx + bw, row_start_y + 26)
                pts = self._rounded_rect_points(bx, row_start_y, bx + bw, row_start_y + 26, 8)
                c.create_polygon(pts, smooth=True, outline=CORE if active else CORE_DIM,
                                 fill="#123552" if active else "#0a1622", tags="dynamic")
                c.create_text(bx + bw / 2, row_start_y + 13, text=opt_label, font=(FONT_DISPLAY, 7, "bold" if active else "normal"),
                              fill=HOT if active else TEXT, tags="dynamic")
                bx += bw + 10
            y = row_start_y + 46

        c.create_text(ox + 30, y, text="Choose your opponent", font=(FONT_DISPLAY, 8), fill=TDIM, anchor="w", tags="dynamic")
        panel_y1 = y + 14
        panel_y2 = max(panel_y1 + 90, ch - 66)
        self._draw_bot_roster(c, ox + 30, panel_y1, max_x, panel_y2)

        btn_y = ch - 46
        ready = all(current.values())
        if ready:
            bw, bh = 180, 36
            bx1 = cx - bw // 2
            self._court_phase_hit = (bx1, btn_y, bx1 + bw, btn_y + bh)
            pts = self._rounded_rect_points(bx1, btn_y, bx1 + bw, btn_y + bh, 8)
            c.create_polygon(pts, smooth=True, outline=RED, fill="#2a0f14", tags="dynamic")
            c.create_text(bx1 + bw // 2, btn_y + bh // 2, text="GENERATE CASE", font=(FONT_DISPLAY, 9, "bold"), fill="#ff9bab", tags="dynamic")
        else:
            self._court_phase_hit = None
            c.create_text(cx, btn_y + 18, text="Pick all options above, including an opponent, to continue.",
                          font=(FONT_DISPLAY, 7), fill=TDIM, tags="dynamic")
        if self._court_loading:
            c.create_text(cx, btn_y - 14, text="Generating case…", font=(FONT_DISPLAY, 8), fill=ORANGE, tags="dynamic")

    def _draw_bot_roster(self, c, x1, y1, x2, y2):
        """Draws the scrollable roster of 8 selectable bot opponents as
        holographic HUD cards (name, source, ELO, one-line style teaser).
        Clicking a card sets self._court_character."""
        ids = list(BOT_CHARACTERS.keys())
        card_h, gap = 58, 8
        total_h = len(ids) * (card_h + gap) - gap
        visible_h = max(1, y2 - y1)
        max_scroll = max(0, total_h - visible_h)
        self._court_scroll = max(0, min(self._court_scroll, max_scroll))
        y = y1 - (max_scroll - self._court_scroll if max_scroll else 0)
        clip_top, clip_bottom = y1, y2
        for cid in ids:
            char = BOT_CHARACTERS[cid]
            card_top, card_bottom = y, y + card_h
            if card_bottom >= clip_top and card_top <= clip_bottom:
                active = self._court_character == cid
                accent = char["accent"]
                pts = self._rounded_rect_points(x1, card_top, x2, card_bottom, 8)
                fill = _blend_hex(accent, ABYSS, 0.72) if active else "#0a1622"
                outline = accent if active else CORE_DIM
                c.create_polygon(pts, smooth=True, outline=outline, fill=fill, tags="dynamic")
                c.create_rectangle(x1, card_top + 3, x1 + 5, card_bottom - 3, fill=accent, outline="", tags="dynamic")
                c.create_text(x1 + 16, card_top + 13, text=char["name"], font=(FONT_DISPLAY, 9, "bold"),
                              fill=HOT if active else TEXT, anchor="w", tags="dynamic")
                c.create_text(x2 - 12, card_top + 13, text=f"ELO {char['elo']}", font=(FONT_DATA, 8, "bold"),
                              fill=accent, anchor="e", tags="dynamic")
                c.create_text(x1 + 16, card_top + 27, text=char["source"], font=(FONT_DISPLAY, 7), fill=TDIM, anchor="w", tags="dynamic")
                self._draw_wrapped_text(c, x1 + 16, card_top + 39, x2 - 16, char["teaser"],
                                        font=(FONT_DISPLAY, 7), fill=TEXT if active else TDIM)
                self._court_setup_hits[("character", cid)] = (x1, max(card_top, clip_top), x2, min(card_bottom, clip_bottom))
            y += card_h + gap
        if max_scroll > 0:
            c.create_text(x2, y1 - 6, text="scroll for more ▾" if self._court_scroll < max_scroll else "▴ scroll up",
                          font=(FONT_DISPLAY, 6), fill=TDIM, anchor="e", tags="dynamic")

    def _draw_court_briefing(self, c, ox, y1, cw, ch):
        case = self._court_case
        if not case:
            return
        self._court_word_hits = []
        cx1, cy1, cx2, cy2 = ox + 20, y1 + 10, cw - 20, ch - 15
        c.create_text(cx1, cy1 + 10, text=case["title"].upper(), font=(FONT_DISPLAY, 13, "bold"), fill=CYAN, anchor="w", tags="dynamic")
        c.create_text(cx2, cy1 + 10, text=case["case_type"].upper(), font=(FONT_DISPLAY, 8), fill=ORANGE, anchor="e", tags="dynamic")
        self._draw_experience_selector(c, cx1, cy1 + 22, cx2)
        self._draw_hline(c, cx1, cx2, cy1 + 38)

        btn_h = 50
        content_y1, content_y2 = cy1 + 48, cy2 - btn_h

        parts = [("FACTS  (click any word to see what it means)", case["facts"], TEXT, False)]
        if case.get("amendment"):
            parts.append(("CONSTITUTIONAL BASIS", case["amendment"], GOLD, True))
        parts.append(("LEGAL ISSUE", case["issue"], TEXT, True))
        witness = case.get("witness")
        if witness:
            w_text = f"{witness['name']} ({witness['role']}). {witness['summary']}"
            parts.append(("KEY WITNESS — cross-examinable during Arguments", w_text, TEXT, False))

        def estimate_height(text, bold):
            font = (FONT_DISPLAY, 9, "bold" if bold else "normal")
            return self._draw_clickable_text(c, cx1, -10000, cx2, text, font=font, fill=TEXT, dry_run=True)

        total_h = 0
        for label, text, col, bold in parts:
            total_h += 16 + estimate_height(text, bold) + 16
        total_h += 50

        visible_h = content_y2 - content_y1
        max_scroll = max(0, total_h - visible_h)
        self._court_scroll = max(0, min(self._court_scroll, max_scroll))

        y = content_y1 - (max_scroll - self._court_scroll) if max_scroll else content_y1
        for label, text, col, bold in parts:
            if content_y1 - 20 <= y <= content_y2 + 20:
                c.create_text(cx1, y, text=label, font=(FONT_DISPLAY, 7, "bold"), fill=TDIM, anchor="w", tags="dynamic")
            used = estimate_height(text, bold)
            if content_y1 - 200 <= y <= content_y2 + 200:
                self._draw_clickable_text(c, cx1, y + 16, cx2, text, font=(FONT_DISPLAY, 9, "bold" if bold else "normal"), fill=col)
            y += 16 + used + 16

        if content_y1 - 20 <= y <= content_y2 + 40:
            c.create_text(cx1, y, text=f"You argue for: {case['your_side']}", font=(FONT_DISPLAY, 9, "bold"), fill=GREEN, anchor="w", tags="dynamic")
            role_label = "Opposing counsel" if self._court_jarvis_role == "opposing" else "Presiding judge"
            bot = BOT_CHARACTERS.get(self._court_character, BOT_CHARACTERS[DEFAULT_BOT])
            c.create_text(cx1, y + 18, text=f"{bot['name']} ({role_label}): {case['jarvis_side']}", font=(FONT_DISPLAY, 8), fill=TDIM, anchor="w", tags="dynamic")

        if max_scroll > 0:
            c.create_text(cx2, content_y1 - 2, text="scroll for more ▾" if self._court_scroll < max_scroll else "▴ scroll up for more",
                          font=(FONT_DISPLAY, 6), fill=TDIM, anchor="e", tags="dynamic")

        bw, bh = 180, 34
        bx1 = (cx1 + cx2) // 2 - bw // 2
        by_btn = cy2 - btn_h // 2 - bh // 2 + 16
        self._court_phase_hit = (bx1, by_btn, bx1 + bw, by_btn + bh)
        pts = self._rounded_rect_points(bx1, by_btn, bx1 + bw, by_btn + bh, 8)
        c.create_polygon(pts, smooth=True, outline=CORE, fill="#0f2b42", tags="dynamic")
        c.create_text(bx1 + bw // 2, by_btn + bh // 2, text="BEGIN OPENING STATEMENT", font=(FONT_DISPLAY, 8, "bold"), fill=HOT, tags="dynamic")

        if self._court_term_popup:
            self._draw_term_popup(c, cx1, cx2, cy1, cy2)

    def _draw_experience_selector(self, c, x1, y, x2):
        self._court_experience_hits = {}
        labels = [("beginner", "Beginner"), ("experienced", "Experienced"), ("advanced", "Advanced")]
        c.create_text(x1, y, text="Level:", font=(FONT_DISPLAY, 7), fill=TDIM, anchor="w", tags="dynamic")
        bx = x1 + 44
        for val, label in labels:
            active = self._court_experience == val
            bw = len(label) * 6 + 16
            self._court_experience_hits[val] = (bx, y - 9, bx + bw, y + 9)
            if active:
                pts = self._rounded_rect_points(bx, y - 9, bx + bw, y + 9, 6)
                c.create_polygon(pts, smooth=True, outline=CORE, fill="#123552", tags="dynamic")
            c.create_text(bx + bw / 2, y, text=label, font=(FONT_DISPLAY, 7, "bold" if active else "normal"),
                          fill=HOT if active else TDIM, tags="dynamic")
            bx += bw + 8

        bx += 16
        c.create_text(bx, y, text="Click:", font=(FONT_DISPLAY, 7), fill=TDIM, anchor="w", tags="dynamic")
        bx += 38
        self._court_mode_hits = {}
        for val, label in [("word", "Word"), ("sentence", "Sentence")]:
            active = self._court_click_mode == val
            bw = len(label) * 6 + 16
            self._court_mode_hits[val] = (bx, y - 9, bx + bw, y + 9)
            if active:
                pts = self._rounded_rect_points(bx, y - 9, bx + bw, y + 9, 6)
                c.create_polygon(pts, smooth=True, outline=GOLD, fill="#2a2410", tags="dynamic")
            c.create_text(bx + bw / 2, y, text=label, font=(FONT_DISPLAY, 7, "bold" if active else "normal"),
                          fill=GOLD if active else TDIM, tags="dynamic")
            bx += bw + 8

    def _draw_term_popup(self, c, panel_x1, panel_x2, panel_y1, panel_y2):
        popup = self._court_term_popup
        pw = 280
        body_font = (FONT_DISPLAY, 7)
        header_h = 34
        pad_bottom = 12
        loading = self._court_term_loading == popup["word"]
        explanation = popup.get("explanation")
        if loading:
            lines = ["Explaining…"]
        elif explanation:
            lines = self._wrap_lines(explanation, body_font, pw - 24)
        else:
            lines = []
        line_h = self._line_height(body_font)
        body_h = len(lines) * line_h
        ph = max(50, header_h + body_h + pad_bottom)
        max_ph = (panel_y2 - panel_y1) - 20
        ph = min(ph, max_ph)
        px = min(max(popup["x"], panel_x1), panel_x2 - pw)
        py = min(max(popup["y"], panel_y1), panel_y2 - ph)
        c.create_rectangle(px, py, px + pw, py + ph, fill="#0a1622", outline=GOLD, width=1, tags="dynamic")
        c.create_text(px + 12, py + 14, text=f'"{popup["word"]}"', font=(FONT_DISPLAY, 9, "bold"), fill=GOLD, anchor="w", tags="dynamic")
        close_hit = (px + pw - 22, py + 4, px + pw - 4, py + 22)
        self._court_term_popup_close_hit = close_hit
        c.create_text(px + pw - 13, py + 13, text="✕", font=(FONT_DISPLAY, 9, "bold"), fill=RED, tags="dynamic")
        if loading:
            c.create_text(px + 12, py + header_h, text="Explaining…", font=body_font, fill=TDIM, anchor="w", tags="dynamic")
        elif explanation:
            self._draw_wrapped_text(c, px + 12, py + header_h, px + pw - 12, explanation, font=body_font, fill=TEXT)

    def _ease_out_back(self, x):
        x = max(0.0, min(1.0, x))
        c1 = 1.70158
        c3 = c1 + 1
        return 1 + c3 * (x - 1) ** 3 + c1 * (x - 1) ** 2

    def _draw_court_backdrop(self, c, x1, y1, x2, y2):
        cx = (x1 + x2) / 2
        sw, sh = x2 - x1, y2 - y1
        vp_y = y1 + 40
        for i in range(6):
            t = i / 5
            te = t * t
            ry = y2 + (vp_y - y2) * te
            rw = sw * 0.46 + (4 - sw * 0.46) * te
            c.create_line(cx - rw, ry, cx + rw, ry, fill=_blend_hex(CORE, ABYSS, 0.88), width=1, tags="dynamic")
        for i in range(7):
            t = i / 6
            bx = x1 + sw * 0.06 + t * sw * 0.88
            c.create_line(bx, y2, cx, vp_y, fill=_blend_hex(CORE, ABYSS, 0.9), width=1, tags="dynamic")
        c.create_oval(cx - 50, y1 + sh * 0.6 - 15, cx + 50, y1 + sh * 0.6 + 15, outline=_blend_hex(GOLD, ABYSS, 0.55), width=1, tags="dynamic")
        c.create_text(cx, y1 + sh * 0.6, text="⚖", font=(FONT_DISPLAY, 13), fill=_blend_hex(GOLD, ABYSS, 0.45), tags="dynamic")
        for col_x in (x1 + sw * 0.065, x2 - sw * 0.065):
            c.create_rectangle(col_x - 10, y1, col_x + 10, y2, outline=_blend_hex(CORE, ABYSS, 0.72),
                                width=1, fill=_blend_hex(CORE, ABYSS, 0.9), tags="dynamic")
            c.create_line(col_x, y1 + 8, col_x, y2 - 8, fill=_blend_hex(CORE, ABYSS, 0.5), width=1, tags="dynamic")
        # gallery: two depth-scaled rows per side (a dimmer, smaller "back"
        # row and a slightly larger "front" row) plus a bench rail under
        # each — reads as rows of spectators rather than a single smear.
        for side in (0, 1):
            base_x = x1 + sw * 0.11 if side == 0 else x2 - sw * 0.11
            direction = 1 if side == 0 else -1
            for row, (row_y, row_r, row_dim) in enumerate([(y1 + 6, 8, 0.3), (y1 + 30, 6, 0.5)]):
                for i in range(4):
                    gx = base_x + direction * i * (17 - row * 3)
                    c.create_oval(gx - row_r, row_y, gx + row_r, row_y + row_r * 2.5,
                                  outline="", fill=_blend_hex(TDIM, ABYSS, row_dim), tags="dynamic")
                rail_y = row_y + row_r * 2.5 + 3
                c.create_line(base_x - 4, rail_y, base_x + direction * 3 * (17 - row * 3) + 4, rail_y,
                              fill=_blend_hex(CMID, ABYSS, 0.6), width=1, tags="dynamic")
        # a foreground counsel-table edge across the very bottom of frame —
        # nearest to camera, so it's drawn largest/brightest, reinforcing
        # the fore/background scale the rest of the backdrop implies.
        table_y = y2 - 6
        c.create_rectangle(x1 + sw * 0.08, table_y, x2 - sw * 0.08, y2, outline=_blend_hex(CORE, ABYSS, 0.55),
                            width=1, fill=_blend_hex(CORE, ABYSS, 0.82), tags="dynamic")
        c.create_line(x1 + sw * 0.08, table_y, x2 - sw * 0.08, table_y, fill=_blend_hex(HOT, ABYSS, 0.4), width=1, tags="dynamic")
        self._draw_glow_ring(c, cx, y1 + 8, 4, GOLD, layers=5, spread=34)
        c.create_text(cx, y1 + 8, text="⚖", font=(FONT_DISPLAY, 15), fill=_blend_hex(GOLD, ABYSS, 0.1), tags="dynamic")
        for i in range(16):
            seed = i * 37
            mx = x1 + ((seed * 13 + self._pulse * 0.15) % sw)
            my = y1 + 24 + ((seed * 7) % max(1, sh - 44)) + math.sin(self._pulse * 0.02 + i) * 6
            mcol = GOLD if i % 3 == 0 else CORE
            c.create_oval(mx - 1.3, my - 1.3, mx + 1.3, my + 1.3, outline="", fill=_blend_hex(mcol, ABYSS, 0.68), tags="dynamic")

    # Registry of signature-prop drawers. Each takes (canvas, hx, hy, cx, arm_y,
    # fy, s, build, col, side, sway) where `side` is the prop's preferred
    # +1/-1/0 hand from the character's "figure" dict and `sway` is the
    # current gesture offset, so props ride the same motion as the arm they
    # sit near instead of feeling pasted on.
    def _draw_character_prop(self, c, prop, hx, hy, cx, arm_y, fy, s, build, col, side, sway):
        if not prop:
            return
        px = cx + (24 * s * (side or 1))
        if prop == "battered_briefcase":
            bx, by = cx - 22 * s, fy - 14 * s
            c.create_rectangle(bx, by, bx + 16 * s * build, by + 11 * s, outline=col, width=2,
                                fill=_blend_hex(col, ABYSS, 0.8), tags="dynamic")
            c.create_line(bx + 3 * s, by, bx + 3 * s, by - 3 * s, fill=col, width=1, tags="dynamic")
            c.create_line(bx + 13 * s * build, by, bx + 13 * s * build, by - 3 * s, fill=col, width=1, tags="dynamic")
        elif prop == "blazer_pin":
            c.create_oval(cx - 3 * s, arm_y + 2 * s, cx + 3 * s, arm_y + 8 * s, outline=col, width=1,
                          fill=_blend_hex(GOLD, ABYSS, 0.5), tags="dynamic")
        elif prop == "loose_tie":
            tx = cx + sway * 0.6
            c.create_line(tx, arm_y - 2 * s, tx + 2 * s, arm_y + 16 * s, fill=RED, width=3,
                          capstyle="round", tags="dynamic")
        elif prop == "legal_pad":
            bx, by = cx - 21 * s, fy - 16 * s
            c.create_rectangle(bx, by, bx + 12 * s, by + 16 * s, outline=col, width=1,
                                fill=_blend_hex(HOT, ABYSS, 0.82), tags="dynamic")
            for i in range(3):
                ly = by + 4 * s + i * 4 * s
                c.create_line(bx + 2 * s, ly, bx + 10 * s, ly, fill=col, width=1, tags="dynamic")
        elif prop == "pointer_finger":
            c.create_line(px, arm_y + 4 * s, px + 9 * s * (side or 1), arm_y - 2 * s, fill=col,
                          width=2, capstyle="round", tags="dynamic")
        elif prop == "pinky_ring":
            c.create_oval(px - 2.5 * s, arm_y + 6 * s, px + 2.5 * s, arm_y + 11 * s, outline=GOLD,
                          width=2, fill=_blend_hex(GOLD, ABYSS, 0.4), tags="dynamic")
        elif prop == "cufflink":
            c.create_oval(px - 2 * s, arm_y + 6 * s, px + 2 * s, arm_y + 10 * s, outline=HOT,
                          width=1, fill=HOT, tags="dynamic")
        elif prop == "red_thread_folder":
            bx, by = cx - 22 * s, fy - 15 * s
            c.create_rectangle(bx, by, bx + 15 * s, by + 12 * s, outline=col, width=1,
                                fill=_blend_hex(col, ABYSS, 0.82), tags="dynamic")
            c.create_line(bx + 15 * s, by + 2 * s, bx + 30 * s, by - 6 * s, fill=RED, width=1, tags="dynamic")

    def _draw_court_figure(self, c, cx, top_y, role, pose="counsel", scale=1.0, speak=0.0, talk=None,
                            freeze=False, pose_mod=None, char=None, lean_bias=0.0):
        col = {"user": GREEN, "jarvis": CORE, "judge": GOLD, "witness": PURPLE}[role]
        s = scale
        # `char` (a BOT_CHARACTERS entry) drives the data-driven silhouette:
        # proportions, gesture feel, and signature prop all come from its
        # "figure" sub-dict, with neutral defaults for anything not set —
        # so a bot with no "figure" block yet still renders (just generic),
        # and adding a new bot never requires touching this function.
        fig = (char or {}).get("figure", {}) if char else {}
        pose_mod = pose_mod or (char or {}).get("pose_mod")
        height_mult = fig.get("height", 1.0)
        build = fig.get("build", 1.0)
        posture_bias = fig.get("posture_bias", 0.0) * s  # constant signature lean, px
        gcfg = fig.get("gesture", {})
        gesture_amp = gcfg.get("amp", 1.0)
        gesture_speed = gcfg.get("speed", 1.0)
        prop = fig.get("prop")
        prop_side = fig.get("prop_side", 1)

        talk = talk or {"ticks": 0, "total": 1, "syll": 1}
        talking = (not freeze) and talk["ticks"] > 0
        bob_phase = {"user": 0.0, "jarvis": 1.6, "judge": 3.1, "witness": 4.6}.get(role, 0.0)
        if freeze:
            # held frame: no idle bob, no gesture — the figure visibly stops
            # mid-motion (used when an objection interrupts the speaker).
            gesture_sway = 0.0
        else:
            top_y = top_y + math.sin(self._pulse * 0.05 + bob_phase) * (1.4 * s)
            # a faster, small-amplitude sway layered on top of the idle bob
            # while actually talking, standing in for a gesture cycle — each
            # character's own amp/speed makes this read as "their" gesture.
            gesture_sway = (math.sin(self._pulse * 0.4 * gesture_speed + bob_phase)
                             * 3.2 * s * gesture_amp) if talking else 0.0
        # a transient lean (judge leaning in on a sharp objection, a witness
        # shifting under cross) rides on top of the signature posture_bias
        # rather than replacing it, so the character's own stance is still
        # visible underneath the momentary reaction.
        lean = posture_bias + lean_bias * s
        hr = 13 * s * height_mult
        hx, hy = cx, top_y + hr

        # ground shadow: a soft flattened ellipse under the feet, darker the
        # closer to camera (bigger `s`), giving the figures some footing
        # instead of floating flat against the backdrop.
        fy_est = top_y + 96 * s * height_mult
        shadow_w = 20 * s * build
        c.create_oval(cx - shadow_w, fy_est - 3 * s, cx + shadow_w, fy_est + 3 * s,
                      outline="", fill=_blend_hex(ABYSS, "#000000", 0.3), tags="dynamic")

        if role == "jarvis":
            self._draw_glow_ring(c, hx, hy, hr, CORE, layers=4, spread=9 * s + 6 * s * speak)
        elif speak > 0.0:
            self._draw_glow_ring(c, hx, hy, hr, col, layers=3, spread=(5 + 7 * speak) * s)
        if pose == "robe":
            y0, fy = top_y + 26 * s * height_mult, top_y + 96 * s * height_mult
            fill = _blend_hex(GOLD, ABYSS, 0.86)
            c.create_polygon(cx - 13 * s + lean * 0.2, y0, cx + 13 * s + lean * 0.2, y0,
                              cx + 18 * s + lean, fy, cx - 18 * s + lean, fy,
                              outline=GOLD, width=2, fill=fill, tags="dynamic")
        else:
            y0 = top_y + 26 * s * height_mult
            hip_y = top_y + 62 * s * height_mult
            fy = top_y + 96 * s * height_mult
            arm_y = top_y + 34 * s * height_mult
            c.create_line(cx, y0, cx + lean, hip_y, fill=col, width=3 * build, capstyle="round", tags="dynamic")
            c.create_line(cx + lean, arm_y, cx - 19 * s * build + gesture_sway + lean,
                          arm_y + 14 * s - abs(gesture_sway) * 0.5,
                          fill=col, width=3 * build, capstyle="round", tags="dynamic")
            if pose == "sworn":
                c.create_line(cx + lean, arm_y, cx + 14 * s + lean, top_y + 16 * s, fill=col, width=3 * build,
                              capstyle="round", tags="dynamic")
            else:
                c.create_line(cx + lean, arm_y, cx + 19 * s * build - gesture_sway + lean,
                              arm_y + 8 * s - abs(gesture_sway) * 0.5,
                              fill=col, width=3 * build, capstyle="round", tags="dynamic")
            c.create_line(cx + lean, hip_y, cx - 13 * s * build, fy, fill=col, width=3 * build, capstyle="round", tags="dynamic")
            c.create_line(cx + lean, hip_y, cx + 13 * s * build, fy, fill=col, width=3 * build, capstyle="round", tags="dynamic")
            # per-bot silhouette cue — a small extra shape layered on the
            # base skeleton so each bot reads as itself even before it
            # speaks, without needing a whole separate sprite/pose system.
            if pose_mod == "slouch":
                c.create_line(cx - 9 * s, y0 + 2 * s, cx + 7 * s, y0 + 10 * s, fill=col, width=2, tags="dynamic")
            elif pose_mod == "hair_flip":
                flip = math.sin(self._pulse * 0.12 + bob_phase) * 4 * s
                c.create_arc(hx + hr * 0.2, hy - hr * 1.3, hx + hr * 1.7 + flip, hy + hr * 0.3, start=250, extent=120,
                             style="arc", outline=col, width=2, tags="dynamic")
            elif pose_mod == "point":
                c.create_line(cx + 19 * s - gesture_sway, arm_y + 8 * s - abs(gesture_sway) * 0.5,
                              cx + 31 * s, arm_y + 2 * s, fill=col, width=2, capstyle="round", tags="dynamic")
            elif pose_mod == "poised":
                c.create_line(cx - 7 * s, y0 + 5 * s, cx + 7 * s, y0 + 5 * s, fill=col, width=2, tags="dynamic")
            elif pose_mod == "rigid":
                c.create_line(cx - 16 * s, fy, cx - 16 * s, fy - 6 * s, fill=col, width=2, tags="dynamic")
                c.create_line(cx + 16 * s, fy, cx + 16 * s, fy - 6 * s, fill=col, width=2, tags="dynamic")
            elif pose_mod == "showy":
                c.create_line(cx + 19 * s - gesture_sway, arm_y + 8 * s - abs(gesture_sway) * 0.5,
                              cx + 27 * s, arm_y - 11 * s, fill=col, width=2, capstyle="round", tags="dynamic")
                c.create_oval(cx + 24 * s, arm_y - 15 * s, cx + 30 * s, arm_y - 9 * s, outline=col, width=1, tags="dynamic")
            elif pose_mod == "crossed_arms":
                c.create_line(cx - 12 * s, arm_y + 7 * s, cx + 12 * s, arm_y - 1 * s, fill=col, width=3, capstyle="round", tags="dynamic")
                c.create_line(cx - 12 * s, arm_y - 1 * s, cx + 12 * s, arm_y + 7 * s, fill=col, width=3, capstyle="round", tags="dynamic")
            elif pose_mod == "lean_forward":
                c.create_line(hx, hy - hr, hx + 7 * s, hy - hr - 5 * s, fill=col, width=2, tags="dynamic")
            if prop:
                self._draw_character_prop(c, prop, hx, hy, cx, arm_y, fy, s, build, col, prop_side, gesture_sway)
        c.create_oval(hx - hr + lean, hy - hr, hx + hr + lean, hy + hr, outline=col, width=3, fill=ABYSS, tags="dynamic")
        # mouth: a short chord across the lower head that opens/closes at a
        # rate derived from the line's estimated syllable count, so it reads
        # as talking-paced rather than a generic pulse.
        if talking:
            frame_per_syll = max(2.0, talk["total"] / max(1, talk["syll"]))
            elapsed = talk["total"] - talk["ticks"]
            mouth_open = (elapsed % frame_per_syll) < frame_per_syll * 0.45
            my = hy + hr * 0.42
            mw = hr * (0.62 if mouth_open else 0.4)
            mh = hr * (0.32 if mouth_open else 0.08)
            c.create_oval(hx - mw / 2 + lean, my - mh / 2, hx + mw / 2 + lean, my + mh / 2, outline=col, width=1,
                          fill=ABYSS if mouth_open else col, tags="dynamic")
        return top_y + 96 * s * height_mult

    def _draw_court_stand(self, c, cx, top_y, w, h, outline_col):
        fill = _blend_hex(outline_col, ABYSS, 0.85)
        top_w = w * 0.86
        c.create_polygon(cx - top_w / 2, top_y, cx + top_w / 2, top_y, cx + w / 2, top_y + h, cx - w / 2, top_y + h,
                          outline=outline_col, width=2, fill=fill, tags="dynamic")
        return top_y + h

    def _draw_jury_row(self, c, x1, top_y, n=7, gap=20, reaction=0.0):
        col = "#4a7a94"
        active_idx = (self._pulse // 18) % n
        for i in range(n):
            jx = x1 + i * gap
            # each juror gets its own phase/period offset (seeded off its
            # index) so the idle shifting never reads as synchronized.
            seed = (i * 53) % 11
            period = 0.03 + (seed % 5) * 0.006
            jy = top_y + math.sin(self._pulse * period + i * 0.7 + seed) * 1.2
            if reaction > 0.0:
                # lean-in beat: jurors shift forward/down slightly and
                # unevenly, as if reacting to what just happened.
                lean = reaction * (2.4 + (seed % 4))
                jy += lean * math.sin(self._pulse * 0.15 + i * 1.3)
            jcol = _blend_hex(GOLD, col, 0.4) if i == active_idx else col
            c.create_oval(jx - 6, jy, jx + 6, jy + 12, outline=jcol, width=2, fill=ABYSS, tags="dynamic")
            c.create_line(jx, jy + 12, jx, jy + 26, fill=jcol, width=2, tags="dynamic")
            if reaction > 0.3 and i % 2 == (self._pulse // 4) % 2:
                # sparse murmur indicator — only a subset of jurors "murmur"
                # on any given frame, not all of them at once.
                c.create_text(jx, jy - 8, text="…", font=(FONT_DISPLAY, 8, "bold"),
                              fill=_blend_hex(GOLD, col, 0.25), tags="dynamic")
        c.create_line(x1 - 8, top_y + 34, x1 + (n - 1) * gap + 8, top_y + 34, fill=col, width=1, tags="dynamic")
        c.create_text(x1 + (n - 1) * gap / 2, top_y + 46, text="JURY", font=(FONT_DISPLAY, 7, "bold"), fill=col, tags="dynamic")

    def _draw_court_bubble(self, c, cx, bottom_y, text, role, pop, scene_x1, scene_x2):
        if not text:
            return
        col = {"user": GREEN, "jarvis": CORE, "judge": GOLD, "witness": PURPLE}[role]
        fill = _blend_hex(col, ABYSS, 0.86)
        font = (FONT_DISPLAY, 7)
        max_w = 150
        lines = self._wrap_lines(text, font, max_w) or [""]
        line_h = self._line_height(font)
        pad = 9
        box_w = max_w + pad * 2
        box_h = len(lines) * line_h + pad * 2
        by2 = bottom_y - 10
        by1 = by2 - box_h
        bx1 = max(scene_x1 + 4, min(cx - box_w / 2, scene_x2 - 4 - box_w))
        bx2 = bx1 + box_w
        if pop < 1.0:
            eb = self._ease_out_back(pop)
            k = 0.62 + 0.38 * eb
            bx1 = cx + (bx1 - cx) * k; bx2 = cx + (bx2 - cx) * k
            by1 = by2 + (by1 - by2) * k
            rise = max(0.0, 1.0 - eb) * 7
            by1 -= rise; by2 -= rise
        pts = self._rounded_rect_points(bx1, by1, bx2, by2, 10)
        c.create_polygon(pts, smooth=True, outline=col, width=2, fill=fill, tags="dynamic")
        tail_cx = min(max(cx, bx1 + 14), bx2 - 14)
        c.create_polygon(tail_cx - 7, by2 - 2, tail_cx + 7, by2 - 2, cx, bottom_y, fill=fill, outline=col, width=1, tags="dynamic")
        if pop >= 0.62:
            ty = by1 + pad + line_h / 2
            for ln in lines:
                c.create_text((bx1 + bx2) / 2, ty, text=ln, font=font, fill=TEXT, tags="dynamic")
                ty += line_h

    def _gavel_head_y(self, k, start_y, strike_y):
        if k < 0.15:
            wind_t = k / 0.15
            return start_y - 16 * math.sin(wind_t * math.pi)
        fall_t = min(1.0, (k - 0.15) / 0.67)
        return start_y + (strike_y - start_y) * (fall_t * fall_t)

    def _draw_court_transition_strike(self, c, x1, y1, x2, y2, k):
        c.create_rectangle(x1, y1, x2, y2, fill=ABYSS, outline="", tags="dynamic")
        cx = (x1 + x2) / 2
        strike_y = y1 + (y2 - y1) * 0.42
        start_y = y1 - 60
        gy = self._gavel_head_y(k, start_y, strike_y)
        fall_t = 0.0 if k < 0.15 else min(1.0, (k - 0.15) / 0.67)
        skew = 10 * (1 - fall_t) if k >= 0.15 else 10 * (k / 0.15)
        block_y = strike_y + 28
        c.create_rectangle(cx - 58, block_y, cx + 58, block_y + 13, fill=CORE_DIM, outline=CORE, width=1, tags="dynamic")
        for back, alpha_steps in ((0.12, 2), (0.06, 1)):
            ggy = self._gavel_head_y(max(0.0, k - back), start_y, strike_y)
            trail_col = _blend_hex(GOLD, ABYSS, 0.55 + 0.15 * alpha_steps)
            c.create_rectangle(cx - 6, ggy - 46, cx + 6, ggy, fill=trail_col, outline="", tags="dynamic")
        c.create_rectangle(cx - 6, gy - 46, cx + 6, gy, fill=GOLD, outline="", tags="dynamic")
        c.create_polygon(cx - 23 - skew, gy, cx + 23 - skew, gy, cx + 23 + skew, gy + 22, cx - 23 + skew, gy + 22,
                          fill=HOT, outline=GOLD, width=2, tags="dynamic")

    def _draw_court_transition_shuffle(self, c, x1, y1, x2, y2, k):
        """Papers being squared up and set down — used for the
        arguments -> closing transition instead of a gavel strike, since
        nothing was actually ruled on; it's a beat of the counsel gathering
        themselves, not a court action."""
        c.create_rectangle(x1, y1, x2, y2, fill=ABYSS, outline="", tags="dynamic")
        cx, cy = (x1 + x2) / 2, y1 + (y2 - y1) * 0.46
        n = 5
        eb = self._ease_out_back(min(1.0, k / 0.8))
        for i in range(n):
            seed = i * 29
            # each sheet starts scattered (offset + rotated) and eases into a
            # neat stack as the transition progresses.
            scatter_x = (((seed * 7) % 61) - 30) * (1.0 - eb)
            scatter_y = (((seed * 11) % 41) - 20) * (1.0 - eb)
            angle = (((seed * 13) % 40) - 20) * (1.0 - eb)
            w, h = 34, 44
            px, py = cx + scatter_x, cy + scatter_y - (n - i) * 1.5
            rad = math.radians(angle)
            cosr, sinr = math.cos(rad), math.sin(rad)
            corners = []
            for dx, dy in ((-w / 2, -h / 2), (w / 2, -h / 2), (w / 2, h / 2), (-w / 2, h / 2)):
                corners += [px + dx * cosr - dy * sinr, py + dx * sinr + dy * cosr]
            shade = _blend_hex(TEXT, ABYSS, 0.35 + i * 0.08)
            c.create_polygon(corners, outline=CORE_DIM, width=1, fill=shade, tags="dynamic")
            for ln in range(3):
                lx1, ly1 = px - w * 0.32, py - h * 0.28 + ln * h * 0.22
                lx2 = px + w * 0.32
                c.create_line(lx1, ly1, lx2, ly1, fill=CORE_DIM, width=1, tags="dynamic")

    def _draw_court_transition_flash(self, c, x1, y1, x2, y2, t=1.0):
        wash = _blend_hex("#fffef2", ABYSS, t * 0.4)
        c.create_rectangle(x1, y1, x2, y2, fill=wash, outline="", tags="dynamic")
        cx, cy = (x1 + x2) / 2, y1 + (y2 - y1) * 0.42
        self._draw_glow_ring(c, cx, cy, 6, GOLD, layers=6, spread=90 + 70 * t, base="#fffef2")
        self._draw_glow_ring(c, cx, cy, 4, HOT, layers=4, spread=50 + 40 * t, base="#fffef2")

    def _draw_ruling_gavel_overlay(self, c, x1, y1, x2, y2, k):
        """Fast gavel strike drawn on top of the (frozen) scene when an
        objection ruling lands — same wind-up/fall curve as the phase
        transition strike, but without wiping the scene to black, since the
        courtroom should stay visible through the beat. Returns True on the
        frame contact happens, so the caller can fire a flash/shake once."""
        cx = (x1 + x2) / 2
        strike_y = y1 + 34
        start_y = y1 - 26
        gy = self._gavel_head_y(k, start_y, strike_y)
        fall_t = 0.0 if k < 0.15 else min(1.0, (k - 0.15) / 0.67)
        block_y = strike_y + 20
        c.create_rectangle(cx - 40, block_y, cx + 40, block_y + 9, fill=CORE_DIM, outline=CORE, width=1, tags="dynamic")
        c.create_rectangle(cx - 5, gy - 34, cx + 5, gy, fill=GOLD, outline="", tags="dynamic")
        skew = 8 * (1 - fall_t) if k >= 0.15 else 8 * (k / 0.15)
        c.create_polygon(cx - 17 - skew, gy, cx + 17 - skew, gy, cx + 17 + skew, gy + 16, cx - 17 + skew, gy + 16,
                          fill=HOT, outline=GOLD, width=2, tags="dynamic")
        return fall_t >= 0.98

    def _draw_court_scene(self, c, x1, y1, x2, y2, shake=(0, 0)):
        if shake != (0, 0):
            x1, x2, y1, y2 = x1 + shake[0], x2 + shake[0], y1 + shake[1], y2 + shake[1]
        case = self._court_case
        phase = self._court_state
        bot = BOT_CHARACTERS.get(self._court_character, BOT_CHARACTERS[DEFAULT_BOT])
        scene_w = x2 - x1
        cx = (x1 + x2) / 2
        jarvis_is_judge = self._court_jarvis_role == "judge"
        witness_active = self._court_cross_exam_active and bool(case.get("witness"))
        jury_active = bool(self._court_jury_mode)
        # true while an objection is pending a ruling or the ruling gavel
        # beat is still playing — counsel/witness figures hold still rather
        # than continuing to talk through the interruption.
        interrupt = self._court_objection_loading or self._court_ruling_anim > 0

        POP_FRAMES = 10
        def pop_progress(role):
            return 1.0 - (self._court_bubble_pop.get(role, 0) / POP_FRAMES)

        user_line = jarvis_line = witness_line = None
        for t in self._court_transcript:
            if t["phase"] != phase:
                continue
            sp = t["speaker"]
            if sp == bot["label"]: jarvis_line = t["text"]
            elif sp.startswith("WITNESS"): witness_line = t["text"]
            else: user_line = t["text"]
        judge_line = None
        for o in self._court_objections:
            if o["phase"] == phase:
                judge_line = f"{o['ruling']} — {o['reason']}"

        current = {"phase": phase, "user": user_line, "jarvis": jarvis_line,
                   "judge": judge_line, "witness": witness_line}
        last = self._court_bubble_state
        if last.get("phase") != phase:
            last = {"phase": phase, "user": None, "jarvis": None, "judge": None, "witness": None}
        bench_top = y1 + 4
        next_last = dict(current)
        for role in ("user", "jarvis", "judge", "witness"):
            if role == "judge" and self._court_ruling_anim > 0:
                # hold the old ruling text (or none) until the gavel beat
                # finishes, instead of popping the new ruling in immediately —
                # this is what gives the ruling a "beat" before the text lands.
                next_last["judge"] = last.get("judge")
                continue
            if current[role] != last.get(role):
                self._court_bubble_pop[role] = POP_FRAMES
                syll = _estimate_syllable_count(current[role])
                total_ticks = max(18, min(150, syll * 7))
                self._court_talk[role] = {"ticks": total_ticks, "total": total_ticks, "syll": syll}
                if role == "judge" and current[role]:
                    self._spawn_burst(cx, bench_top + 66, colors=[GOLD, HOT], n=8)
        self._court_bubble_state = next_last

        self._draw_court_backdrop(c, x1, y1, x2, y2)

        # the judge (whichever figure is presiding) leans subtly forward on
        # the ruling beat — a decaying transient rather than a pose_mod, so
        # it reads as a reaction to *this* objection, not a standing trait.
        judge_lean_px = self._court_judge_lean * 6
        if jarvis_is_judge:
            foot = self._draw_court_figure(c, cx, bench_top, "jarvis", pose="robe",
                                            speak=max(0.0, 1.0 - pop_progress("jarvis")),
                                            talk=self._court_talk["jarvis"], char=bot, lean_bias=judge_lean_px)
            self._draw_court_stand(c, cx, foot + 4, 150, 26, GOLD)
            c.create_text(cx, foot + 42, text=f"{bot['label']} · PRESIDING", font=(FONT_DISPLAY, 7, "bold"), fill=bot["accent"], tags="dynamic")
            self._draw_court_bubble(c, cx, bench_top - 6, jarvis_line, "jarvis", pop_progress("jarvis"), x1, x2)
            if self._court_catchphrase_frames > 0:
                self._draw_catchphrase_banner(c, cx, bench_top - 6, self._court_catchphrase, bot["accent"])
        else:
            foot = self._draw_court_figure(c, cx, bench_top, "judge", pose="robe",
                                            speak=max(0.0, 1.0 - pop_progress("judge")),
                                            talk=self._court_talk["judge"], lean_bias=judge_lean_px)
            self._draw_court_stand(c, cx, foot + 4, 150, 26, GOLD)
            c.create_text(cx, foot + 42, text="THE COURT", font=(FONT_DISPLAY, 7, "bold"), fill=GOLD, tags="dynamic")
            self._draw_court_bubble(c, cx, bench_top - 6, judge_line, "judge", pop_progress("judge"), x1, x2)

        if jury_active:
            self._draw_jury_row(c, x1 + 16, y1 + 8, n=7, gap=20,
                                reaction=self._court_jury_reaction / 24.0 if self._court_jury_reaction else 0.0)

        ENTER_FRAMES = 14
        if witness_active:
            self._court_witness_enter = min(ENTER_FRAMES, self._court_witness_enter + 1)
            # the longer cross-exam runs, the more the witness's posture
            # drifts — capped so it reads as growing discomfort, not a
            # collapse.
            self._court_witness_unease = min(240, self._court_witness_unease + 1)
        else:
            self._court_witness_enter = 0
            self._court_witness_unease = 0
        if witness_active:
            enter_eb = self._ease_out_back(self._court_witness_enter / ENTER_FRAMES)
            slide = (1.0 - enter_eb) * 50
            wx = cx + scene_w * 0.19 + slide
            wtop = bench_top + 52
            unease = min(1.0, self._court_witness_unease / 240)
            shift = math.sin(self._pulse * 0.02) * 4 * unease
            wfoot = self._draw_court_figure(c, wx, wtop, "witness", pose="sworn", scale=0.82,
                                             speak=max(0.0, 1.0 - pop_progress("witness")),
                                             talk=self._court_talk["witness"], freeze=interrupt, lean_bias=shift)
            self._draw_court_stand(c, wx, wfoot + 4, 66, 22, PURPLE)
            c.create_text(wx, wfoot + 38, text="WITNESS", font=(FONT_DISPLAY, 7, "bold"), fill=PURPLE, tags="dynamic")
            self._draw_court_bubble(c, wx, wtop - 6, witness_line, "witness", pop_progress("witness"), x1, x2)

        pod_top = y2 - 104
        if jarvis_is_judge:
            ufoot = self._draw_court_figure(c, cx, pod_top, "user", speak=max(0.0, 1.0 - pop_progress("user")),
                                             talk=self._court_talk["user"], freeze=interrupt)
            self._draw_court_stand(c, cx, ufoot + 4, 84, 30, GREEN)
            c.create_text(cx, ufoot + 46, text="YOU", font=(FONT_DISPLAY, 7, "bold"), fill=GREEN, tags="dynamic")
            self._draw_court_bubble(c, cx, pod_top - 6, user_line, "user", pop_progress("user"), x1, x2)
        else:
            ux, jx = x1 + scene_w * 0.24, x2 - scene_w * 0.24
            ufoot = self._draw_court_figure(c, ux, pod_top, "user", speak=max(0.0, 1.0 - pop_progress("user")),
                                             talk=self._court_talk["user"], freeze=interrupt)
            self._draw_court_stand(c, ux, ufoot + 4, 84, 30, GREEN)
            c.create_text(ux, ufoot + 46, text="YOU", font=(FONT_DISPLAY, 7, "bold"), fill=GREEN, tags="dynamic")
            self._draw_court_bubble(c, ux, pod_top - 6, user_line, "user", pop_progress("user"), x1, x2)
            jfoot = self._draw_court_figure(c, jx, pod_top, "jarvis", speak=max(0.0, 1.0 - pop_progress("jarvis")),
                                             talk=self._court_talk["jarvis"], freeze=interrupt, char=bot)
            self._draw_court_stand(c, jx, jfoot + 4, 84, 30, bot["accent"])
            c.create_text(jx, jfoot + 46, text=f"{bot['label']} · OPPOSING COUNSEL", font=(FONT_DISPLAY, 7, "bold"), fill=bot["accent"], tags="dynamic")
            self._draw_court_bubble(c, jx, pod_top - 6, jarvis_line, "jarvis", pop_progress("jarvis"), x1, x2)
            if self._court_catchphrase_frames > 0:
                self._draw_catchphrase_banner(c, jx, pod_top - 6, self._court_catchphrase, bot["accent"])

        for role in ("user", "jarvis", "judge", "witness"):
            if self._court_bubble_pop.get(role, 0) > 0:
                self._court_bubble_pop[role] -= 1
            if self._court_talk[role]["ticks"] > 0 and not (role != "judge" and interrupt):
                self._court_talk[role]["ticks"] -= 1
        if self._court_jury_reaction > 0:
            self._court_jury_reaction -= 1
        if self._court_catchphrase_frames > 0:
            self._court_catchphrase_frames -= 1
        if self._court_judge_lean > 0.0:
            self._court_judge_lean = max(0.0, self._court_judge_lean - 0.08)

    def _draw_court_log_overlay(self, c, ox, y1, cw, ch, phase):
        lx1, ly1, lx2, ly2 = ox + 30, y1 + 30, cw - 30, ch - 15
        c.create_rectangle(lx1, ly1, lx2, ly2, fill=ABYSS, outline=CORE, width=1, tags="dynamic")
        c.create_text(lx1 + 16, ly1 + 16, text="TRANSCRIPT — THIS PHASE", font=(FONT_DISPLAY, 10, "bold"), fill=CYAN, anchor="w", tags="dynamic")
        self._draw_hline(c, lx1 + 16, lx2 - 16, ly1 + 30)
        items = [(t["speaker"], t["text"]) for t in self._court_transcript if t["phase"] == phase]
        scroll = self._draw_message_log(c, lx1 + 10, ly1 + 40, lx2 - 10, ly2 - 10, items, self._court_scroll)
        self._court_scroll = scroll

    def _draw_court_proceeding(self, c, ox, y1, cw, ch):
        case = self._court_case
        phase_labels = {"opening": "OPENING STATEMENT", "arguments": "ARGUMENTS & REBUTTALS", "closing": "CLOSING ARGUMENT"}
        next_phase = {"opening": "arguments", "arguments": "closing", "closing": None}
        phase = self._court_state
        c.create_text(ox + 20, y1 + 10, text=f"{case['title']}  ·  {phase_labels.get(phase, phase.upper())}",
                      font=(FONT_DISPLAY, 9, "bold"), fill=CYAN, anchor="w", tags="dynamic")
        c.create_text(cw - 20, y1 + 10, text=f"Arguing for: {case['your_side']}", font=(FONT_DISPLAY, 7), fill=GREEN, anchor="e", tags="dynamic")
        self._draw_hline(c, ox + 10, cw - 10, y1 + 22)

        extra_row = phase == "arguments"
        scene_x1, scene_y1, scene_x2, scene_y2 = ox + 10, y1 + 30, cw - 10, ch - 96

        # a newly-appended ruling kicks off the gavel/freeze-frame beat below,
        # bumps the jury if the ruling went against the user, and gives the
        # camera a small push toward the bench.
        RULING_TOTAL = 22
        if len(self._court_objections) > self._court_last_objection_count:
            self._court_last_objection_count = len(self._court_objections)
            if self._court_transition_frame <= 0:
                self._court_ruling_anim = RULING_TOTAL
                self._court_ruling_burst_fired = False
            newest = self._court_objections[-1]
            against_user = (newest["by"] == "YOU" and newest["ruling"] == "OVERRULED") or \
                           (newest["by"] != "YOU" and newest["ruling"] == "SUSTAINED")
            if against_user:
                self._court_jury_reaction = 24
            self._court_cam_push = 1.0
            self._court_judge_lean = 1.0
            if self._court_jarvis_role != "judge":
                self._fire_catchphrase("objection_won" if against_user else "objection_lost", once=False)

        # subtle push toward the focal point (the bench) on key beats, eased
        # back down every frame rather than a hard cut to/from a fixed camera.
        push = self._court_cam_push
        if push > 0.01:
            fx = scene_x1 + (scene_x2 - scene_x1) * self._court_cam_focus[0]
            fy = scene_y1 + (scene_y2 - scene_y1) * self._court_cam_focus[1]
            shrink = push * 0.09
            draw_x1 = scene_x1 + (fx - scene_x1) * shrink
            draw_x2 = scene_x2 - (scene_x2 - fx) * shrink
            draw_y1 = scene_y1 + (fy - scene_y1) * shrink
            draw_y2 = scene_y2 - (scene_y2 - fy) * shrink
            self._court_cam_push *= 0.90
        else:
            draw_x1, draw_y1, draw_x2, draw_y2 = scene_x1, scene_y1, scene_x2, scene_y2
            self._court_cam_push = 0.0

        tf = self._court_transition_frame
        TOTAL = 26
        if tf > 0:
            p = 1.0 - (tf / TOTAL)
            shuffle = self._court_transition_kind == "shuffle"
            if p < 0.56:
                if shuffle:
                    self._draw_court_transition_shuffle(c, scene_x1, scene_y1, scene_x2, scene_y2, min(1.0, p / 0.56))
                else:
                    self._draw_court_transition_strike(c, scene_x1, scene_y1, scene_x2, scene_y2, min(1.0, p / 0.56))
            elif p < 0.68:
                flash_t = (p - 0.56) / 0.12
                self._draw_court_transition_flash(c, scene_x1, scene_y1, scene_x2, scene_y2, flash_t * (0.5 if shuffle else 1.0))
                if not self._court_transition_burst_fired:
                    self._spawn_burst((scene_x1 + scene_x2) / 2, scene_y1 + (scene_y2 - scene_y1) * 0.42,
                                       colors=([CORE, HOT] if shuffle else [GOLD, HOT, CORE]), n=(14 if shuffle else 28))
                    self._court_transition_burst_fired = True
            else:
                settle_t = (p - 0.68) / (1.0 - 0.68)
                shake_mag = 6 * (1.0 - settle_t)
                shake = (random.uniform(-shake_mag, shake_mag), random.uniform(-shake_mag, shake_mag))
                self._draw_court_scene(c, scene_x1, scene_y1, scene_x2, scene_y2, shake=shake)
            self._court_transition_frame -= 1
        elif self._court_ruling_anim > 0:
            ra = self._court_ruling_anim
            rp = 1.0 - (ra / RULING_TOTAL)
            if rp < 0.55:
                # wind-up and fall — the scene stays visible and frozen
                # (via `interrupt` inside _draw_court_scene) while the gavel
                # comes down, instead of cutting away from the room.
                self._draw_court_scene(c, draw_x1, draw_y1, draw_x2, draw_y2)
                self._draw_ruling_gavel_overlay(c, draw_x1, draw_y1, draw_x2, draw_y2, min(1.0, rp / 0.55))
            elif rp < 0.7:
                # contact: a soft flash + a small shake, not a full white-out
                flash_t = (rp - 0.55) / 0.15
                shake_mag = 5 * (1.0 - flash_t)
                shake = (random.uniform(-shake_mag, shake_mag), random.uniform(-shake_mag, shake_mag))
                self._draw_court_scene(c, draw_x1, draw_y1, draw_x2, draw_y2, shake=shake)
                self._draw_court_transition_flash(c, draw_x1, draw_y1, draw_x2, draw_y2, flash_t * 0.5)
                if not self._court_ruling_burst_fired:
                    self._spawn_burst((draw_x1 + draw_x2) / 2, draw_y1 + 34, colors=[GOLD, HOT], n=14)
                    self._court_ruling_burst_fired = True
            else:
                # brief freeze-frame beat before the ruling text is allowed
                # to pop in — mimics the pause of a real ruling landing.
                self._draw_court_scene(c, draw_x1, draw_y1, draw_x2, draw_y2)
            self._court_ruling_anim -= 1
        else:
            self._draw_court_scene(c, draw_x1, draw_y1, draw_x2, draw_y2)

        if extra_row:
            ctrl_y = ch - 84
            obw, obh = 90, 24
            obx1 = ox + 10
            self._court_objection_hit = (obx1, ctrl_y, obx1 + obw, ctrl_y + obh)
            pts = self._rounded_rect_points(obx1, ctrl_y, obx1 + obw, ctrl_y + obh, 6)
            c.create_polygon(pts, smooth=True, outline=RED, fill="#2a0f14", tags="dynamic")
            c.create_text(obx1 + obw // 2, ctrl_y + obh // 2, text="⚠ OBJECT", font=(FONT_DISPLAY, 7, "bold"), fill="#ff9bab", tags="dynamic")

            if case.get("witness"):
                cxw, cxh = 110, 24
                cxx1 = obx1 + obw + 8
                active = self._court_cross_exam_active
                self._court_cross_exam_hit = (cxx1, ctrl_y, cxx1 + cxw, ctrl_y + cxh)
                pts = self._rounded_rect_points(cxx1, ctrl_y, cxx1 + cxw, ctrl_y + cxh, 6)
                c.create_polygon(pts, smooth=True, outline=CORE, fill="#123552" if active else "#0a1622", tags="dynamic")
                c.create_text(cxx1 + cxw // 2, ctrl_y + cxh // 2, text="🎙 CROSS-EXAM" if not active else "🎙 EXAMINING", font=(FONT_DISPLAY, 7, "bold"), fill=HOT if active else TEXT, tags="dynamic")

            if case.get("exhibits"):
                evw, evh = 90, 24
                evx1 = cw - 10 - evw
                self._court_evidence_hit = (evx1, ctrl_y, evx1 + evw, ctrl_y + evh)
                pts = self._rounded_rect_points(evx1, ctrl_y, evx1 + evw, ctrl_y + evh, 6)
                c.create_polygon(pts, smooth=True, outline=GOLD, fill="#2a2410", tags="dynamic")
                c.create_text(evx1 + evw // 2, ctrl_y + evh // 2, text="📄 EXHIBITS", font=(FONT_DISPLAY, 7, "bold"), fill=GOLD, tags="dynamic")

        if self._court_objection_menu_open:
            self._draw_objection_menu(c, ox, ctrl_y if extra_row else y1 + 30, cw, ch)

        if self._court_show_evidence and case.get("exhibits"):
            self._draw_evidence_overlay(c, ox, y1, cw, ch)

        if self._court_show_log:
            self._draw_court_log_overlay(c, ox, y1, cw, ch, phase)

        status_y = ch - 50
        if self._court_loading:
            bot = BOT_CHARACTERS.get(self._court_character, BOT_CHARACTERS[DEFAULT_BOT])
            c.create_text((ox + cw) // 2, status_y, text=f"{bot['label']} is responding…", font=(FONT_DISPLAY, 8), fill=ORANGE, tags="dynamic")
        elif self._court_objection_loading:
            c.create_text((ox + cw) // 2, status_y, text="Judge is ruling…", font=(FONT_DISPLAY, 8), fill=ORANGE, tags="dynamic")
        elif self._court_hint_loading:
            c.create_text((ox + cw) // 2, status_y, text="Thinking of a tip…", font=(FONT_DISPLAY, 8), fill=ORANGE, tags="dynamic")
        elif self._court_hint:
            self._draw_wrapped_text(c, ox + 20, status_y - 6, cw - 180, "💡 " + self._court_hint, font=(FONT_DISPLAY, 7), fill=GOLD)
        elif self._court_cross_exam_active:
            c.create_text(ox + 20, status_y, text="Type your question for the witness and press Send.", font=(FONT_DISPLAY, 7), fill=GOLD, anchor="w", tags="dynamic")
        else:
            c.create_text(ox + 20, status_y, text="Type your argument below and press Send.", font=(FONT_DISPLAY, 7), fill=TDIM, anchor="w", tags="dynamic")

        hbw, hbh = 90, 30
        hbx1 = ox + 10
        self._court_hint_hit = (hbx1, ch - 40, hbx1 + hbw, ch - 40 + hbh)
        pts = self._rounded_rect_points(hbx1, ch - 40, hbx1 + hbw, ch - 40 + hbh, 8)
        c.create_polygon(pts, smooth=True, outline=GOLD, fill="#2a2410", tags="dynamic")
        c.create_text(hbx1 + hbw // 2, ch - 40 + hbh // 2, text="💡 HINT", font=(FONT_DISPLAY, 8, "bold"), fill=GOLD, tags="dynamic")

        lbw, lbh = 118, 30
        lbx1 = hbx1 + hbw + 8
        self._court_log_hit = (lbx1, ch - 40, lbx1 + lbw, ch - 40 + lbh)
        pts = self._rounded_rect_points(lbx1, ch - 40, lbx1 + lbw, ch - 40 + lbh, 8)
        log_active = self._court_show_log
        c.create_polygon(pts, smooth=True, outline=(CORE if log_active else CORE_DIM),
                          fill="#123552" if log_active else "#0a1622", tags="dynamic")
        c.create_text(lbx1 + lbw // 2, ch - 40 + lbh // 2, text="✕ HIDE LOG" if log_active else "☰ TRANSCRIPT",
                      font=(FONT_DISPLAY, 8, "bold"), fill=HOT if log_active else TEXT, tags="dynamic")

        nxt = next_phase.get(phase)
        bw, bh = 160, 30
        bx1 = cw - 20 - bw
        self._court_phase_hit = (bx1, ch - 40, bx1 + bw, ch - 40 + bh)
        pts = self._rounded_rect_points(bx1, ch - 40, bx1 + bw, ch - 40 + bh, 8)
        c.create_polygon(pts, smooth=True, outline=CORE, fill="#0f2b42", tags="dynamic")
        label = f"NEXT: {phase_labels.get(nxt,'')}" if nxt else "GET VERDICT"
        c.create_text(bx1 + bw // 2, ch - 40 + bh // 2, text=label, font=(FONT_DISPLAY, 8, "bold"), fill=HOT, tags="dynamic")

    def _draw_objection_menu(self, c, ox, anchor_y, cw, ch):
        self._court_objection_type_hits = {}
        mw, mh = 220, len(OBJECTION_TYPES) * 26 + 16
        mx1, my1 = ox + 10, anchor_y + 30
        c.create_rectangle(mx1, my1, mx1 + mw, my1 + mh, fill=ABYSS, outline=RED, width=1, tags="dynamic")
        y = my1 + 10
        for otype in OBJECTION_TYPES:
            self._court_objection_type_hits[otype] = (mx1 + 6, y, mx1 + mw - 6, y + 22)
            c.create_text(mx1 + 14, y + 11, text=otype, font=(FONT_DISPLAY, 7), fill=TEXT, anchor="w", tags="dynamic")
            y += 26

    def _draw_evidence_overlay(self, c, ox, y1, cw, ch):
        case = self._court_case
        ex1, ey1, ex2, ey2 = ox + 30, y1 + 30, cw - 30, ch - 15
        c.create_rectangle(ex1, ey1, ex2, ey2, fill=ABYSS, outline=GOLD, width=1, tags="dynamic")
        c.create_text(ex1 + 16, ey1 + 16, text="EXHIBITS", font=(FONT_DISPLAY, 10, "bold"), fill=GOLD, anchor="w", tags="dynamic")
        self._draw_hline(c, ex1 + 16, ex2 - 16, ey1 + 30)
        y = ey1 + 44
        for ex in case.get("exhibits", []):
            c.create_text(ex1 + 16, y, text=ex["label"], font=(FONT_DISPLAY, 8, "bold"), fill=CYAN, anchor="w", tags="dynamic")
            used = self._draw_wrapped_text(c, ex1 + 16, y + 16, ex2 - 16, ex["description"], font=(FONT_DISPLAY, 8), fill=TEXT)
            y += 16 + used + 16

    def _draw_court_verdict(self, c, ox, y1, cw, ch):
        v = self._court_verdict
        case = self._court_case
        if not v:
            return
        cx = ox + (cw - ox) // 2
        # this screen has no courtroom scene to push/shake, so the verdict's
        # "key beat" is a brief settle-in on the winner line instead — a drop
        # + glow that eases out, echoing the gavel-final feeling.
        ENTER_FRAMES = 16
        self._court_verdict_enter = min(ENTER_FRAMES, self._court_verdict_enter + 1)
        if self._court_verdict_bot_won is not None:
            self._fire_catchphrase("verdict_win" if self._court_verdict_bot_won else "verdict_loss")
        eb = self._ease_out_back(self._court_verdict_enter / ENTER_FRAMES)
        drop = max(0.0, 1.0 - eb) * 14
        if self._court_verdict_enter < ENTER_FRAMES:
            self._draw_glow_ring(c, cx, y1 + 40, 4, GOLD, layers=4, spread=20 * (1.0 - self._court_verdict_enter / ENTER_FRAMES))
        c.create_text(cx, y1 + 18, text="VERDICT", font=(FONT_DISPLAY, 13, "bold"), fill=CYAN, tags="dynamic")
        c.create_text(cx, y1 + 40 - drop, text=v.get("winner", "Unknown"), font=(FONT_DISPLAY, 11, "bold"), fill=GOLD, tags="dynamic")
        self._draw_hline(c, ox + 20, cw - 20, y1 + 56)
        if self._court_catchphrase_frames > 0:
            bot = BOT_CHARACTERS.get(self._court_character, BOT_CHARACTERS[DEFAULT_BOT])
            self._draw_catchphrase_banner(c, cx, y1 + 84, self._court_catchphrase, bot["accent"])
            self._court_catchphrase_frames -= 1

        btn_h = 50
        content_y1, content_y2 = y1 + 64, ch - btn_h

        def estimate_height(text, size=8):
            avail_px = max(100, cw - 20 - (ox + 20))
            font = (FONT_DISPLAY, size)
            n_lines = max(1, len(self._wrap_lines(text, font, avail_px))) if text else 1
            return n_lines * self._line_height(font)

        reasoning_h = estimate_height(v.get("reasoning", ""))
        rubric = v.get("rubric") or {}
        rubric_h = (len(rubric) * 22 + 20) if rubric else 0
        feedback_h = estimate_height(v.get("feedback", ""))
        total_h = reasoning_h + 16 + rubric_h + 36 + feedback_h
        visible_h = content_y2 - content_y1
        max_scroll = max(0, total_h - visible_h)
        self._court_scroll = max(0, min(self._court_scroll, max_scroll))

        y = content_y1 - (max_scroll - self._court_scroll) if max_scroll else content_y1
        if content_y1 - 200 <= y <= content_y2 + 200:
            self._draw_wrapped_text(c, ox + 20, y, cw - 20, v.get("reasoning", ""), font=(FONT_DISPLAY, 8), fill=TEXT)
        y += reasoning_h + 16

        if rubric:
            if content_y1 - 200 <= y <= content_y2 + 200:
                c.create_text(ox + 20, y, text="RUBRIC", font=(FONT_DISPLAY, 7, "bold"), fill=TDIM, anchor="w", tags="dynamic")
            y += 16
            for label, score in rubric.items():
                if content_y1 - 30 <= y <= content_y2 + 30:
                    nice_label = label.replace("_", " ").upper()
                    c.create_text(ox + 20, y, text=nice_label, font=(FONT_DISPLAY, 7), fill=TEXT, anchor="w", tags="dynamic")
                    bar_x1 = ox + 160
                    bar_w = (cw - 20 - bar_x1 - 40)
                    col = GREEN if score >= 7 else (ORANGE if score >= 4 else RED)
                    c.create_rectangle(bar_x1, y - 5, bar_x1 + bar_w, y + 5, outline=CORE_DIM, width=1, tags="dynamic")
                    c.create_rectangle(bar_x1, y - 5, bar_x1 + bar_w * (score / 10), y + 5, fill=col, outline="", tags="dynamic")
                    c.create_text(cw - 20, y, text=f"{score}/10", font=(FONT_DATA, 7, "bold"), fill=col, anchor="e", tags="dynamic")
                y += 22
            y += 4

        if content_y1 - 200 <= y <= content_y2 + 200:
            c.create_text(ox + 20, y, text="FEEDBACK ON YOUR ARGUMENT", font=(FONT_DISPLAY, 7, "bold"), fill=TDIM, anchor="w", tags="dynamic")
            self._draw_wrapped_text(c, ox + 20, y + 16, cw - 20, v.get("feedback", ""), font=(FONT_DISPLAY, 8), fill=TEXT)

        if max_scroll > 0:
            c.create_text(cw - 20, content_y1 - 2, text="scroll for more ▾" if self._court_scroll < max_scroll else "▴ scroll up",
                          font=(FONT_DISPLAY, 6), fill=TDIM, anchor="e", tags="dynamic")

        bw, bh = 150, 32
        bx1 = cx - bw - 10
        self._court_phase_hit = (bx1, ch - 40, bx1 + bw, ch - 40 + bh)
        pts = self._rounded_rect_points(bx1, ch - 40, bx1 + bw, ch - 40 + bh, 8)
        c.create_polygon(pts, smooth=True, outline=CORE, fill="#0f2b42", tags="dynamic")
        c.create_text(bx1 + bw // 2, ch - 40 + bh // 2, text="NEW SESSION", font=(FONT_DISPLAY, 8, "bold"), fill=HOT, tags="dynamic")

        ebw, ebh = 150, 32
        ebx1 = cx + 10
        self._court_export_hit = (ebx1, ch - 40, ebx1 + ebw, ch - 40 + ebh)
        epts = self._rounded_rect_points(ebx1, ch - 40, ebx1 + ebw, ch - 40 + ebh, 8)
        c.create_polygon(epts, smooth=True, outline=GOLD, fill="#2a2410", tags="dynamic")
        export_label = self._court_export_msg or "💾 EXPORT TRANSCRIPT"
        c.create_text(ebx1 + ebw // 2, ch - 40 + ebh // 2, text=export_label, font=(FONT_DISPLAY, 8, "bold"), fill=GOLD, tags="dynamic")

    # ── shared text / drawing helpers ──
    def _get_tkfont(self, font_spec):
        key = tuple(font_spec)
        f = self._tkfont_cache.get(key)
        if f is None:
            f = tkfont.Font(font=font_spec)
            self._tkfont_cache[key] = f
        return f

    def _line_height(self, font_spec):
        return self._get_tkfont(font_spec).metrics("linespace")

    def _wrap_lines(self, text, font, avail_px):
        tkf = self._get_tkfont(font)
        words = text.split(); line = ""; lines = []
        for w in words:
            test = line + " " + w if line else w
            if tkf.measure(test) > avail_px and line: lines.append(line); line = w
            else: line = test
        if line: lines.append(line)
        return lines

    def _draw_wrapped_text(self, c, x1, y1, x2, text, font, fill):
        avail_px = max(100, x2 - x1)
        lines = self._wrap_lines(text, font, avail_px)
        line_h = self._line_height(font)
        for i, ln in enumerate(lines):
            c.create_text(x1, y1 + i * line_h, text=ln, font=font, fill=fill, anchor="w", tags="dynamic")
        return len(lines) * line_h

    def _draw_clickable_text(self, c, x1, y1, x2, text, font, fill, dry_run=False):
        size = font[1]
        tkf = self._get_tkfont(font)
        space_w = tkf.measure(" ")

        word_to_sentence = []
        if not dry_run:
            sentences = court_split_sentences(text)
            for s in sentences:
                n = len(s.split())
                word_to_sentence.extend([s] * n)
        words = text.split()
        if not dry_run:
            while len(word_to_sentence) < len(words):
                word_to_sentence.append(text)

        line_h = tkf.metrics("linespace")
        x, y = x1, y1
        for i, w in enumerate(words):
            ww = tkf.measure(w)
            if x + ww > x2 and x > x1:
                x = x1
                y += line_h
            if not dry_run:
                c.create_text(x, y, text=w, font=font, fill=fill, anchor="w", tags="dynamic")
                clean_word = w.strip(".,;:!?\"'()[]")
                if clean_word:
                    self._court_word_hits.append((x, y - size / 2 - 2, x + ww, y + size / 2 + 2, clean_word, word_to_sentence[i]))
            x += ww + space_w
        return (y - y1) + line_h

    def _draw_message_log(self, c, x1, y1, x2, y2, messages, scroll_offset):
        block_gap = 6
        avail_px = max(150, (x2 - x1) - 75 - 20)
        body_font = (FONT_DISPLAY, 8)
        tkf = self._get_tkfont(body_font)
        line_h = tkf.metrics("linespace")
        blocks = []
        for sender, text in messages:
            words = text.split(); line = ""; lines = []
            for w in words:
                test = line + " " + w if line else w
                if tkf.measure(test) > avail_px and line: lines.append(line); line = w
                else: line = test
            if line: lines.append(line)
            if not lines: lines = [""]
            h = max(line_h * len(lines), line_h) + block_gap
            blocks.append((sender, lines, h))

        total_h = sum(b[2] for b in blocks) + 18
        visible_h = y2 - y1
        max_scroll = max(0, total_h - visible_h)
        scroll_offset = max(0, min(scroll_offset, max_scroll))

        y = y1 + 18 - (max_scroll - scroll_offset)
        clip_top, clip_bottom = y1 + 2, y2 - 2
        for sender, lines, h in blocks:
            block_top = y
            block_bottom = y + h
            if block_bottom < clip_top or block_top > clip_bottom:
                y += h
                continue
            bot = BOT_CHARACTERS.get(self._court_character, BOT_CHARACTERS[DEFAULT_BOT])
            if sender == bot["label"]:
                if clip_top <= y <= clip_bottom:
                    c.create_text(x1 + 12, y, text=f"{bot['label']} ›", font=(FONT_DISPLAY, 8, "bold"), fill=bot["accent"], anchor="w", tags="dynamic")
                col = TEXT
            else:
                if clip_top <= y <= clip_bottom:
                    c.create_text(x1 + 12, y, text="YOU ›", font=(FONT_DISPLAY, 8, "bold"), fill=GREEN, anchor="w", tags="dynamic")
                col = "#c8ffdc"
            for i, ln in enumerate(lines):
                ly = y + i * line_h
                if clip_top <= ly <= clip_bottom:
                    c.create_text(x1 + 75, ly, text=ln, font=(FONT_DISPLAY, 8), fill=col, anchor="w", tags="dynamic")
            y += h

        if max_scroll > 0:
            bar_h = max(20, visible_h * (visible_h / total_h))
            frac_from_bottom = scroll_offset / max_scroll
            bar_y = y1 + (visible_h - bar_h) * (1 - frac_from_bottom)
            c.create_rectangle(x2 - 6, bar_y, x2 - 3, bar_y + bar_h, fill=CMID, outline="", tags="dynamic")
        if scroll_offset > 5:
            c.create_text(x2 - 30, y1 + 12, text="▼ NEW", font=(FONT_DISPLAY, 6, "bold"), fill=ORANGE, tags="dynamic")
        return scroll_offset

    def _rounded_rect_points(self, x1, y1, x2, y2, r):
        r = min(r, (x2 - x1) / 2, (y2 - y1) / 2)
        return [
            x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1,
        ]

    def _draw_glow_ring(self, c, cx, cy, r, core_color, layers=7, spread=16, base=None):
        base = base or ABYSS
        for i in range(layers, 0, -1):
            t = i / layers
            col = _blend_hex(core_color, base, t * 0.9)
            rad = r + i * spread / layers
            c.create_oval(cx - rad, cy - rad, cx + rad, cy + rad, outline=col, width=2, tags="dynamic")

    def _draw_hline(self, c, x1, x2, y):
        c.create_line(x1, y, x2, y, fill=GLASS_EDGE, width=1, tags="dynamic")
        for col, rad in [("#123552", 5), (CORE, 2.5)]:
            c.create_oval(x1 - rad, y - rad, x1 + rad, y + rad, fill=col, outline="", tags="dynamic")
            c.create_oval(x2 - rad, y - rad, x2 + rad, y + rad, fill=col, outline="", tags="dynamic")


# ── Startup: get an API key, then run ────────────────────────────────────────
def _prompt_for_api_key():
    root = tk.Tk()
    root.withdraw()
    key = simpledialog.askstring(
        "Anthropic API Key",
        "Enter your Anthropic API key (from console.anthropic.com):",
        show="*",
    )
    root.destroy()
    return (key or "").strip()


def main():
    cfg = _load_config()
    api_key = cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        api_key = _prompt_for_api_key()
        if api_key:
            cfg["api_key"] = api_key
            _save_config(cfg)
    if not api_key:
        try:
            tk.Tk().withdraw()
            messagebox.showerror("Moot Court Trainer", "No Anthropic API key provided. The app can't run without one.")
        except Exception:
            print("No Anthropic API key provided. Exiting.")
        return
    init_claude(api_key)
    app = MootCourtApp()
    app.run()


if __name__ == "__main__":
    main()
