"""Unit tests — run with: python3 -m unittest discover -s tests -v

The provider tests run against recorded payload *shapes* rather than the live
endpoints, so they stay meaningful on a host with no market-data access.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cockpit import config, news, render, synthesize  # noqa: E402
from cockpit.fetch import evaluate  # noqa: E402
from cockpit.providers import (  # noqa: E402
    ProviderError, Quote, _num, fetch_cnbc, fetch_fred, fetch_sec,
)


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

    def test_a_blank_price_is_distinguishable_from_a_missing_one(self):
        """The VIX fell to relayed overnight and the error could not say why:
        it listed the payload's keys alphabetically, truncated before "last"
        ever appeared. Absent and blank have to read differently."""
        blank = {"symbol": ".VIX", "last": "", "previous_day_closing": "",
                 "curmktstatus": "POST_MKT", "high": "15.9"}
        with self.assertRaises(ProviderError) as caught:
            patched_cnbc({"FormattedQuoteResult": {"FormattedQuote": [blank]}})
        message = str(caught.exception)
        self.assertIn("last=''", message)
        self.assertIn("POST_MKT", message)
        # The symbol comes from the tile being fetched, not from the payload.
        self.assertIn("US10Y", message)

        with self.assertRaises(ProviderError) as caught:
            patched_cnbc({"FormattedQuoteResult": {"FormattedQuote": [{"symbol": ".VIX"}]}})
        self.assertIn("absent:", str(caught.exception))
        self.assertNotIn("last=''", str(caught.exception))


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


class SecFilings(unittest.TestCase):
    """Filers report Q1-Q3 on 10-Qs and only a full year on the 10-K, restate
    quarters in later filings, and label the same line differently from each
    other. A trailing-twelve-month figure has to survive all three."""

    class Tile:
        provider, key, decimals, label = "sec", "capex", 2, "Capex"
        symbol, transform, scale = "", "", 1.0

    def _fetch(self, concepts, symbol, scale=1.0):
        """concepts: {(cik, tag): [(start, end, val, filed), …]}"""
        from cockpit import providers

        tile = self.Tile()
        tile.symbol, tile.scale = symbol, scale

        def fake_get_json(url, headers=None):
            cik = url.split("/CIK")[1][:10].lstrip("0")
            tag = url.rsplit("/", 1)[1].removesuffix(".json")
            rows = concepts.get((cik, tag))
            if rows is None:
                raise ProviderError(f"no USD facts for CIK {cik} tag {tag}")
            return {"cik": int(cik), "units": {"USD": [
                {"start": s, "end": e, "val": v, "filed": f} for s, e, v, f in rows]}}

        original = providers._get_json
        providers._get_json = fake_get_json
        try:
            return fetch_sec(tile)
        finally:
            providers._get_json = original

    @staticmethod
    def _quarters(values, start_year=2024):
        """Consecutive 91-day quarters, oldest first."""
        rows, month = [], 0
        for value in values:
            y0, m0 = start_year + month // 12, month % 12 + 1
            month += 3
            y1, m1 = start_year + month // 12, month % 12 + 1
            rows.append((f"{y0}-{m0:02d}-01", f"{y1}-{m1:02d}-01", value, "2026-01-01"))
        return rows

    def test_ttm_sums_the_last_four_quarters(self):
        rows = self._quarters([10.0, 20.0, 30.0, 40.0, 50.0, 60.0])
        quote = self._fetch({("789019", "Capex"): rows}, "789019/Capex")
        self.assertAlmostEqual(quote.value, 30 + 40 + 50 + 60)
        self.assertAlmostEqual(quote.prev_close, 20 + 30 + 40 + 50)

    def test_several_filers_are_summed(self):
        rows = self._quarters([1.0, 2.0, 3.0, 4.0, 5.0])
        quote = self._fetch({("789019", "Capex"): rows, ("1018724", "Capex"): rows},
                            "789019+1018724/Capex")
        self.assertAlmostEqual(quote.value, 2 * (2 + 3 + 4 + 5))
        self.assertEqual(quote.field_map["_filers"], "2")

    def test_it_falls_through_to_the_tag_a_filer_actually_uses(self):
        """Amazon books capex under a different line item than Microsoft."""
        rows = self._quarters([1.0, 2.0, 3.0, 4.0, 5.0])
        quote = self._fetch({("1018724", "PaymentsToAcquireProductiveAssets"): rows},
                            "1018724/PaymentsToAcquirePropertyPlantAndEquipment|"
                            "PaymentsToAcquireProductiveAssets")
        self.assertAlmostEqual(quote.value, 14.0)
        self.assertEqual(quote.field_map["_tags"], "PaymentsToAcquireProductiveAssets")

    def test_a_missing_fourth_quarter_comes_from_the_annual_figure(self):
        rows = self._quarters([10.0, 20.0, 30.0, 40.0, 50.0])
        # Drop the quarter ending 2025-01-01 and give the year that contains it.
        rows = [r for r in rows if r[1] != "2025-01-01"]
        rows.append(("2024-01-01", "2025-01-01", 10 + 20 + 30 + 99.0, "2026-01-01"))
        quote = self._fetch({("789019", "Capex"): rows}, "789019/Capex")
        # Q4 recovered as the year minus its three reported quarters (99), then
        # the trailing four are Q2, Q3, that recovered Q4, and the next quarter.
        self.assertAlmostEqual(quote.value, 20 + 30 + 99.0 + 50.0)

    def test_a_restated_quarter_takes_the_later_filing(self):
        rows = self._quarters([10.0, 20.0, 30.0, 40.0, 50.0])
        restated = (rows[-1][0], rows[-1][1], 99.0, "2026-06-01")
        quote = self._fetch({("789019", "Capex"): rows + [restated]}, "789019/Capex")
        self.assertAlmostEqual(quote.value, 20 + 30 + 40 + 99.0)

    def test_scale_reaches_the_reported_figure(self):
        rows = self._quarters([1e9, 2e9, 3e9, 4e9, 5e9])
        quote = self._fetch({("789019", "Capex"): rows}, "789019/Capex", scale=1e-9)
        self.assertAlmostEqual(quote.value, 14.0)

    def test_it_prefers_the_tag_that_runs_to_today(self):
        """A filer that changed line items leaves the old tag populated but
        frozen. First-match would report figures from whenever it stopped —
        which is exactly how nvda_revenue first shipped reading 2020."""
        stale = self._quarters([1.0, 2.0, 3.0, 4.0, 5.0], start_year=2016)
        current = self._quarters([10.0, 20.0, 30.0, 40.0, 50.0], start_year=2025)
        quote = self._fetch({("1045810", "OldTag"): stale,
                             ("1045810", "NewTag"): current},
                            "1045810/OldTag|NewTag")
        self.assertAlmostEqual(quote.value, 20 + 30 + 40 + 50)
        self.assertEqual(quote.field_map["_tags"], "NewTag")
        self.assertGreater(quote.as_of, "2025")

    def test_a_filer_frozen_years_back_is_refused_not_summed(self):
        """Summing a live filer with a dead series dates the whole tile to
        whenever the dead one stopped, while still looking like real data."""
        current = self._quarters([10.0] * 6, start_year=2025)
        frozen = self._quarters([1.0] * 6, start_year=2016)
        with self.assertRaises(ProviderError) as caught:
            self._fetch({("789019", "Capex"): current, ("1018724", "Capex"): frozen},
                        "789019+1018724/Capex")
        self.assertIn("across eras", str(caught.exception))

    def test_filers_a_few_weeks_apart_are_still_summed(self):
        """Quarters end on different dates and some file later; that is normal
        and must not trip the staleness guard."""
        a = self._quarters([10.0] * 6, start_year=2025)
        b = [(s, e, 5.0, f) for s, e, _, f in self._quarters([0.0] * 5, start_year=2025)]
        quote = self._fetch({("789019", "Capex"): a, ("1018724", "Capex"): b},
                            "789019+1018724/Capex")
        self.assertAlmostEqual(quote.value, 40.0 + 20.0)

    def test_too_little_history_raises_rather_than_part_summing(self):
        with self.assertRaises(ProviderError):
            self._fetch({("789019", "Capex"): self._quarters([1.0, 2.0])}, "789019/Capex")

    def test_a_malformed_symbol_raises(self):
        with self.assertRaises(ProviderError):
            self._fetch({}, "789019")


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


class Briefing(unittest.TestCase):
    """The strip must merge outlets covering one story, keep the headlines
    verbatim, and never render anything a reader cannot click through to."""

    FEED = """<?xml version="1.0"?><rss><channel>
      <item><title>Fed holds rates steady as inflation cools</title>
        <link>https://example.com/a</link>
        <pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate></item>
      <item><title>Nvidia slides on China export report</title>
        <link>https://example.com/b</link>
        <pubDate>Mon, 24 Aug 2026 10:00:00 GMT</pubDate></item>
    </channel></rss>"""

    def test_it_parses_titles_links_and_times(self):
        items = news.parse_feed(self.FEED.encode(), "Reuters")
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].headline, "Fed holds rates steady as inflation cools")
        self.assertEqual(items[0].url, "https://example.com/a")
        self.assertEqual(items[0].published.hour, 12)

    def test_an_item_without_a_link_is_dropped(self):
        """A bullet a reader cannot check is worse than one fewer bullet."""
        feed = """<rss><channel><item><title>Something happened</title>
          <pubDate>Mon, 24 Aug 2026 12:00:00 GMT</pubDate></item></channel></rss>"""
        self.assertEqual(news.parse_feed(feed.encode(), "X"), [])

    def test_unparseable_xml_yields_nothing_rather_than_raising(self):
        self.assertEqual(news.parse_feed(b"<not xml", "X"), [])

    def _item(self, headline, source, hour):
        from datetime import datetime, timezone

        return news.Item(headline, f"https://example.com/{source}{hour}", source,
                         datetime(2026, 8, 24, hour, tzinfo=timezone.utc))

    def test_two_outlets_on_one_story_become_one_bullet(self):
        stories = news.group([
            self._item("Fed holds rates steady as inflation cools", "Reuters", 12),
            self._item("Fed holds rates steady, citing cooling inflation", "Bloomberg", 11),
        ])
        self.assertEqual(len(stories), 1)
        self.assertEqual(stories[0].sources, ["Bloomberg", "Reuters"])

    def test_different_stories_stay_apart(self):
        stories = news.group([
            self._item("Fed holds rates steady as inflation cools", "Reuters", 12),
            self._item("Nvidia slides on China export report", "CNBC", 11),
        ])
        self.assertEqual(len(stories), 2)

    def test_a_common_name_alone_does_not_merge_unrelated_stories(self):
        """Rarity alone over-merged: with "Trump" under the corpus ceiling, an
        EPA lawsuit and a South Korea defence story became one cluster."""
        items = [self._item("Trump EPA fast-tracks datacenter coolant approval", "Guardian", 12),
                 self._item("Trump and South Korea agree new defence terms", "CNBC", 11)]
        self.assertEqual(len(news.group(items)), 2)

    def test_a_shared_rare_name_with_real_overlap_still_merges(self):
        items = [self._item("Qiagen names Pratt as chief executive", "Bloomberg", 12),
                 self._item("Qiagen names Jonathan Pratt chief executive officer", "MarketWatch", 11)]
        self.assertEqual(len(news.group(items)), 1)

    def test_corroboration_outranks_recency(self):
        """Two desks independently running a story is the only editorial
        signal this strip has, so it has to beat a fresher single-source item."""
        stories = sorted(news.group([
            self._item("Fed holds rates steady as inflation cools", "Reuters", 9),
            self._item("Fed holds rates steady, citing cooling inflation", "WSJ", 9),
            self._item("Nvidia slides on China export report", "CNBC", 23),
        ]), key=news.Story.rank, reverse=True)
        self.assertEqual(stories[0].sources, ["Reuters", "WSJ"])

    def test_the_shown_headline_is_one_an_outlet_published(self):
        published = ["Fed holds rates steady as inflation cools",
                     "Fed holds rates steady, citing cooling inflation"]
        stories = news.group([self._item(published[0], "Reuters", 12),
                              self._item(published[1], "Bloomberg", 11)])
        self.assertIn(stories[0].lead.headline, published)

    def test_one_firehose_outlet_cannot_take_the_whole_strip(self):
        """A market-notes feed posts every few minutes and a curated top-stories
        feed a few times a day. On recency alone the firehose wins every slot,
        which is how the first live briefing came back four-fifths one outlet."""
        firehose = [self._item(f"Small market note number {n}", "Firehose", 20 + n // 10)
                    for n in range(12)]
        curated = [self._item("Fed holds rates steady as inflation cools", "Bloomberg", 9),
                   self._item("Steel tariffs escalate between US and Canada", "WSJ", 8)]
        ranked = sorted(news.group(firehose + curated), key=news.Story.rank, reverse=True)
        chosen = news.select(ranked, count=5, outlets=3)
        leads = [story.lead.source for story in chosen]
        self.assertLessEqual(leads.count("Firehose"), 2, leads)
        self.assertIn("Bloomberg", leads)
        self.assertIn("WSJ", leads)

    def test_the_strip_still_fills_when_one_outlet_carries_it(self):
        """The cap is a share of the slots, not a hard ceiling: if the other
        outlets went quiet, a thin spread beats a two-bullet strip."""
        headlines = ["Fed holds rates steady as inflation cools",
                     "Oil slips below eighty dollars a barrel",
                     "Chipmakers rally on export licence relief",
                     "Regional banks tumble after deposit data",
                     "Housing starts fall to a three-year low"]
        items = [self._item(h, "Only", 10 + n) for n, h in enumerate(headlines)]
        ranked = sorted(news.group(items), key=news.Story.rank, reverse=True)
        self.assertEqual(len(ranked), 5, "fixture headlines must be distinct stories")
        # outlets=3 gives a cap of 2, so filling to five must use the fallback.
        self.assertEqual(len(news.select(ranked, count=5, outlets=3)), 5)

    def test_headlines_are_escaped_into_the_page(self):
        markup = render.briefing_html({
            "captured_at": "2026-08-24T12:00:00+00:00",
            "sources_read": ["X"],
            "stories": [{"headline": "<script>alert(1)</script>",
                         "url": "https://example.com/a", "sources": ["X"],
                         "published": "2026-08-24T12:00:00+00:00"}],
        }, render.ZoneInfo("UTC"))
        self.assertNotIn("<script>alert", markup)
        self.assertIn("&lt;script&gt;", markup)

    def test_an_empty_briefing_renders_nothing(self):
        self.assertEqual(render.briefing_html({"stories": []}, render.ZoneInfo("UTC")), "")
        self.assertEqual(render.briefing_html(None, render.ZoneInfo("UTC")), "")


class Synthesis(unittest.TestCase):
    """The written lines are the only text on the board nobody published, so
    the grounding checks are the whole safety story."""

    STORIES = [
        {"sources": ["BBC", "Bloomberg"], "headline": "Yields surge",
         "variants": [{"source": "BBC", "headline": "German finance chief blames Trump"}]},
        {"sources": ["Guardian"], "headline": "Streaming prices rise",
         "variants": [{"source": "Guardian", "headline": "Netflix raises prices"}]},
    ]

    def test_a_line_citing_a_real_topic_is_kept(self):
        kept = synthesize.validate([{"text": "Yields surge", "topic_ids": [0]}], self.STORIES)
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0]["sources"], ["BBC", "Bloomberg"])

    def test_a_line_citing_a_topic_that_does_not_exist_is_dropped(self):
        """The citation is the only check available on prose written elsewhere."""
        for ids in ([9], [-1], [0, 9]):
            self.assertEqual(
                synthesize.validate([{"text": "Something", "topic_ids": ids}], self.STORIES),
                [], msg=str(ids))

    def test_a_line_citing_nothing_is_dropped(self):
        self.assertEqual(
            synthesize.validate([{"text": "Vague market commentary", "topic_ids": []}],
                                self.STORIES), [])

    def test_an_empty_line_is_dropped(self):
        self.assertEqual(
            synthesize.validate([{"text": "   ", "topic_ids": [0]}], self.STORIES), [])

    def test_sources_are_the_union_of_the_cited_topics(self):
        kept = synthesize.validate([{"text": "Both", "topic_ids": [0, 1]}], self.STORIES)
        self.assertEqual(kept[0]["sources"], ["BBC", "Bloomberg", "Guardian"])

    def test_the_prompt_carries_every_outlet_wording(self):
        text = synthesize.prompt_for(self.STORIES)
        self.assertIn("German finance chief blames Trump", text)
        self.assertIn("carried by 2 outlets", text)

    def test_without_a_key_it_declines_rather_than_guessing(self):
        import os

        original = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            self.assertIsNone(synthesize.synthesize({"stories": self.STORIES}))
        finally:
            if original is not None:
                os.environ["ANTHROPIC_API_KEY"] = original

    def test_the_model_is_not_quietly_downgraded(self):
        """Choosing a cheaper model is the bill-payer's call, not the code's."""
        self.assertEqual(synthesize.MODEL, "claude-opus-5")

    def test_each_outlet_links_to_what_that_outlet_published(self):
        """Naming the outlets a line was compressed from is only useful if the
        reader can open what they ran."""
        markup = render.briefing_html({
            "captured_at": "2026-08-26T12:00:00+00:00",
            "sources_read": ["BBC", "Bloomberg"],
            "stories": [{
                "headline": "h", "url": "https://bbc/x", "sources": ["BBC", "Bloomberg"],
                "published": "2026-08-26T12:00:00+00:00",
                "variants": [{"source": "BBC", "headline": "a", "url": "https://bbc/x"},
                             {"source": "Bloomberg", "headline": "b", "url": "https://bloom/y"}],
            }],
            "synthesis": {"model": "claude-opus-5", "written_at": "2026-08-26T12:05:00+00:00",
                          "lines": [{"text": "Something happened", "topic_ids": [0],
                                     "sources": ["BBC", "Bloomberg"]}]},
        }, render.ZoneInfo("UTC"))
        self.assertIn('href="https://bbc/x"', markup)
        self.assertIn('href="https://bloom/y"', markup)
        self.assertIn('rel="noopener"', markup)

    def test_a_line_spanning_topics_links_every_outlet_it_cites(self):
        stories = [
            {"sources": ["BBC"], "variants": [{"source": "BBC", "headline": "a",
                                               "url": "https://bbc/1"}]},
            {"sources": ["NPR"], "variants": [{"source": "NPR", "headline": "b",
                                               "url": "https://npr/2"}]},
        ]
        links = render._article_links(stories, [0, 1])
        self.assertEqual(links, {"BBC": "https://bbc/1", "NPR": "https://npr/2"})
        # A topic id outside the list must not raise or invent a link.
        self.assertEqual(render._article_links(stories, [0, 99]),
                         {"BBC": "https://bbc/1"})

    def test_an_outlet_with_no_url_renders_as_plain_text(self):
        """Older snapshots carry variants without a url; a link that goes
        nowhere is worse than no link."""
        markup = render._sources_html(["BBC", "NPR"], {"BBC": "https://bbc/1"})
        self.assertIn('href="https://bbc/1"', markup)
        self.assertIn("NPR", markup)
        self.assertEqual(markup.count("<a "), 1)

    def test_a_hostile_url_or_name_cannot_break_out(self):
        markup = render._sources_html(
            ['Evil" onmouseover="x'], {'Evil" onmouseover="x': 'https://a/"><script>'})
        self.assertNotIn('onmouseover="x"', markup)
        self.assertNotIn("<script>", markup)

    def test_an_outlet_links_to_its_piece_on_the_line_being_shown(self):
        """A cluster can hold more than one story. Sending a reader to the
        wrong article under a line naming their outlet is worse than no link —
        the first live build linked an EPA lawsuit line to a defence story."""
        stories = [{
            "sources": ["CNBC", "Guardian"],
            "variants": [
                {"source": "CNBC", "headline": "Trump and South Korea agree defence deal",
                 "url": "https://cnbc/defence"},
                {"source": "CNBC", "headline": "Groups sue EPA over datacenter chemicals",
                 "url": "https://cnbc/epa"},
                {"source": "Guardian", "headline": "Trump EPA fast-tracks datacenter coolant",
                 "url": "https://guardian/epa"},
            ],
        }]
        links = render._article_links(
            stories, [0], "Groups sue Trump's EPA over datacenter chemicals")
        self.assertEqual(links["CNBC"], "https://cnbc/epa")
        self.assertEqual(links["Guardian"], "https://guardian/epa")

    def test_without_a_line_the_first_article_is_used(self):
        stories = [{"variants": [
            {"source": "BBC", "headline": "a", "url": "https://bbc/1"},
            {"source": "BBC", "headline": "b", "url": "https://bbc/2"},
        ]}]
        self.assertEqual(render._article_links(stories, [0])["BBC"], "https://bbc/1")

    def test_written_lines_are_escaped_and_attributed(self):
        markup = render.briefing_html({
            "captured_at": "2026-08-24T12:00:00+00:00", "sources_read": ["BBC"],
            "stories": [{"headline": "h", "url": "u", "sources": ["BBC"],
                         "published": "2026-08-24T12:00:00+00:00"}],
            "synthesis": {"model": "claude-opus-5", "written_at": "2026-08-24T12:05:00+00:00",
                          "lines": [{"text": "<script>alert(1)</script>",
                                     "topic_ids": [0], "sources": ["BBC"]}]},
        }, render.ZoneInfo("UTC"))
        self.assertNotIn("<script>alert", markup)
        self.assertIn("Written by", markup)
        self.assertIn("claude-opus-5", markup)

    def test_the_published_headlines_render_when_nothing_was_written(self):
        markup = render.briefing_html({
            "captured_at": "2026-08-24T12:00:00+00:00", "sources_read": ["BBC"],
            "stories": [{"headline": "Real headline", "url": "https://x/a",
                         "sources": ["BBC"], "published": "2026-08-24T12:00:00+00:00"}],
            "synthesis": {"lines": []},
        }, render.ZoneInfo("UTC"))
        self.assertIn("Real headline", markup)
        self.assertNotIn("Written by", markup)


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
