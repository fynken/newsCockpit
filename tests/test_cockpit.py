"""Unit tests — run with: python3 -m unittest discover -s tests -v

The provider tests run against recorded payload *shapes* rather than the live
endpoints, so they stay meaningful on a host with no market-data access.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cockpit import config, render  # noqa: E402
from cockpit.fetch import evaluate  # noqa: E402
from cockpit.providers import ProviderError, Quote, _num, fetch_cnbc, fetch_fred  # noqa: E402


class FakeTile:
    def __init__(self, symbol="US10Y", key="us10y", decimals=3):
        self.symbol = symbol
        self.key = key
        self.decimals = decimals
        self.provider = "cnbc"


def patched_cnbc(payload):
    """Run fetch_cnbc against a canned payload."""
    from cockpit import providers

    original = providers._get_json
    providers._get_json = lambda url, headers=None: payload
    try:
        return fetch_cnbc(FakeTile())
    finally:
        providers._get_json = original


class NumberParsing(unittest.TestCase):
    def test_formats(self):
        cases = {
            "4.253": 4.253, "+0.50%": 0.5, "1,234.56": 1234.56, "-0.021": -0.021,
            "UNCH": None, "": None, "N/A": None, None: None, 12: 12.0,
            "-1,024.5": -1024.5, "$4,231.10": 4231.10,
        }
        for raw, expected in cases.items():
            self.assertEqual(_num(raw), expected, msg=f"_num({raw!r})")


class CnbcEnvelopes(unittest.TestCase):
    """CNBC wraps quotes differently per instrument class; all shapes must unwrap."""

    QUOTE = {
        "symbol": "US10Y", "name": "U.S. 10 Year Treasury", "last": "4.253",
        "change": "0.021", "change_pct": "+0.50%", "previous_day_closing": "4.232",
        "open": "4.240", "high": "4.262", "low": "4.228",
        "yrhiprice": "4.810", "yrloprice": "3.604",
        "curmktstatus": "REG_MKT", "last_time": "2026-08-21T09:27:00.000-0400",
    }

    def test_formatted_envelope(self):
        quote = patched_cnbc({"FormattedQuoteResult": {"FormattedQuote": [self.QUOTE]}})
        self.assertAlmostEqual(quote.value, 4.253)
        self.assertAlmostEqual(quote.change, 0.021)
        self.assertAlmostEqual(quote.prev_close, 4.232)
        self.assertAlmostEqual(quote.year_low, 3.604)
        self.assertEqual(quote.instrument_name, "U.S. 10 Year Treasury")
        self.assertEqual(quote.field_map["value"], "last")

    def test_quickquote_envelope(self):
        quote = patched_cnbc({"QuickQuoteResult": {"QuickQuote": self.QUOTE}})
        self.assertAlmostEqual(quote.value, 4.253)

    def test_bare_list(self):
        self.assertAlmostEqual(patched_cnbc([self.QUOTE]).value, 4.253)

    def test_alternate_price_key(self):
        """Some bond quotes carry the yield under a different key."""
        payload = {k: v for k, v in self.QUOTE.items() if k != "last"}
        payload["latest_yield"] = "4.301"
        quote = patched_cnbc({"FormattedQuoteResult": {"FormattedQuote": [payload]}})
        self.assertAlmostEqual(quote.value, 4.301)
        self.assertEqual(quote.field_map["value"], "latest_yield")

    def test_unrecognised_shape_raises(self):
        with self.assertRaises(ProviderError):
            patched_cnbc({"something": "else"})

    def test_missing_price_raises(self):
        with self.assertRaises(ProviderError):
            patched_cnbc({"FormattedQuoteResult": {"FormattedQuote": [{"symbol": "X"}]}})


class FredSeries(unittest.TestCase):
    """FRED gives a whole history; the board needs the latest reading, the one
    before it, and — for a price index — the year-on-year rate rather than the
    index level, which is meaningless on a card."""

    class Tile:
        provider, symbol, key, decimals = "fred", "CPIAUCNS", "cpi_yoy", 2
        transform, scale = "", 1.0

    def _fetch(self, csv_body, **attrs):
        from cockpit import providers

        tile = self.Tile()
        for name, value in attrs.items():
            setattr(tile, name, value)
        original = providers._get
        self.sent = {}

        def capture(url, headers=None):
            self.sent = {"url": url, "headers": headers or {}}
            return csv_body.encode()

        providers._get = capture
        try:
            return fetch_fred(tile)
        finally:
            providers._get = original

    def test_it_does_not_claim_to_be_a_browser(self):
        """FRED hangs until timeout on the Chrome user agent this board sends
        everywhere else, and answers a plain one in a fifth of a second."""
        from cockpit import providers

        self._fetch(self._monthly([100.0, 101.0]))
        agent = self.sent["headers"].get("User-Agent", "")
        self.assertEqual(agent, providers.PLAIN_AGENT)
        self.assertNotIn("Chrome", agent)

    @staticmethod
    def _monthly(values, start_year=2025):
        """A CSV of consecutive monthly observations, FRED's newer header."""
        rows = ["observation_date,CPIAUCNS"]
        for index, value in enumerate(values):
            year, month = start_year + index // 12, index % 12 + 1
            rows.append(f"{year}-{month:02d}-01,{value}")
        return "\n".join(rows) + "\n"

    def test_level_takes_the_last_observation(self):
        quote = self._fetch(self._monthly([100.0, 101.0, 102.5]))
        self.assertAlmostEqual(quote.value, 102.5)
        self.assertAlmostEqual(quote.prev_close, 101.0)
        self.assertEqual(quote.as_of, "2025-03-01")

    def test_scale_converts_the_units(self):
        """FRED publishes reserves in millions; the card shows trillions."""
        quote = self._fetch(self._monthly([1_200_000.0, 1_250_000.0]), scale=1e-6)
        self.assertAlmostEqual(quote.value, 1.25)
        self.assertAlmostEqual(quote.prev_close, 1.2)

    def test_yoy_is_the_change_against_a_year_earlier(self):
        # 13 months: a flat 100 for a year, then 103 — a 3% annual rate.
        quote = self._fetch(self._monthly([100.0] * 12 + [103.0, 104.0]),
                            transform="yoy")
        self.assertAlmostEqual(quote.value, 4.0)      # 104 vs 100 a year before
        self.assertAlmostEqual(quote.prev_close, 3.0)  # last month's rate
        self.assertAlmostEqual(quote.change, 1.0)      # rate rose a point
        self.assertEqual(quote.as_of, "2026-02-01")

    def test_missing_observations_are_dropped(self):
        body = "observation_date,X\n2026-01-01,.\n2026-02-01,5.0\n"
        self.assertAlmostEqual(self._fetch(body).value, 5.0)

    def test_the_older_date_header_still_parses(self):
        self.assertAlmostEqual(self._fetch("DATE,X\n2026-01-01,7.5\n").value, 7.5)

    def test_too_short_a_history_for_yoy_raises(self):
        with self.assertRaises(ProviderError):
            self._fetch(self._monthly([100.0] * 6), transform="yoy")

    def test_an_all_missing_series_raises(self):
        with self.assertRaises(ProviderError):
            self._fetch("observation_date,X\n2026-01-01,.\n")


