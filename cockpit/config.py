"""Loading and validating sources.toml."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
DIST_DIR = ROOT / "dist"
SOURCES_FILE = ROOT / "sources.toml"
SNAPSHOT_FILE = DATA_DIR / "snapshot.json"
HISTORY_FILE = DATA_DIR / "history.csv"
INBOX_FILE = DATA_DIR / "agent-inbox.json"

GOOD_WHEN = ("up", "down", "neutral")
TRANSFORMS = ("", "yoy")


@dataclass
class Tile:
    key: str
    label: str
    group: str
    provider: str
    symbol: str = ""
    expr: str = ""
    url: str = ""
    unit: str = ""
    prefix: str = ""
    decimals: int = 2
    featured: bool = False
    good_when: str = "up"
    note: str = ""
    #: A second provider to try when the first cannot be reached. Which hosts
    #: answer depends entirely on where the build runs — CNBC answers GitHub's
    #: runners and refuses this sandbox, Yahoo does the opposite — so a tile
    #: names both and the fetcher takes whichever one replies.
    alt_provider: str = ""
    alt_symbol: str = ""
    #: "yoy" turns a level series into its percent change against the
    #: observation a year earlier — how an inflation rate is quoted. Only the
    #: fred provider reads it.
    transform: str = ""
    #: Multiplier applied to a level, for series published in awkward units
    #: (FRED reports reserves in millions of dollars; 1e-6 shows trillions).
    scale: float = 1.0

    def attempts(self) -> list["Tile"]:
        """This tile, then its fallback: the providers to try, in order."""
        from dataclasses import replace

        if not self.alt_provider:
            return [self]
        return [self, replace(self, provider=self.alt_provider, symbol=self.alt_symbol)]

    def quote_url(self) -> str:
        """Where a human goes to check this number by hand."""
        if self.url:
            return self.url
        if self.provider == "cnbc" and self.symbol:
            return f"https://www.cnbc.com/quotes/{self.symbol}/"
        if self.provider == "yahoo" and self.symbol:
            return f"https://finance.yahoo.com/quote/{self.symbol}"
        if self.provider == "coingecko" and self.symbol:
            return f"https://www.coingecko.com/en/coins/{self.symbol}"
        if self.provider == "sec" and self.symbol:
            cik = self.symbol.split("/")[0].split("+")[0]
            return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}"
        if self.provider == "fred" and self.symbol:
            return f"https://fred.stlouisfed.org/series/{self.symbol}"
        if self.provider == "stooq" and self.symbol:
            return f"https://stooq.com/q/?s={self.symbol}"
        return ""


@dataclass
class Briefing:
    """Which outlets the headline strip reads, and how much of it to show."""

    feeds: dict[str, str] = field(default_factory=dict)
    count: int = 5
    max_age_hours: int = 24


@dataclass
class Cockpit:
    title: str = "Finance Cockpit"
    tagline: str = ""
    timezone: str = "UTC"
    stale_after_minutes: int = 90
    briefing: Briefing = field(default_factory=Briefing)
    tiles: list[Tile] = field(default_factory=list)

    def by_key(self, key: str) -> Tile | None:
        return next((t for t in self.tiles if t.key == key), None)

    def groups(self) -> list[str]:
        """Group names in the order they first appear in sources.toml."""
        seen: list[str] = []
        for tile in self.tiles:
            if tile.group not in seen:
                seen.append(tile.group)
        return seen


class ConfigError(ValueError):
    pass


def load(path: Path | None = None) -> Cockpit:
    path = path or SOURCES_FILE
    if not path.exists():
        raise ConfigError(f"No sources file at {path}")
    raw = tomllib.loads(path.read_text(encoding="utf-8"))

    meta = raw.get("cockpit", {})
    cockpit = Cockpit(
        title=meta.get("title", "Finance Cockpit"),
        tagline=meta.get("tagline", ""),
        timezone=meta.get("timezone", "UTC"),
        stale_after_minutes=int(meta.get("stale_after_minutes", 90)),
    )

    news = raw.get("briefing", {})
    feeds = news.get("feeds", {})
    if not isinstance(feeds, dict) or any(not isinstance(v, str) for v in feeds.values()):
        raise ConfigError("briefing.feeds must be a table of name = \"feed url\"")
    cockpit.briefing = Briefing(
        feeds=feeds,
        count=int(news.get("count", 5)),
        max_age_hours=int(news.get("max_age_hours", 24)),
    )

    known = {f for f in Tile.__dataclass_fields__}
    seen_keys: set[str] = set()
    for index, entry in enumerate(raw.get("tile", []), start=1):
        unknown = set(entry) - known
        if unknown:
            raise ConfigError(
                f"tile #{index}: unknown field(s) {sorted(unknown)}. "
                f"Allowed: {sorted(known)}"
            )
        for required in ("key", "label", "group", "provider"):
            if not entry.get(required):
                raise ConfigError(f"tile #{index}: missing required field '{required}'")
        key = entry["key"]
        if key in seen_keys:
            raise ConfigError(f"duplicate tile key '{key}'")
        seen_keys.add(key)

        good_when = entry.get("good_when", "up")
        if good_when not in GOOD_WHEN:
            raise ConfigError(
                f"tile '{key}': good_when must be one of {GOOD_WHEN}, got '{good_when}'"
            )
        if entry["provider"] == "derived" and not entry.get("expr"):
            raise ConfigError(f"tile '{key}': provider 'derived' requires an 'expr'")
        if entry["provider"] != "derived" and not entry.get("symbol"):
            raise ConfigError(f"tile '{key}': provider '{entry['provider']}' requires a 'symbol'")
        if bool(entry.get("alt_provider")) != bool(entry.get("alt_symbol")):
            raise ConfigError(
                f"tile '{key}': alt_provider and alt_symbol must be set together"
            )
        if entry.get("transform", "") not in TRANSFORMS:
            raise ConfigError(
                f"tile '{key}': transform must be one of {TRANSFORMS}, "
                f"got '{entry['transform']}'"
            )

        cockpit.tiles.append(Tile(**entry))

    if not cockpit.tiles:
        raise ConfigError("sources.toml defines no tiles")
    return cockpit
