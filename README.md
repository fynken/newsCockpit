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

## Refreshing

```bash
./refresh.sh                 # fetch everything, rebuild the page
./refresh.sh --only us10y    # just one tile
./refresh.sh --offline       # skip the network, build from the inbox + cache
```

Or just ask Claude to *"refresh the cockpit"* — same pipeline, then republish.

## Adding a source

Add a `[[tile]]` block to `sources.toml` and refresh. For anything on CNBC,
`symbol` is the last path segment of its quote URL — `US10Y` for
`https://www.cnbc.com/quotes/US10Y/`.

```toml
[[tile]]
key      = "ust5y"          # unique; also the name used in derived expressions
label    = "US 5-Year Treasury"
group    = "Rates"          # groups become sections, in first-appearance order
provider = "cnbc"
symbol   = "US5Y"
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

`providers.py` looks each field up against a list of candidate keys, because
CNBC's payload differs by instrument class (equity vs index vs bond vs future).
If a tile ever shows the wrong number, `python3 -m cockpit raw <SYMBOL>` prints
the untouched payload, and every fetched quote records which key it actually
read in `field_map` in `data/snapshot.json`. Adjust `CNBC_FIELDS` to match.

Only the Python standard library is used, so there is nothing to install.
`urllib` honours `HTTPS_PROXY` and `SSL_CERT_FILE`, which is what lets the
fetcher work from behind a proxy that terminates TLS.
