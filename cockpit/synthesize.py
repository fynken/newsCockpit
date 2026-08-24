"""Compound the clustered topics into a few written lines.

This is the one part of the board a model writes, and it is deliberately the
only part: everything else is a figure someone published or arithmetic over
figures someone published. So the guardrails here are not decoration.

A line is kept only if it cites topics that exist in the input and outlets that
actually ran them. Anything the model returns that fails those checks is
discarded and the mechanical strip — real headlines, verbatim — is what ships.
The board would rather say less than say something nobody wrote.

Requires the anthropic SDK and ANTHROPIC_API_KEY. Without either, this is a
no-op: `python3 -m cockpit synthesize` exits cleanly and the briefing keeps the
published headlines it already has.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

#: The skill's default. Not downgraded for cost without the owner asking —
#: a cheaper model is a decision for whoever pays the bill, and the run is a
#: few hundred tokens either way.
MODEL = "claude-opus-5"
MAX_TOKENS = 1024

SYSTEM = """You compress a business-news wire into a short briefing for a \
finance dashboard.

You will be given today's topics. Each topic is a cluster of headlines that \
several newsrooms published about the same event, with the outlets that ran it.

Write 3 to 5 lines. Each line covers one topic, or two topics where they are \
plainly the same story. Choose the topics that matter most to someone watching \
rates, credit, equities and the AI capital-expenditure cycle; a topic many \
outlets ran is usually more important than one only a single outlet carried.

Rules that matter more than style:
- Every fact in a line must appear in the headlines given to you. Do not add \
context, causes, figures, or consequences from your own knowledge, however \
confident you are. You are compressing, not reporting.
- If the headlines disagree or are vague, be vague in the same way.
- No hedging filler, no "amid", no "as investors weigh". One clause, present \
tense, the concrete thing that happened.
- Under 110 characters per line.
- Cite the topic ids you used."""

SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "topic_ids": {"type": "array", "items": {"type": "integer"}},
                },
                "required": ["text", "topic_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["lines"],
    "additionalProperties": False,
}


def prompt_for(stories: list[dict]) -> str:
    """The topics, as the model sees them."""
    blocks = []
    for index, story in enumerate(stories):
        outlets = ", ".join(story.get("sources", []))
        variants = story.get("variants") or [
            {"source": (story.get("sources") or [""])[0],
             "headline": story.get("headline", "")}
        ]
        lines = "\n".join(
            f"    - {v['source']}: {v['headline']}" for v in variants
        )
        blocks.append(f"[{index}] carried by {len(story.get('sources', []))} "
                      f"outlets ({outlets}):\n{lines}")
    return "Today's topics:\n\n" + "\n\n".join(blocks)


def validate(lines: list[dict], stories: list[dict]) -> list[dict]:
    """Keep only lines grounded in topics that were actually supplied.

    A line citing a topic id that does not exist is a line about something the
    wire did not carry. There is no way to check the prose itself from here, so
    the citation is the check that exists — and a line that fails it goes.
    """
    kept = []
    for line in lines:
        text = (line.get("text") or "").strip()
        ids = [i for i in line.get("topic_ids", []) if isinstance(i, int)]
        if not text or not ids:
            continue
        if any(i < 0 or i >= len(stories) for i in ids):
            continue
        sources: list[str] = []
        for i in ids:
            for name in stories[i].get("sources", []):
                if name not in sources:
                    sources.append(name)
        kept.append({"text": text, "topic_ids": ids, "sources": sorted(sources)})
    return kept


def synthesize(briefing: dict) -> dict | None:
    """Written lines for this briefing, or None if it could not be done.

    Every failure path returns None rather than raising: a missing key, an
    unavailable SDK, a refusal, a bad response. The caller keeps the headlines
    it already had, which are real either way.
    """
    stories = (briefing or {}).get("stories") or []
    if not stories or not os.environ.get("ANTHROPIC_API_KEY"):
        return None

    try:
        import anthropic
    except ImportError:
        return None

    client = anthropic.Anthropic()
    try:
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM,
            output_config={
                # A short compression job; low effort is the point, and it is
                # the difference between pennies and cents a month.
                "effort": "low",
                "format": {"type": "json_schema", "schema": SCHEMA},
            },
            messages=[{"role": "user", "content": prompt_for(stories)}],
        )
    except Exception as exc:  # noqa: BLE001 — any failure keeps the headlines
        print(f"  synthesis: {type(exc).__name__}: {exc}")
        return None

    if getattr(response, "stop_reason", None) == "refusal":
        print("  synthesis: the request was declined; keeping the headlines")
        return None

    try:
        text = next(b.text for b in response.content if b.type == "text")
        lines = validate(json.loads(text).get("lines", []), stories)
    except (StopIteration, json.JSONDecodeError, AttributeError, TypeError) as exc:
        print(f"  synthesis: unusable response ({type(exc).__name__}); keeping the headlines")
        return None

    if not lines:
        print("  synthesis: nothing survived grounding checks; keeping the headlines")
        return None

    served = getattr(response, "model", MODEL)
    print(f"  synthesis: {len(lines)} lines written by {served}")
    return {
        "lines": lines,
        "model": served,
        "written_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
