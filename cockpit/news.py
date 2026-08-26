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
#: When only one distinctive entity is shared, the headlines must also have
#: this much vocabulary in common. Rarity alone over-merges: in a corpus where
#: "Trump" is under the rarity ceiling, an EPA lawsuit and a South Korea
#: defence story become one cluster, and then the outlet links under a line
#: point at whatever each outlet contributed to the pile.
WEAK_MATCH_OVERLAP = 0.20
#: …or this many distinctive tokens in common. Two desks rarely phrase an event
#: alike — "Fed holds rates steady as inflation cools" against "Powell signals
#: patience as Fed leaves rates unchanged" shares little vocabulary and one
#: obvious subject. Names, tickers and numbers carry the identity of a story;
#: the connective tissue around them does not.
SHARED_ENTITIES = 2


@dataclass
class Item:
    headline: str
    url: str
    source: str
    published: datetime

    def tokens(self) -> set[str]:
        words = re.findall(r"[a-z0-9']+", self.headline.lower())
        return {w for w in words if w not in STOPWORDS and len(w) > 2}

    def entities(self) -> set[str]:
        """The tokens that identify *which* story this is: capitalised words,
        tickers and figures. Lowercased for comparison, but only collected
        where the outlet capitalised them or they carry digits."""
        found = set()
        for word in re.findall(r"[A-Za-z][A-Za-z'&.]+|\$?[0-9][0-9,.%]*", self.headline):
            bare = word.strip(".,'&").lower()
            if len(bare) < 3 or bare in STOPWORDS:
                continue
            if word[0].isupper() or any(ch.isdigit() for ch in word):
                found.add(bare)
        return found


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


def _distinctive(items: list[Item]) -> set[str]:
    """Entities rare enough that sharing one implies the same story.

    Two headlines both naming "Vipshop" are covering Vipshop. Two both naming
    "Fed" are not necessarily covering the same thing — half the business wire
    mentions the Fed on any given day. Rarity across the day's own corpus is
    what separates the two, and it needs no list to maintain.
    """
    seen: dict[str, int] = {}
    for item in items:
        for entity in item.entities():
            seen[entity] = seen.get(entity, 0) + 1
    # Tighter than it looks: a name in a twentieth of the day's headlines is
    # still a common name, and treating it as identifying merges the unrelated.
    ceiling = max(2, len(items) // 20)
    return {entity for entity, count in seen.items() if count <= ceiling}


def group(items: list[Item]) -> list[Story]:
    """Cluster items covering the same event, newest first within each."""
    distinctive = _distinctive(items)
    stories: list[Story] = []
    for item in sorted(items, key=lambda i: i.published, reverse=True):
        tokens = item.tokens()
        if not tokens:
            continue
        entities = item.entities()
        for story in stories:
            lead = story.lead
            overlap = len(tokens & lead.tokens()) / max(1, len(tokens | lead.tokens()))
            shared = entities & lead.entities()
            if (overlap >= SAME_STORY
                    or len(shared) >= SHARED_ENTITIES
                    or (shared & distinctive and overlap >= WEAK_MATCH_OVERLAP)):
                story.items.append(item)
                break
        else:
            stories.append(Story([item]))
    return stories


def select(stories: list[Story], count: int, outlets: int,
           min_sources: int = 1) -> list[Story]:
    """Top stories, without letting one outlet take the whole strip.

    Feeds differ enormously in how much they publish: a curated top-stories
    feed posts a few times a day, a market-notes firehose posts every few
    minutes. Ranked on recency alone the firehose wins every slot, and the
    first live briefing came back four-fifths one outlet — an earnings preview
    and an FDA date, dressed as the day's top business news.

    So each outlet gets a share of the slots. That is what makes the strip a
    consolidation rather than one desk's feed with a border around it.
    """
    # A topic several outlets independently ran is the only evidence of
    # importance available without a model reading the stories. Where enough
    # of those exist they are the whole strip, and the per-outlet cap is not
    # needed: corroboration already guarantees the spread.
    corroborated = [s for s in stories if len(s.sources) >= min_sources]
    if len(corroborated) >= max(1, min(count, 3)):
        return corroborated[:count]

    cap = max(1, -(-count // max(1, outlets)))
    chosen: list[Story] = []
    used: dict[str, int] = {}
    for story in stories:
        lead = story.lead.source
        if used.get(lead, 0) >= cap:
            continue
        chosen.append(story)
        used[lead] = used.get(lead, 0) + 1
        if len(chosen) == count:
            return chosen

    # Too few outlets answered to fill the strip under the cap — better a
    # thinner spread than a short briefing.
    taken = {id(story) for story in chosen}
    for story in stories:
        if len(chosen) == count:
            break
        if id(story) not in taken:
            chosen.append(story)
    return chosen


def briefing(feeds: dict[str, str], *, count: int = 5, max_age_hours: int = 24,
             min_sources: int = 2) -> dict:
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

    ranked = sorted(group(items), key=Story.rank, reverse=True)
    stories = select(ranked, count, len(answered), min_sources=min_sources)
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources_read": answered,
        "feeds_configured": len(feeds),
        "stories": [
            {
                "headline": story.lead.headline,
                "url": story.lead.url,
                "sources": story.sources,
                "corroborated": len(story.sources) >= min_sources,
                # Each outlet's own wording *and* its link: the synthesised
                # line names the outlets it was compressed from, and naming
                # them is only useful if a reader can open what they ran.
                "variants": [
                    {"source": item.source, "headline": item.headline, "url": item.url}
                    for item in sorted(story.items, key=lambda i: i.source)
                ][:8],
                "published": story.newest.published.isoformat(timespec="seconds"),
            }
            for story in stories
        ],
    }
