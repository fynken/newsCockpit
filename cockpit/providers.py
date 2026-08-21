"""Data providers.

Every provider is a function ``fetch(tile) -> Quote``. It either returns a
populated Quote or raises ProviderError; the orchestrator in fetch.py decides
what to do with a failure (fall back to the agent inbox, or carry the previous
value forward and mark the tile stale).

Only the standard library is used, so the pipeline runs anywhere Python 3.11
runs with no install step. urllib honours HTTPS_PROXY and SSL_CERT_FILE, which
is what lets this work from behind a corporate or sandbox egress proxy.
"""

from __future__ import annotations

import csv
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)
#: Some hosts stall a client that claims to be Chrome without behaving like
#: one — no browser TLS fingerprint, no matching headers. FRED is one: it
#: answers this agent in 0.2s and hangs until timeout on the Chrome string.
#: Say what we actually are, and where to complain.
PLAIN_AGENT = "newsCockpit/1.0 (+https://github.com/fynken/newsCockpit)"
TIMEOUT = 20
#: Statuses worth one more try — a throttle or a momentary edge failure, not a
#: verdict. One retry only: when a host is throttling the whole build (Yahoo
#: rate-limits by IP, so every tile hits it at once), more attempts just make a
#: failing refresh slow.
RETRY_STATUSES = (429, 500, 502, 503, 504)
RETRY_DELAY = 2.0


class ProviderError(RuntimeError):
    """A provider could not produce a usable quote."""


@dataclass
class Quote:
    """One instrument reading. All numeric fields are floats or None."""

    value: float | None = None
    change: float | None = None
    change_pct: float | None = None
    prev_close: float | None = None
    day_open: float | None = None
    day_high: float | None = None
    day_low: float | None = None
    year_high: float | None = None
    year_low: float | None = None
    as_of: str | None = None
    market_status: str = ""
    instrument_name: str = ""
    #: [[iso8601, value], …] — an intraday/daily series if the provider has one.
    series: list[list] = field(default_factory=list)
    #: Which raw key each field came from, so a wrong mapping is debuggable.
    field_map: dict = field(default_factory=dict)

    def derive_missing(self) -> "Quote":
        """Fill in change / change_pct / prev_close from whichever one or two are known.

        Sources are inconsistent about which of the three they publish, so the
        board reconstructs the rest rather than leaving a tile half-empty.
        """
        if self.value is None:
            return self
        if self.change is None and self.prev_close is not None:
            self.change = self.value - self.prev_close
        if self.prev_close is None and self.change is not None:
            self.prev_close = self.value - self.change
        if self.prev_close is None and self.change_pct not in (None, -100.0):
            self.prev_close = self.value / (1 + self.change_pct / 100.0)
            self.change = self.value - self.prev_close
        if self.change_pct is None and self.change is not None and self.prev_close:
            self.change_pct = self.change / abs(self.prev_close) * 100.0
        return self


# ── helpers ────────────────────────────────────────────────────────────────


def _get(url: str, *, headers: dict | None = None) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
    host = urllib.parse.urlsplit(url).netloc
    for attempt in (1, 2):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            if exc.code in RETRY_STATUSES and attempt == 1:
                time.sleep(RETRY_DELAY)
                continue
            hint = " (rate-limited — this host's IP is being throttled)" if exc.code == 429 else ""
            raise ProviderError(f"HTTP {exc.code} from {host}{hint}") from exc
        except Exception as exc:  # socket errors, proxy CONNECT denials, TLS failures
            raise ProviderError(f"{type(exc).__name__}: {exc}") from exc
    raise ProviderError(f"no response from {host}")  # unreachable; keeps the type checker happy