class Derivation(unittest.TestCase):
    def test_change_from_prev_close(self):
        quote = Quote(value=100.0, prev_close=98.0).derive_missing()
        self.assertAlmostEqual(quote.change, 2.0)
        self.assertAlmostEqual(quote.change_pct, 2.0408163, places=5)

    def test_prev_close_from_change(self):
        quote = Quote(value=100.0, change=2.0).derive_missing()
        self.assertAlmostEqual(quote.prev_close, 98.0)


class Expressions(unittest.TestCase):
    def test_arithmetic(self):
        self.assertAlmostEqual(evaluate("(a - b) * 100", {"a": 4.7, "b": 3.98}), 72.0)

    def test_unary_minus(self):
        self.assertAlmostEqual(evaluate("-a + 1", {"a": 2.0}), -1.0)

    def test_unknown_name_raises(self):
        with self.assertRaises(ProviderError):
            evaluate("a + b", {"a": 1.0})

    def test_calls_are_rejected(self):
        for hostile in ('__import__("os").system("id")', "open('/etc/passwd')", "a.__class__"):
            with self.assertRaises(ProviderError, msg=hostile):
                evaluate(hostile, {"a": 1.0})


class Formatting(unittest.TestCase):
    def test_direction_respects_good_when(self):
        rising = {"change": 1.0, "decimals": 2, "good_when": "up"}
        self.assertEqual(render.delta_class(rising), "up")
        self.assertEqual(render.delta_class({**rising, "good_when": "down"}), "down")
        self.assertEqual(render.delta_class({**rising, "good_when": "neutral"}), "flat")

    def test_delta_always_carries_a_glyph_and_sign(self):
        """Direction must never be colour-alone."""
        markup = render.delta_html({"value": 1.0, "change": -0.5, "change_pct": -2.0,
                                    "decimals": 2, "good_when": "up"})
        self.assertIn("▼", markup)
        self.assertIn("-0.50", markup)

    def test_missing_value_renders_a_dash(self):
        self.assertEqual(render.fmt_value({"value": None, "decimals": 2}), "—")

    def test_labels_are_escaped(self):
        markup = render.tile_html(
            {"key": "x", "label": "<script>alert(1)</script>", "group": "g", "value": None,
             "origin": "missing", "decimals": 2, "symbol": "", "url": "", "error": "boom"},
            render.ZoneInfo("UTC"),
        )
        self.assertNotIn("<script>", markup)
        self.assertIn("&lt;script&gt;", markup)


