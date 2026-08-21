# Finance Cockpit

A pinnable finance dashboard. You list the instruments you care about in
`sources.toml`; a script fetches them and bakes a single self-contained HTML
page; that page is published as an Artifact at a stable URL you can bookmark.
Refreshing rebuilds the page at the **same URL** — the link never changes.

```
sources.toml ──▶ python -m cockpit fetch ──▶ data/snapshot.json ──▶ dist/index.html ──▶ Artifact URL
                          │                          │
                    providers.py               data/history.csv
                  (cnbc, yahoo, …)          (one row per new reading,
                                             this is where sparklines
                                             come from)
```

## The published board

https://claude.ai/code/artifact/20c37e1b-d6ee-405a-84e5-4ddbe36a749a

Bookmark that. Republishing `dist/index.html` to this same URL updates the page
in place; publishing without it would create a *second*, unrelated artifact, so
always pass this URL when refreshing from a new conversation.

## Refreshing

```bash
./refresh.sh                 # fetch everything, rebuild the page
./refresh.sh --only us10y    # just one tile
./refresh.sh --offline       # skip the network, build from the inbox + cache
```

Or just ask Claude to *"refresh the cockpit"* — same pipeline, then republish.

## Adding a source

Add a `[[tile]]` block to `sources.toml` and refresh. `symbol` is the last path
segment of the provider's own quote URL — `^FVX` for
`https://finance.yahoo.com/quote/^FVX`, `US5Y` for
`https://www.cnbc.com/quotes/US5Y/`.

```toml
[[tile]]
key      = "ust5y"          # unique; also the name used in derived expressions
label    = "US 5-Year Treasury"
group    = "Rates"          # groups become sections, in first-appearance order
provider = "yahoo"
symbol   = "^FVX"
unit     = "%"
decimals = 3
good_when = "neutral"       # "up" (default) | "down" | "neutral"
```

`sources.toml` documents every field and every provider at the top of the file.
Bad config fails loudly at fetch time rather than producing a broken board.

### Derived tiles

A tile can be arithmetic over other tiles. Only numbers, tile keys and
`+ - * / **` are allowed — the expression never becomes executable code.

```toml
[[tile]]
key      = "curve_2s10s"
provider = "derived"
expr     = "(us10y - us2y) * 100"
unit     = " bp"
```

## Where each number comes from

Every tile resolves down a three-step ladder, and the board says on its face
which step won:

| Origin | Meaning |
|---|---|
| **live** | the provider answered over HTTP during this build |
| **relayed** | the provider was unreachable, so the reading came from `data/agent-inbox.json` |
| **cached** | nothing fresh was available; the previous value was carried forward |
| **no data** | no value at all — the tile says so instead of showing a stale number |

A tile also carries two different timestamps, which are not the same thing:
**As of** is what the market says the reading is for, and **Read** is when this
board obtained it. Tiles unread for longer than `stale_after_minutes` are drawn
with a dashed border.

### What each provider can actually reach

Which providers answer is a property of **where the build runs**, not of the
code. The two places this board builds see almost opposite worlds — measured
2026-08-21, and the probe step in `.github/workflows/refresh.yml` re-measures
it on every CI run:

| Provider | Host | GitHub runner | Claude sandbox |
|---|---|---|---|
| cnbc | `quote.cnbc.com` | **200** | **403** — Akamai bot-block |
| coingecko | `api.coingecko.com` | **200** | **200** |
| yahoo | `query1.finance.yahoo.com` | **429** | **429** — throttled by IP |
| frankfurter | `api.frankfurter.app` | 301 → `.dev`, followed | 301 → `.dev`, **not allowed** |
| stooq | `stooq.com` | **404** | **404** |

Hence `alt_provider`: each tile leads with CNBC, which works in CI where the
schedule runs, and falls back to Yahoo, which is what a Claude session can
reach. Yahoo answers `WebFetch` normally even though it throttles both hosts
directly, which is what makes the relay path work at all.

### The relay path

Some hosts cannot reach market-data providers — a corporate proxy, or a sandbox
with a restrictive egress policy. Rather than failing, the pipeline reads
`data/agent-inbox.json`, where Claude (or you) can drop observed readings:

```bash
python3 -m cockpit inbox > data/agent-inbox.json   # print a skeleton to fill in
./refresh.sh --offline                             # build from it
```

Each entry needs a `value`; `change`, `change_pct` and `prev_close` are
reconstructed from whichever one you supply. Relayed tiles are labelled as such
on the board, with a per-tile note naming the source — the board never passes a
relayed number off as a direct quote.

## Scheduled refresh

