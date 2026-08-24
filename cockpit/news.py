"""The briefing: business headlines, consolidated across outlets.

"Consolidated" here means merged, deduplicated and ranked — never rewritten.
Every bullet is a headline an outlet actually published, carried verbatim,
with its source and timestamp attached and a link to the original. Nothing in
this module paraphrases, summarises or infers, because there is no model in
the build: it runs on a GitHub runner with the standard library. A bullet you
cannot click through to is a bullet this board should not be showing.

The ranking is the only judgement it makes, and it is a mechanical one: a
story two outlets both ran outranks one that only appeared in a single feed.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ElementTree
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime

from .providers import USER_AGENT

TIMEOUT = 15

#: Words that carry no signal when deciding whether two outlets are covering
#: the same story. Deliberately short — an aggressive list starts merging
#: stories that merely share a subject.
STOPWORDS = frozenset("""
a an and are as at be but by for from has have how in into is it its of on or
that the to was were what when where which who will with why says say said
after before over under new more most less than then this these those it's
""".split())

#: How much of two headlines' vocabulary must overlap to call them one story.
SAME_STORY = 0.45


@dataclass
class Item:
    headline: str
    url: str
    source: str
    published: datetime

    def tokens(self) -> set[str]:
        words = re.findall(r"[a-z0-9']+", self.headline.lower())
        return {w for w in words if w not in STOPWORDS and len(w) > 2}


@dataclass
class Story:
    """One event, as covered by one or more outlets."""

    items: list[Item] = field(default_factory=list)

    @property
    def sources(self) -> list[str]:
        return sorted({item.source for item in self.items})

    @property
    def newest(self) -> Item:
        return max(self.items, key=lambda i: i.published)

    @property
    def lead(self) -> Item:
        """The headline to show: the shortest, which is usually the least
        editorialised of the versions and the one that reads as a fact."""
        return min(self.items, key=lambda i: (len(i.headline), i.headline))

    def rank(self) -> tuple:
        # Corroboration first, recency second. A story two desks independently
        # decided to run is more likely to matter than one outlet's angle.
        return (len(self.sources), self.newest.published)


def _text(node, *names: str) -> str:
    for name in names:
        found = node.find(name)
        if found is not None and (found.text or "").strip():
            return " ".join((found.text or "").split())
    return ""


def parse_feed(body: bytes, source: str) -> list[Item]:
    """RSS or Atom into items. A feed that will not parse yields nothing."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return []

    items: list[Item] = []
    entries = root.iter("item")
    for node in entries:
        headline = _text(node, "title")
        url = _text(node, "link", "guid")
        stamp = _text(node, "pubDate", "date")
        if not headline or not url:
            continue
        try:
            published = parsedate_to_datetime(stamp)
        except (TypeError, ValueError):
            continue
        if published.tzinfo is None:
            published = published.replace(tzinfo=timezone.utc)
        items.append(Item(headline, url, source, published.astimezone(timezone.utc)))
    return items


def fetch_feed(url: str, source: str) -> list[Item]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return parse_feed(response.read(), source)
    except (urllib.error.URLError, OSError, ValueError):
        # One dead feed must not cost the whole briefing; the others carry it.
        return []


def group(items: list[Item]) -> list[Story]:
    """Cluster items covering the same event, newest first within each."""
    stories: list[Story] = []
    for item in sorted(items, key=lambda i: i.published, reverse=True):
        tokens = item.tokens()
        if not tokens:
            continue
        for story in stories:
            other = story.lead.tokens()
            overlap = len(tokens & other) / max(1, len(tokens | other))
            if overlap >= SAME_STORY:
                story.items.append(item)
                break
        else:
            stories.append(Story([item]))
    return stories


def briefing(feeds: dict[str, str], *, count: int = 5, max_age_hours: int = 24) -> dict:
    """The top stories across the configured outlets.

    Returns the shape the renderer and the snapshot both use. `sources_read`
    records which outlets actually answered, so a briefing built from two feeds
    instead of six is visible rather than silently thinner.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    items, answered = [], []
    for source, url in feeds.items():
        fetched = [item for item in fetch_feed(url, source) if item.published >= cutoff]
        if fetched:
            answered.append(source)
            items.extend(fetched)

    stories = sorted(group(items), key=Story.rank, reverse=True)[:count]
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources_read": answered,
        "feeds_configured": len(feeds),
        "stories": [
            {
                "headline": story.lead.headline,
                "url": story.lead.url,
                "sources": story.sources,
                "published": story.newest.published.isoformat(timespec="seconds"),
            }
            for story in stories
        ],
    }