class HistoryLedger(unittest.TestCase):
    """A repeated build off one set of readings must not fabricate a trend."""

    def setUp(self):
        import tempfile

        from cockpit import config as cfg

        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self._original = cfg.HISTORY_FILE
        cfg.HISTORY_FILE = Path(self.tmp.name) / "history.csv"
        self.addCleanup(lambda: setattr(cfg, "HISTORY_FILE", self._original))

    def test_same_market_timestamp_is_recorded_once(self):
        from cockpit import fetch as f

        row = ("2026-01-01T10:00:00+00:00", "us10y", 4.71, "2026-01-01T09:27:00-05:00")
        f.append_history([row])
        f.append_history([("2026-01-01T10:05:00+00:00", "us10y", 4.71, row[3])])
        self.assertEqual(len(f.load_history()["us10y"]), 1)

    def test_a_new_market_timestamp_is_recorded(self):
        from cockpit import fetch as f

        f.append_history([("2026-01-01T10:00:00+00:00", "us10y", 4.71, "2026-01-01T09:27:00-05:00")])
        f.append_history([("2026-01-01T11:00:00+00:00", "us10y", 4.75, "2026-01-01T10:27:00-05:00")])
        series = f.load_history()["us10y"]
        self.assertEqual([point[1] for point in series], [4.71, 4.75])

    def test_readings_without_a_timestamp_are_always_kept(self):
        from cockpit import fetch as f

        f.append_history([("2026-01-01T10:00:00+00:00", "x", 1.0, "")])
        f.append_history([("2026-01-01T11:00:00+00:00", "x", 1.0, "")])
        self.assertEqual(len(f.load_history()["x"]), 2)