The board is a static page baked at build time — it does not poll. It changes
only when something runs `./refresh.sh` and republishes the result.

`.github/workflows/refresh.yml` does the fetching, on GitHub's runners, for
free: weekdays at 08:00, 15:00 and 22:30 Zurich — the European open, just
before the US open, and after the US close. Each run probes the providers,
refreshes, and commits `data/` and `dist/` only if more than CoinGecko (which
answers from anywhere) came back live, so a run that reached nothing leaves
the board alone rather than overwriting it with a rebuild of the same inbox.

Two things to know about that schedule:

- **GitHub only runs scheduled workflows from the default branch.** Until this
  workflow is merged there, the cron entries are inert; the run log is reachable
  by pushing a change to the workflow file, which is what the `push` trigger is
  for.
- **Cron fires in UTC.** The three entries read `0 6`, `0 13` and `30 20` while
  Switzerland is on CEST. Shift them to `0 7`, `0 14` and `30 21` when the
  clocks go back at the end of October.

### What CI cannot do

Republish the Artifact. That URL can only be written by Claude's Artifact tool,
so CI keeps `dist/index.html` and the history current in git, and the published
page updates when you ask Claude to refresh the cockpit. If you would rather
have a free self-updating URL, serve `dist/index.html` from GitHub Pages and
bookmark that instead.

### Refreshing from a Claude session

CNBC refuses the sandbox, so a session cannot fetch most tiles directly. The
runbook is: try `./refresh.sh` first and keep whatever comes back live; for
each tile that did not resolve, `WebFetch`
`https://query1.finance.yahoo.com/v8/finance/chart/<alt_symbol>?interval=5m&range=1d`
and write `meta.regularMarketPrice`, `chartPreviousClose`,
`regularMarketDayHigh` / `Low`, `fiftyTwoWeekHigh` / `Low` and
`regularMarketTime` into `data/agent-inbox.json` (`as_of` is that epoch as
ISO-8601 UTC); then `./refresh.sh` again, republish `dist/index.html` to the
artifact URL, and commit.

The one rule that matters: **never invent a number.** A tile with no reachable
source is meant to go relayed, cached, or blank — that is what the origin
ladder is for.

## History and sparklines

Every refresh appends new readings to `data/history.csv`, and the sparklines are
drawn from it. A reading is only recorded if its **market timestamp** is new, so
rebuilding twice off the same data does not invent a trend. Providers that
return their own intraday series (`yahoo`, `frankfurter`) draw a real line from
the first build; the rest fill in as the board is refreshed over time.

Keep `data/history.csv` in git — it is the only record of everything the board
has ever seen.

## Commands

| | |
|---|---|
| `python3 -m cockpit refresh` | fetch, then rebuild the page |
| `python3 -m cockpit fetch` | fetch only |
| `python3 -m cockpit build` | rebuild the page from the current snapshot |
| `python3 -m cockpit status` | print the last snapshot as a table |
| `python3 -m cockpit inbox` | print an `agent-inbox.json` skeleton |
| `python3 -m cockpit raw US10Y` | dump a provider's untouched payload |

## Tests

```bash
python3 -m unittest discover -s tests -v
```

The provider tests run against recorded payload *shapes*, not the live
endpoints, so they stay meaningful on a host with no market-data access.

## Notes on the providers

Most tiles are on `yahoo`, whose chart API carries a price, a previous close, day
and 52-week ranges and an intraday series in one response. Bitcoin is on
`coingecko`. The 2-year Treasury is the awkward one: Yahoo has no 2-year yield
index (its Treasury indices are `^IRX`, `^FVX`, `^TNX`, `^TYX`) and the 2-year
yield future `2YY=F` quotes an expired contract, so that tile stays on CNBC and
therefore on the relay path — it is the oldest number on the board, and the
2s10s curve inherits its age.

`providers.py` looks each CNBC field up against a list of candidate keys, because
CNBC's payload differs by instrument class (equity vs index vs bond vs future).
If a tile ever shows the wrong number, `python3 -m cockpit raw <SYMBOL>` prints
the untouched payload, and every fetched quote records which key it actually
read in `field_map` in `data/snapshot.json`. Adjust `CNBC_FIELDS` to match.

A provider that answers 429 or 5xx is retried once, two seconds later, and then
given up on — with a whole build hitting the same throttled host at once, more
attempts only make a failing refresh slower.

Only the Python standard library is used, so there is nothing to install.
`urllib` honours `HTTPS_PROXY` and `SSL_CERT_FILE`, which is what lets the
fetcher work from behind a proxy that terminates TLS.
