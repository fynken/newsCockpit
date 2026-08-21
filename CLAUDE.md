# Working on the Finance Cockpit

## Branching: there isn't any

Commit straight to `main` and push. No feature branches, no pull requests —
the owner asked for this explicitly on 2026-08-21. `main` is what CI builds
from and what the schedule follows, so anything not on `main` is invisible to
the board.

## Where this code actually runs, and why it matters

The board is built in two very different places, and they reach different
hosts. Nothing about this is guessable from the code, so **measure, don't
reason** — and measure on a runner, because the Claude sandbox is the more
restricted of the two:

| | GitHub runner | Claude sandbox |
|---|---|---|
| `quote.cnbc.com` | 200 | 403, Akamai bot-block |
| `fred.stlouisfed.org` | 200, but only for a non-browser user agent | blocked entirely |
| `api.coingecko.com` | 200 | 200 |
| `query1.finance.yahoo.com` | 429 | 429 (but `WebFetch` reaches it) |

Two consequences worth remembering before touching a provider:

- **User agents are per-provider, deliberately.** CNBC wants the Chrome
  string; FRED hangs on it and answers `PLAIN_AGENT` instantly. There is no
  global right answer, and a test pins the FRED side.
- **A data change cannot be verified here.** Push it and read the CI run —
  the workflow's probe steps print provider reachability and each FRED
  series' own header into the run summary for exactly this reason.

## The rule that outranks the others

**Never invent a number.** A tile with no reachable source goes relayed,
cached, or blank — that is what the origin ladder is for, and the board says
on its face which step won. Filling a gap with a plausible figure is the one
change that makes this whole thing worthless.

## Before pushing

- `python3 -m unittest discover -s tests`
- If `.github/workflows/` changed: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/refresh.yml'))"`.
  A broken workflow has already shipped once — YAML block scalars end at the
  first unindented line, which multi-line `python3 -c` snippets will do to you.
- CI commits refreshed data back to `main`, so `git pull` before you push or
  you will collide with it.

## Publishing

`dist/index.html` goes to the Artifact at the URL in README.md — always pass
that URL, or you create a second, unrelated artifact. CI cannot do this step;
it is the one thing that still needs a Claude session.