class ProviderFallback(unittest.TestCase):
    """CNBC answers CI and refuses this sandbox; Yahoo does the reverse. A tile
    naming both must resolve wherever it is built, and must report the provider
    that actually answered rather than the one it asked first."""

    def setUp(self):
        from cockpit import fetch as f, providers

        self.fetch = f
        self.tile = config.Tile(
            key="us10y", label="US 10-Year", group="Rates", provider="cnbc",
            symbol="US10Y", alt_provider="yahoo", alt_symbol="^TNX", decimals=3,
        )
        self._original = dict(providers.PROVIDERS)
        self.addCleanup(lambda: providers.PROVIDERS.update(self._original))

    def _run(self, cnbc, yahoo):
        from cockpit import providers

        providers.PROVIDERS["cnbc"] = cnbc
        providers.PROVIDERS["yahoo"] = yahoo
        return self.fetch.resolve(self.tile, offline=False, inbox={}, previous={})

    @staticmethod
    def _answers(value):
        return lambda tile: Quote(value=value, prev_close=value - 0.01).derive_missing()

    @staticmethod
    def _refuses(message):
        def fetcher(tile):
            raise ProviderError(message)

        return fetcher

    def test_first_provider_wins_when_reachable(self):
        record = self._run(self._answers(4.253), self._refuses("HTTP 429"))
        self.assertEqual(record["origin"], "live")
        self.assertEqual(record["provider"], "cnbc")
        self.assertAlmostEqual(record["value"], 4.253)

    def test_falls_back_when_the_first_is_unreachable(self):
        record = self._run(self._refuses("HTTP 403 from quote.cnbc.com"), self._answers(4.714))
        self.assertEqual(record["origin"], "live")
        self.assertAlmostEqual(record["value"], 4.714)
        # The tile must not claim a CNBC symbol for a number Yahoo supplied.
        self.assertEqual(record["provider"], "yahoo")
        self.assertEqual(record["symbol"], "^TNX")
        self.assertIn("finance.yahoo.com", record["url"])
        self.assertIn("403", record["error"])

    def test_both_unreachable_falls_through_to_the_ladder(self):
        record = self._run(self._refuses("HTTP 403"), self._refuses("HTTP 429"))
        self.assertEqual(record["origin"], "missing")
        self.assertIsNone(record["value"])
        self.assertIn("cnbc", record["error"])
        self.assertIn("yahoo", record["error"])

    def test_a_tile_without_a_fallback_tries_one_provider(self):
        tried = []
        self.tile.alt_provider = self.tile.alt_symbol = ""
        self._run(lambda t: tried.append(t) or self._refuses("nope")(t),
                  lambda t: tried.append(t) or self._refuses("nope")(t))
        self.assertEqual(len(tried), 1)


class ConfigValidation(unittest.TestCase):
    def test_repo_sources_load(self):
        cockpit = config.load()
        self.assertTrue(cockpit.tiles)
        self.assertEqual(len({t.key for t in cockpit.tiles}), len(cockpit.tiles))
        for tile in cockpit.tiles:
            self.assertIn(tile.good_when, config.GOOD_WHEN)

    def test_alt_provider_needs_a_symbol(self):
        import tempfile

        broken = Path(tempfile.mkdtemp()) / "sources.toml"
        broken.write_text(
            '[[tile]]\nkey="x"\nlabel="X"\ngroup="G"\nprovider="cnbc"\n'
            'symbol="X"\nalt_provider="yahoo"\n', encoding="utf-8"
        )
        with self.assertRaises(config.ConfigError):
            config.load(broken)

    def test_every_fallback_names_a_known_provider(self):
        from cockpit.providers import PROVIDERS

        for tile in config.load().tiles:
            if tile.alt_provider:
                self.assertIn(tile.alt_provider, PROVIDERS, msg=tile.key)
                self.assertNotEqual(tile.alt_provider, tile.provider, msg=tile.key)

    def test_transform_is_validated(self):
        import tempfile

        broken = Path(tempfile.mkdtemp()) / "sources.toml"
        broken.write_text(
            '[[tile]]\nkey="x"\nlabel="X"\ngroup="G"\nprovider="fred"\n'
            'symbol="X"\ntransform="detrend"\n', encoding="utf-8"
        )
        with self.assertRaises(config.ConfigError):
            config.load(broken)

    def test_derived_expressions_only_name_tiles_that_exist(self):
        import ast

        cockpit = config.load()
        keys = {t.key for t in cockpit.tiles}
        for tile in cockpit.tiles:
            if tile.provider != "derived":
                continue
            names = {n.id for n in ast.walk(ast.parse(tile.expr, mode="eval"))
                     if isinstance(n, ast.Name)}
            self.assertTrue(names <= keys, msg=f"{tile.key} refers to {names - keys}")

    def test_featured_tile_is_unique(self):
        featured = [t for t in config.load().tiles if t.featured]
        self.assertLessEqual(len(featured), 1, "at most one hero figure per board")


if __name__ == "__main__":
    unittest.main()
