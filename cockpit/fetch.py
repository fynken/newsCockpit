"""Fetch orchestration: turn sources.toml into data/snapshot.json.

Each tile is resolved through a three-step ladder, and the step that won is
recorded on the tile as its `origin`, so the rendered board can always say
where a number came from:

  live   — the provider answered over HTTP just now.
  manual — the provider was unreachable, but data/agent-inbox.json carries a
           recent reading for this tile (this is how the board stays current
           in a sandbox whose egress proxy blocks market data hosts).
  cache  — nothing fresh was available; the previous snapshot's value is
           carried forward and the tile is flagged stale on the board.
"""

from __future__ import annotations

import argparse
import ast
import csv
import json
import operator
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import config
from .providers import PROVIDERS, ProviderError, Quote

HISTORY_HEADER = ("captured_at", "key", "value", "as_of")
HISTORY_KEEP_PER_KEY = 400


# ── derived tiles ──────────────────────────────────────────────────────────

_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.Pow: operator.pow,
}


def evaluate(expr: str, values: dict[str, float]) -> float:
    """Evaluate an arithmetic expression over other tiles' values.

    Only numbers, tile keys and + - * / ** are permitted — this never runs
    arbitrary code from sources.toml.
    """

    def walk(node):
        if isinstance(node, ast.Expression):
            return walk(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in values:
                raise ProviderError(f"expression refers to unknown or unresolved tile '{node.id}'")
            return values[node.id]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
            operand = walk(node.operand)
            return operand if isinstance(node.op, ast.UAdd) else -operand
        if isinstance(node, ast.BinOp) and type(node.op) in _BINOPS:
            return _BINOPS[type(node.op)](walk(node.left), walk(node.right))
        raise ProviderError(f"unsupported syntax in expression: {ast.dump(node)[:60]}")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ProviderError(f"could not parse expression '{expr}': {exc}") from exc
    return walk(tree)


# ── inbox / snapshot / history i/o ─────────────────────────────────────────


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def load_inbox() -> dict:
    """Readings supplied out of band (by Claude, or by hand) when HTTP is blocked."""
    inbox = _read_json(config.INBOX_FILE, {})
    return inbox.get("quotes", {}) if isinstance(inbox, dict) else {}


def load_previous() -> dict:
    snapshot = _read_json(config.SNAPSHOT_FILE, {})
    return {tile["key"]: tile for tile in snapshot.get("tiles", [])}


def append_history(rows: list[tuple[str, str, float, str]]) -> None:
    """Append readings, skipping any whose market timestamp we already hold.

    Rebuilding twice off the same data is one observation, not two — without
    this guard a repeated build would draw a trend line out of a single reading.
    """
    seen = last_observations()
    rows = [row for row in rows if not (row[3] and seen.get(row[1]) == row[3])]
    if not rows:
        return
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not config.HISTORY_FILE.exists()
    with config.HISTORY_FILE.open("a", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        if is_new:
            writer.writerow(HISTORY_HEADER)
        writer.writerows(rows)


def last_observations() -> dict[str, str]:
    """{tile_key: the as_of of the most recent recorded reading}."""
    latest: dict[str, str] = {}
    if not config.HISTORY_FILE.exists():
        return latest
    with config.HISTORY_FILE.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if row.get("as_of"):
                latest[row["key"]] = row["as_of"]
    return latest


def load_history() -> dict[str, list[list]]:
    """{tile_key: [[iso, value], …]} oldest first, trimmed per key."""
    series: dict[str, list[list]] = {}
    if not config.HISTORY_FILE.exists():
        return series
    with config.HISTORY_FILE.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            try:
                series.setdefault(row["key"], []).append([row["captured_at"], float(row["value"])])
            except (KeyError, TypeError, ValueError):
                continue
    return {key: points[-HISTORY_KEEP_PER_KEY:] for key, points in series.items()}


# ── resolution ─────────────────────────────────────────────────────────────


def _from_inbox(entry: dict) -> Quote:
    quote = Quote(
        value=entry.get("value"),
        change=entry.get("change"),
        change_pct=entry.get("change_pct"),
        prev_close=entry.get("prev_close"),
        day_open=entry.get("open"),
        day_high=entry.get("day_high"),
        day_low=entry.get("day_low"),
        year_high=entry.get("year_high"),
        year_low=entry.get("year_low"),
        as_of=entry.get("as_of"),
        market_status=entry.get("market_status", ""),
        instrument_name=entry.get("instrument_name", ""),
    )
    if quote.value is None:
        raise ProviderError("inbox entry has no 'value'")
    return quote.derive_missing()


def resolve(tile, *, offline: bool, inbox: dict, previous: dict) -> dict:
    """Run one tile down the ladder and return its snapshot record."""
    record = {
        "key": tile.key,
        "label": tile.label,
        "group": tile.group,
        "provider": tile.provider,
        "symbol": tile.symbol,
        "expr": tile.expr,
        "url": tile.quote_url(),
        "unit": tile.unit,
        "prefix": tile.prefix,
        "decimals": tile.decimals,
        "featured": tile.featured,
        "good_when": tile.good_when,
        "note": tile.note,
        "origin": "missing",
        "error": None,
        "source_note": "",
        "series": [],
    }

    attempts: list[str] = []
    quote: Quote | None = None

    if not offline:
        for candidate in tile.attempts():
            if candidate.provider not in PROVIDERS:
                continue
            try:
                quote = PROVIDERS[candidate.provider](candidate)
            except ProviderError as exc:
                attempts.append(f"{candidate.provider}: {exc}")
                continue
            # Say which provider actually answered, not which one was asked
            # first — the symbol and the "check by hand" link must match the
            # number on the tile.
            record.update({"provider": candidate.provider, "symbol": candidate.symbol,
                           "url": candidate.quote_url(), "origin": "live",
                           "fetched_at": _now()})
            break

    if quote is None and tile.key in inbox:
        try:
            quote = _from_inbox(inbox[tile.key])
            record["origin"] = "manual"
            record["fetched_at"] = inbox[tile.key].get("captured_at") or _now()
            record["source_note"] = inbox[tile.key].get("note", "")
            if inbox[tile.key].get("source_url"):
                record["url"] = inbox[tile.key]["source_url"]
        except ProviderError as exc:
            attempts.append(f"inbox: {exc}")

    if quote is None and tile.key in previous and previous[tile.key].get("value") is not None:
        carried = previous[tile.key]
        record.update(
            {key: carried.get(key) for key in
             ("value", "change", "change_pct", "prev_close", "day_open", "day_high",
              "day_low", "year_high", "year_low", "as_of", "market_status",
              "instrument_name")}
        )
        record["origin"] = "cache"
        record["fetched_at"] = carried.get("fetched_at")
        record["error"] = "; ".join(attempts) or "no fresh source"
        return record

    if quote is None:
        record["value"] = None
        record["error"] = "; ".join(attempts) or "no source produced a value"
        record["fetched_at"] = None
        return record

    payload = asdict(quote)
    record["series"] = payload.pop("series", [])
    record["field_map"] = payload.pop("field_map", {})
    record.update(payload)
    if attempts:
        record["error"] = "; ".join(attempts)
    return record


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _mark_staleness(records: list[dict], stale_after_minutes: int) -> None:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=stale_after_minutes)
    for record in records:
        stamp = record.get("fetched_at")
        record["stale"] = True
        if not stamp:
            continue
        try:
            record["stale"] = datetime.fromisoformat(stamp) < cutoff
        except ValueError:
            pass


def run(*, offline: bool = False, only: list[str] | None = None) -> dict:
    cockpit = config.load()
    inbox = load_inbox()
    previous = load_previous()

    tiles = [t for t in cockpit.tiles if not only or t.key in only]
    plain = [t for t in tiles if t.provider != "derived"]
    derived = [t for t in tiles if t.provider == "derived"]

    records: dict[str, dict] = {}
    for tile in plain:
        records[tile.key] = resolve(tile, offline=offline, inbox=inbox, previous=previous)
        state = records[tile.key]["origin"]
        value = records[tile.key].get("value")
        print(f"  {state:<7} {tile.key:<14} {value if value is not None else '—'}")

    resolved = {key: r["value"] for key, r in records.items() if r.get("value") is not None}
    weakest = {"live": 0, "manual": 1, "cache": 2, "missing": 3}
    for tile in derived:
        record = resolve(tile, offline=True, inbox={}, previous={})
        try:
            record["value"] = evaluate(tile.expr, resolved)
            inputs = [n.id for n in ast.walk(ast.parse(tile.expr, mode="eval"))
                      if isinstance(n, ast.Name)]
            record["origin"] = max(
                (records[k]["origin"] for k in inputs if k in records),
                key=lambda o: weakest.get(o, 3), default="missing",
            )
            record["fetched_at"] = min(
                (records[k].get("fetched_at") for k in inputs
                 if k in records and records[k].get("fetched_at")), default=_now(),
            )
            # A derived tile is only as current as its oldest input, and it needs
            # an as_of of its own or every rebuild would log a fresh observation.
            record["as_of"] = min(
                (records[k].get("as_of") for k in inputs
                 if k in records and records[k].get("as_of")), default=record["fetched_at"],
            )
            record["error"] = None
            previous_values = {
                k: records[k]["prev_close"] for k in inputs
                if k in records and records[k].get("prev_close") is not None
            }
            if len(previous_values) == len(set(inputs)):
                record["prev_close"] = evaluate(tile.expr, previous_values)
                Quote(value=record["value"], prev_close=record["prev_close"]).derive_missing()
                record["change"] = record["value"] - record["prev_close"]
                record["change_pct"] = (
                    record["change"] / abs(record["prev_close"]) * 100.0
                    if record["prev_close"] else None
                )
        except ProviderError as exc:
            record["value"] = None
            record["error"] = str(exc)
        records[tile.key] = record
        print(f"  {record['origin']:<7} {tile.key:<14} "
              f"{record['value'] if record['value'] is not None else '—'}")

    ordered = [records[t.key] for t in tiles if t.key in records]

    stamp = _now()
    append_history([
        (stamp, r["key"], r["value"], str(r.get("as_of") or "")) for r in ordered
        if r.get("value") is not None and r["origin"] in ("live", "manual")
    ])

    history = load_history()
    for record in ordered:
        if not record["series"]:
            record["series"] = history.get(record["key"], [])

    _mark_staleness(ordered, cockpit.stale_after_minutes)

    snapshot = {
        "generated_at": stamp,
        "title": cockpit.title,
        "tagline": cockpit.tagline,
        "timezone": cockpit.timezone,
        "stale_after_minutes": cockpit.stale_after_minutes,
        "groups": cockpit.groups(),
        "tiles": ordered,
    }
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    config.SNAPSHOT_FILE.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    return snapshot


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch quotes into data/snapshot.json")
    parser.add_argument("--offline", action="store_true",
                        help="skip HTTP entirely; use the agent inbox and cache only")
    parser.add_argument("--only", nargs="*", help="restrict to these tile keys")
    args = parser.parse_args(argv)
    snapshot = run(offline=args.offline, only=args.only)
    live = sum(1 for t in snapshot["tiles"] if t["origin"] == "live")
    manual = sum(1 for t in snapshot["tiles"] if t["origin"] == "manual")
    cached = sum(1 for t in snapshot["tiles"] if t["origin"] == "cache")
    missing = sum(1 for t in snapshot["tiles"] if t["origin"] == "missing")
    print(f"\n{live} live · {manual} manual · {cached} cached · {missing} missing")
    return 1 if missing == len(snapshot["tiles"]) else 0
