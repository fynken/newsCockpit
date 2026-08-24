"""Command line entry point: python -m cockpit <command>."""

from __future__ import annotations

import argparse
import json
import sys

from . import config, fetch, render


def cmd_fetch(args) -> int:
    print("Fetching…")
    return fetch.main(
        (["--offline"] if args.offline else []) + (["--only", *args.only] if args.only else [])
    )


def cmd_build(args) -> int:
    out = render.build()
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
    return 0


def cmd_refresh(args) -> int:
    print("Fetching…")
    snapshot = fetch.run(offline=args.offline, only=args.only)
    out = render.build(snapshot)
    live = sum(1 for t in snapshot["tiles"] if t["origin"] == "live")
    manual = sum(1 for t in snapshot["tiles"] if t["origin"] == "manual")
    cached = sum(1 for t in snapshot["tiles"] if t["origin"] == "cache")
    missing = sum(1 for t in snapshot["tiles"] if t["origin"] == "missing")
    print(f"\n{live} live · {manual} relayed · {cached} cached · {missing} missing")
    print(f"Wrote {out} ({out.stat().st_size:,} bytes)")
    if missing == len(snapshot["tiles"]):
        print(
            "\nNo tile resolved. If this host cannot reach market data providers, put "
            "readings into data/agent-inbox.json (see 'python -m cockpit inbox') and "
            "re-run with --offline.",
            file=sys.stderr,
        )
        return 1
    return 0


def cmd_synthesize(args) -> int:
    """Rewrite the briefing's headlines as compounded lines, if a key is set.

    A no-op without ANTHROPIC_API_KEY, so the same command is safe in CI, on a
    laptop and in a fork that has no key at all.
    """
    from . import synthesize as synth

    snapshot = json.loads(config.SNAPSHOT_FILE.read_text(encoding="utf-8"))
    briefing = snapshot.get("briefing") or {}
    written = synth.synthesize(briefing)
    if not written:
        briefing.pop("synthesis", None)
        snapshot["briefing"] = briefing
        config.SNAPSHOT_FILE.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
        return 0
    briefing["synthesis"] = written
    snapshot["briefing"] = briefing
    config.SNAPSHOT_FILE.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")
    for line in written["lines"]:
        print(f"  · {line['text']}")
    return 0


def cmd_status(args) -> int:
    cockpit = config.load()
    try:
        snapshot = json.loads(config.SNAPSHOT_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        print("No snapshot yet — run 'python -m cockpit refresh'.")
        return 1
    print(f"{cockpit.title} — built {snapshot['generated_at']}")
    for record in snapshot["tiles"]:
        value = record.get("value")
        shown = "—" if value is None else f"{value:,.{record.get('decimals', 2)}f}"
        flag = " STALE" if record.get("stale") and value is not None else ""
        print(f"  {record['origin']:<7} {record['key']:<14} {shown:>14}{flag}")
        if record.get("error"):
            print(f"          ↳ {record['error']}")
    return 0


def cmd_raw(args) -> int:
    """Print a provider's untouched payload, for checking field names by eye."""
    from .providers import raw_cnbc

    print(json.dumps(raw_cnbc(args.symbol), indent=2)[: args.limit])
    return 0


def cmd_inbox(args) -> int:
    """Print a skeleton agent-inbox.json for the configured tiles."""
    cockpit = config.load()
    keys = args.only or [t.key for t in cockpit.tiles if t.provider != "derived"]
    template = {
        "captured_at": "<ISO-8601 timestamp of when these were observed>",
        "quotes": {
            key: {
                "value": 0.0,
                "change": 0.0,
                "as_of": "<what the source says the reading is as of>",
                "source_url": cockpit.by_key(key).quote_url() if cockpit.by_key(key) else "",
                "note": "",
            }
            for key in keys
        },
    }
    print(json.dumps(template, indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cockpit", description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add(name, handler, help_text):
        sub = subparsers.add_parser(name, help=help_text)
        sub.set_defaults(handler=handler)
        return sub

    for name, handler, help_text in (
        ("fetch", cmd_fetch, "fetch quotes into data/snapshot.json"),
        ("refresh", cmd_refresh, "fetch, then rebuild dist/index.html"),
    ):
        sub = add(name, handler, help_text)
        sub.add_argument("--offline", action="store_true",
                         help="skip HTTP; use data/agent-inbox.json and the cache only")
        sub.add_argument("--only", nargs="*", help="restrict to these tile keys")

    add("build", cmd_build, "rebuild dist/index.html from the current snapshot")
    add("synthesize", cmd_synthesize,
        "rewrite the briefing as compounded lines (needs ANTHROPIC_API_KEY)")
    add("status", cmd_status, "print the last snapshot as a table")

    raw = add("raw", cmd_raw, "dump a CNBC payload to check field names")
    raw.add_argument("symbol")
    raw.add_argument("--limit", type=int, default=4000)

    inbox = add("inbox", cmd_inbox, "print an agent-inbox.json skeleton")
    inbox.add_argument("--only", nargs="*")

    args = parser.parse_args(argv)
    try:
        return args.handler(args)
    except config.ConfigError as exc:
        print(f"sources.toml: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