def _get_json(url: str, *, headers: dict | None = None):
    body = _get(url, headers=headers)
    try:
        return json.loads(body)
    except json.JSONDecodeError as exc:
        raise ProviderError(f"response was not JSON ({body[:80]!r}…)") from exc


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def _num(raw) -> float | None:
    """Parse CNBC-style numbers: '4.253', '+0.50%', '1,234.56', 'UNCH', ''."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    text = str(raw).strip().replace(",", "")
    if not text or text.upper() in {"UNCH", "N/A", "NA", "-", "--"}:
        return None
    match = _NUM.search(text)
    if not match:
        return None
    parsed = float(match.group())
    return -parsed if text.lstrip().startswith("-") and parsed > 0 else parsed


def _pick(payload: dict, candidates: tuple[str, ...], field_map: dict, name: str):
    """First candidate key that yields a number. Records which one won."""
    for key in candidates:
        if key in payload:
            value = _num(payload[key])
            if value is not None:
                field_map[name] = key
                return value
    return None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ── CNBC ───────────────────────────────────────────────────────────────────

CNBC_QUOTE_API = (
    "https://quote.cnbc.com/quote-html-webservice/restQuote/symbolType/symbol"
    "?symbols={symbol}&requestMethod=itv&noform=1&partnerId=2&fund=1"
    "&exthrs=1&output=json&events=1"
)

# CNBC's payload keys differ by instrument class (equity vs index vs bond vs
# future), so each field is looked up against the candidates seen in the wild,
# most specific first. `cockpit raw <key>` prints the untouched payload if a
# mapping ever needs correcting.
CNBC_FIELDS: dict[str, tuple[str, ...]] = {
    "value": ("last", "latest_yield", "bondlastprice", "mark_price", "previous_day_closing"),
    "change": ("change", "bondchange", "mark_change"),
    "change_pct": ("change_pct", "mark_change_pct"),
    "prev_close": ("previous_day_closing", "bondpreviousclose"),
    "day_open": ("open", "bondopen"),
    "day_high": ("high", "bondhigh"),
    "day_low": ("low", "bondlow"),
    "year_high": ("yrhiprice", "high_52week", "fiftyTwoWeekHigh"),
    "year_low": ("yrloprice", "low_52week", "fiftyTwoWeekLow"),
}


def _cnbc_records(payload) -> list[dict]:
    """Unwrap CNBC's variably-shaped envelope down to a list of quote dicts."""
    node = payload
    for key in ("FormattedQuoteResult", "QuickQuoteResult"):
        if isinstance(node, dict) and key in node:
            node = node[key]
            break
    if isinstance(node, dict):
        for key in ("FormattedQuote", "QuickQuote", "quote"):
            if key in node:
                node = node[key]
                break
    if isinstance(node, dict):
        node = [node]
    if not isinstance(node, list) or not node or not isinstance(node[0], dict):
        raise ProviderError("unrecognised CNBC payload shape")
    return node


def fetch_cnbc(tile) -> Quote:
    payload = _get_json(CNBC_QUOTE_API.format(symbol=urllib.parse.quote(tile.symbol, safe="")))
    record = _cnbc_records(payload)[0]

    quote = Quote()
    for name, candidates in CNBC_FIELDS.items():
        setattr(quote, name, _pick(record, candidates, quote.field_map, name))
    if quote.value is None:
        raise ProviderError(f"no price field in CNBC payload (keys: {sorted(record)[:12]})")

    quote.instrument_name = str(record.get("name") or record.get("shortName") or "")
    quote.market_status = str(record.get("curmktstatus") or "")
    quote.as_of = str(record.get("last_time") or record.get("last_time_msec") or "") or None
    quote.field_map["_symbol"] = str(record.get("symbol") or tile.symbol)
    return quote.derive_missing()


def raw_cnbc(symbol: str):
    """The untouched CNBC payload — for checking field names by eye."""
    return _get_json(CNBC_QUOTE_API.format(symbol=urllib.parse.quote(symbol, safe="")))


# ── Yahoo Finance ──────────────────────────────────────────────────────────

YAHOO_CHART_API = (
    "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    "?interval={interval}&range={range}"
)


def fetch_yahoo(tile) -> Quote:
    url = YAHOO_CHART_API.format(
        symbol=urllib.parse.quote(tile.symbol, safe=""), interval="5m", range="1d"
    )
    payload = _get_json(url)
    results = (payload.get("chart") or {}).get("result") or []
    if not results:
        error = (payload.get("chart") or {}).get("error")
        raise ProviderError(f"Yahoo returned no result ({error})")
    result = results[0]
    meta = result.get("meta") or {}

    quote = Quote(
        value=_num(meta.get("regularMarketPrice")),
        prev_close=_num(meta.get("chartPreviousClose") or meta.get("previousClose")),
        day_high=_num(meta.get("regularMarketDayHigh")),
        day_low=_num(meta.get("regularMarketDayLow")),
        year_high=_num(meta.get("fiftyTwoWeekHigh")),
        year_low=_num(meta.get("fiftyTwoWeekLow")),
        instrument_name=str(meta.get("shortName") or meta.get("symbol") or ""),
        market_status=str(meta.get("marketState") or ""),
    )
    if quote.value is None:
        raise ProviderError("Yahoo meta had no regularMarketPrice")
    stamp = _num(meta.get("regularMarketTime"))
    if stamp:
        quote.as_of = datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds")

    stamps = result.get("timestamp") or []
    closes = (((result.get("indicators") or {}).get("quote") or [{}])[0]).get("close") or []
    quote.series = [
        [datetime.fromtimestamp(t, timezone.utc).isoformat(timespec="seconds"), float(c)]
        for t, c in zip(stamps, closes)
        if c is not None
    ]
    if quote.series and quote.day_open is None:
        quote.day_open = quote.series[0][1]
    quote.field_map["_provider"] = "yahoo/chart"
    return quote.derive_missing()


# ── stooq ──────────────────────────────────────────────────────────────────

STOOQ_API = "https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"


def fetch_stooq(tile) -> Quote:
    body = _get(STOOQ_API.format(symbol=urllib.parse.quote(tile.symbol, safe="^"))).decode(
        "utf-8", "replace"
    )
    rows = list(csv.DictReader(io.StringIO(body)))
    if not rows:
        raise ProviderError("stooq returned no rows")
    row = rows[0]
    quote = Quote(
        value=_num(row.get("Close")),
        day_open=_num(row.get("Open")),
        day_high=_num(row.get("High")),
        day_low=_num(row.get("Low")),
        instrument_name=str(row.get("Symbol") or tile.symbol),
    )
    if quote.value is None:
        raise ProviderError(f"stooq gave no close for '{tile.symbol}' (row: {row})")
    date, clock = row.get("Date", ""), row.get("Time", "")
    if date and date != "N/D":
        quote.as_of = f"{date}T{clock or '00:00:00'}Z"
    quote.field_map["_provider"] = "stooq/csv"
    return quote.derive_missing()


# ── Frankfurter (ECB reference rates) ──────────────────────────────────────


def fetch_frankfurter(tile) -> Quote:
    try:
        base, target = tile.symbol.upper().replace("-", "/").split("/")
    except ValueError as exc:
        raise ProviderError(f"frankfurter symbol must look like 'EUR/USD', got '{tile.symbol}'") from exc

    latest = _get_json(f"https://api.frankfurter.app/latest?from={base}&to={target}")
    value = _num((latest.get("rates") or {}).get(target))
    if value is None:
        raise ProviderError(f"no {target} rate in frankfurter response")

    quote = Quote(value=value, as_of=str(latest.get("date") or "") or None,
                  instrument_name=f"{base}/{target}")
    history = _get_json(
        f"https://api.frankfurter.app/{_days_ago(30)}..?from={base}&to={target}"
    )
    quote.series = sorted(
        [day, float(rate[target])]
        for day, rate in (history.get("rates") or {}).items()
        if target in rate
    )
    if len(quote.series) >= 2:
        quote.prev_close = quote.series[-2][1]
        quote.year_high = max(point[1] for point in quote.series)
        quote.year_low = min(point[1] for point in quote.series)
    quote.field_map["_provider"] = "frankfurter/ecb"
    return quote.derive_missing()


def _days_ago(days: int) -> str:
    from datetime import timedelta

    return (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()


# ── FRED (St. Louis Fed) ───────────────────────────────────────────────────

# fredgraph.csv is the endpoint behind the download button on every FRED chart.
# It needs no API key, which is the whole reason this board uses it: one fewer
# secret to hold, and CI can call it with nothing configured.
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
#: How much history to keep for the sparkline. Three years of monthly prints.
FRED_POINTS = 36
#: Observations per year, for the year-on-year transform. Every series this
#: board reads is monthly; a quarterly one would need this to be 4.
FRED_PERIODS_PER_YEAR = 12


def _fred_observations(symbol: str) -> list[list]:
    """[[iso_date, value], …] oldest first, with FRED's missing marker dropped."""
    body = _get(
        FRED_CSV.format(series=urllib.parse.quote(symbol, safe="")),
        headers={"User-Agent": PLAIN_AGENT},
    ).decode("utf-8", "replace")
    rows = list(csv.reader(io.StringIO(body)))
    if len(rows) < 2:
        raise ProviderError(f"FRED returned no observations for '{symbol}'")
    # The date column has been called both DATE and observation_date; the value
    # column is named after the series. Take them positionally instead.
    points = []
    for row in rows[1:]:
        if len(row) < 2:
            continue
        value = _num(row[1])  # FRED writes "." for a missing observation
        if value is not None:
            points.append([row[0], value])
    if not points:
        raise ProviderError(f"every observation of '{symbol}' was missing")
    return points


def fetch_fred(tile) -> Quote:
    points = _fred_observations(tile.symbol)

    if tile.transform == "yoy":
        lag = FRED_PERIODS_PER_YEAR
        if len(points) < lag + 2:
            raise ProviderError(
                f"'{tile.symbol}' has {len(points)} observations, too few for a "
                f"year-on-year rate"
            )
        # Percent change against the observation a year earlier — the way a
        # headline inflation rate is quoted.
        rate = [[points[i][0], (points[i][1] / points[i - lag][1] - 1.0) * 100.0]
                for i in range(lag, len(points)) if points[i - lag][1]]
        quote = Quote(value=rate[-1][1], prev_close=rate[-2][1], as_of=rate[-1][0])
        quote.series = rate[-FRED_POINTS:]
        quote.field_map["_transform"] = f"yoy over {lag} observations"
    else:
        scaled = [[day, value * tile.scale] for day, value in points]
        quote = Quote(value=scaled[-1][1], as_of=scaled[-1][0])
        if len(scaled) >= 2:
            quote.prev_close = scaled[-2][1]
        quote.series = scaled[-FRED_POINTS:]

    quote.year_high = max(point[1] for point in quote.series)
    quote.year_low = min(point[1] for point in quote.series)
    quote.instrument_name = tile.symbol
    quote.field_map["_provider"] = "fred/fredgraph.csv"
    quote.field_map["_observations"] = str(len(points))
    return quote.derive_missing()


def raw_fred(symbol: str, rows: int = 15) -> str:
    """The tail of a FRED series as it arrives — for checking units by eye."""
    return "\n".join(f"{day},{value}" for day, value in _fred_observations(symbol)[-rows:])


# ── CoinGecko ──────────────────────────────────────────────────────────────


def fetch_coingecko(tile) -> Quote:
    payload = _get_json(
        "https://api.coingecko.com/api/v3/simple/price"
        f"?ids={urllib.parse.quote(tile.symbol)}&vs_currencies=usd"
        "&include_24hr_change=true&include_last_updated_at=true"
    )
    record = payload.get(tile.symbol)
    if not record:
        raise ProviderError(f"coingecko knows no coin id '{tile.symbol}'")

    quote = Quote(
        value=_num(record.get("usd")),
        change_pct=_num(record.get("usd_24h_change")),
        instrument_name=tile.symbol.replace("-", " ").title(),
    )
    if quote.value is None:
        raise ProviderError("coingecko returned no usd price")
    if quote.change_pct is not None:
        quote.prev_close = quote.value / (1 + quote.change_pct / 100.0)
        quote.change = quote.value - quote.prev_close
    stamp = _num(record.get("last_updated_at"))
    if stamp:
        quote.as_of = datetime.fromtimestamp(stamp, timezone.utc).isoformat(timespec="seconds")
    quote.field_map["_provider"] = "coingecko/simple"
    return quote


PROVIDERS = {
    "cnbc": fetch_cnbc,
    "fred": fetch_fred,
    "yahoo": fetch_yahoo,
    "stooq": fetch_stooq,
    "frankfurter": fetch_frankfurter,
    "coingecko": fetch_coingecko,
}
