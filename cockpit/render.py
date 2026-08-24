"""Render data/snapshot.json into a single self-contained HTML page.

Everything the page needs is inlined, because the published board runs under a
strict CSP that blocks external requests — Google Fonts is the one exception.
That is also why the numbers are baked in at build time rather than fetched by
the page: a refresh is a rebuild.
"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from . import config
from .style import CSS, FONT_LINK

ORIGIN_LABEL = {
    "live": "live",
    "manual": "relayed",
    "cache": "cached",
    "missing": "no data",
}
ORIGIN_BLURB = {
    "live": "fetched directly from the provider",
    "manual": "relayed by Claude — the provider was unreachable from the build host",
    "cache": "carried over from the last successful fetch",
    "missing": "no value available",
}


def esc(text) -> str:
    return html.escape(str(text if text is not None else ""), quote=True)


# ── formatting ─────────────────────────────────────────────────────────────


def fmt_number(value, decimals: int) -> str:
    if value is None:
        return "—"
    return f"{value:,.{decimals}f}"


def fmt_value(record) -> str:
    return fmt_number(record.get("value"), record.get("decimals", 2))


def direction(record) -> str:
    change = record.get("change")
    if change is None or abs(change) < 10 ** -(record.get("decimals", 2) + 2):
        return "flat"
    return "up" if change > 0 else "down"


def delta_class(record) -> str:
    """Direction to colour class, honouring good_when."""
    way = direction(record)
    if way == "flat":
        return "flat"
    if record.get("good_when") == "neutral":
        return "flat"
    if record.get("good_when") == "down":
        return "down" if way == "up" else "up"
    return way


def delta_html(record) -> str:
    if record.get("value") is None or record.get("change") is None:
        return ""
    way = direction(record)
    arrow = {"up": "▲", "down": "▼", "flat": "■"}[way]
    decimals = record.get("decimals", 2)
    change = record["change"]
    signed = f"{change:+,.{decimals}f}"
    pct = record.get("change_pct")
    pct_html = f'<span class="pct">{pct:+.2f}%</span>' if pct is not None else ""
    return (
        f'<span class="delta delta--{delta_class(record)}">'
        f'<span class="arrow" aria-hidden="true">{arrow}</span>'
        f'<span>{esc(signed)}</span>{pct_html}</span>'
    )


def local_time(iso: str | None, tz: ZoneInfo, fmt: str = "%d %b %Y · %H:%M %Z") -> str:
    if not iso:
        return "—"
    text = str(iso).strip()
    for candidate in (text, text.replace("Z", "+00:00")):
        try:
            stamp = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=ZoneInfo("UTC"))
        return stamp.astimezone(tz).strftime(fmt)
    return esc(text)


# ── components ─────────────────────────────────────────────────────────────


def sparkline(record, *, height: int = 42) -> str:
    """A 2px line with a soft area fill and an emphasised endpoint."""
    series = [point for point in (record.get("series") or []) if point[1] is not None]
    if len(series) < 2:
        return (
            '<div class="spark-empty" role="img" aria-label="No trend line yet — '
            f'{len(series)} reading recorded for {esc(record["label"])}"></div>'
        )

    series = series[-90:]
    values = [float(point[1]) for point in series]
    low, high = min(values), max(values)
    span = (high - low) or (abs(high) or 1) * 0.001
    width, pad = 100.0, 3.0
    inner = height - pad * 2

    def x(index: int) -> float:
        return index / (len(values) - 1) * width

    def y(value: float) -> float:
        return pad + (1 - (value - low) / span) * inner

    points = [(x(i), y(v)) for i, v in enumerate(values)]
    line = "M " + " L ".join(f"{px:.2f} {py:.2f}" for px, py in points)
    area = f"{line} L {width:.2f} {height} L 0 {height} Z"
    end_x, end_y = points[-1]

    decimals = record.get("decimals", 2)
    tip_data = [
        [str(point[0]), fmt_number(float(point[1]), decimals)] for point in series
    ]
    gradient_id = f"g-{esc(record['key'])}"

    return (
        f'<div class="spark-wrap" tabindex="0" role="img" '
        f'aria-label="Trend of {esc(record["label"])}: '
        f'{fmt_number(values[0], decimals)} to {fmt_number(values[-1], decimals)} '
        f'over the last {len(values)} readings" '
        f"data-points='{html.escape(json.dumps(tip_data), quote=True)}' "
        f'data-prefix="{esc(record.get("prefix", ""))}" data-unit="{esc(record.get("unit", ""))}">'
        f'<svg class="spark" viewBox="0 0 {width:.0f} {height}" preserveAspectRatio="none" '
        f'aria-hidden="true" focusable="false">'
        f"<defs><linearGradient id=\"{gradient_id}\" x1=\"0\" y1=\"0\" x2=\"0\" y2=\"1\">"
        f'<stop offset="0%" stop-color="var(--accent)" stop-opacity="0.20"/>'
        f'<stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/>'
        f"</linearGradient></defs>"
        f'<path d="{area}" fill="url(#{gradient_id})"/>'
        f'<path d="{line}" fill="none" stroke="var(--accent)" stroke-width="2" '
        f'stroke-linecap="round" stroke-linejoin="round" vector-effect="non-scaling-stroke"/>'
        f'<circle class="spark-cursor" r="3" fill="var(--accent)" stroke="var(--surface)" '
        f'stroke-width="2" opacity="0" vector-effect="non-scaling-stroke"/>'
        f'<circle cx="{end_x:.2f}" cy="{end_y:.2f}" r="3" fill="var(--accent)" '
        f'stroke="var(--surface)" stroke-width="2" vector-effect="non-scaling-stroke"/>'
        f"</svg><div class=\"spark-tip\" aria-hidden=\"true\"></div></div>"
    )


def gauge(record, key: str, low_field: str, high_field: str, label: str) -> str:
    low, high, value = record.get(low_field), record.get(high_field), record.get("value")
    if low is None or high is None or value is None or high <= low:
        return ""
    position = max(0.0, min(1.0, (value - low) / (high - low))) * 100
    decimals = record.get("decimals", 2)
    return (
        f'<div class="gauge">'
        f'<span class="gauge-key">{esc(label)}</span>'
        f'<span class="gauge-track" role="img" aria-label="{esc(label)} range '
        f'{fmt_number(low, decimals)} to {fmt_number(high, decimals)}, '
        f'now at {position:.0f} percent of range">'
        f'<span class="gauge-fill" style="width:{position:.2f}%"></span>'
        f'<span class="gauge-mark" style="left:{position:.2f}%"></span>'
        f"</span>"
        f'<span class="gauge-bounds">{fmt_number(low, decimals)} – {fmt_number(high, decimals)}</span>'
        f"</div>"
    )


def gauges_html(record) -> str:
    parts = [
        gauge(record, "day", "day_low", "day_high", "Day"),
        gauge(record, "yr", "year_low", "year_high", "52w"),
    ]
    parts = [part for part in parts if part]
    return f'<div class="gauges">{"".join(parts)}</div>' if parts else ""


def foot_stats(record, tz: ZoneInfo) -> str:
    decimals = record.get("decimals", 2)
    cells = [
        f'<span class="stat">{esc(name)} <b>{fmt_number(value, decimals)}</b></span>'
        for name, value in (("Prev", record.get("prev_close")), ("Open", record.get("day_open")))
        if value is not None
    ]
    if record.get("value") is not None and record.get("change") is None:
        cells.append('<span class="stat">No prior close published</span>')
    if record.get("as_of"):
        stamped = local_time(record["as_of"], tz, "%d %b %H:%M")
        cells.append(f'<span class="stat">As of <b>{esc(stamped)}</b></span>')
    return f'<div class="tile-foot">{"".join(cells)}</div>' if cells else ""


def tile_html(record, tz: ZoneInfo, *, featured: bool = False) -> str:
    origin = record.get("origin", "missing")
    classes = ["tile"]
    if featured:
        classes.append("tile--featured")
    if record.get("stale") and origin != "missing":
        classes.append("tile--stale")
    if record.get("value") is None:
        classes.append("tile--missing")

    edge = {
        "live": "transparent",
        "manual": "var(--accent)",
        "cache": "var(--muted)",
        "missing": "var(--down)",
    }[origin]

    url = record.get("url") or ""
    symbol = record.get("symbol") or record.get("expr") or ""
    source_line = esc(symbol)
    if url:
        source_line = f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(symbol)} ↗</a>'

    head = (
        f'<div class="tile-head"><div>'
        f'<p class="tile-label">{esc(record["label"])}</p>'
        f'<div class="tile-sym">{source_line}</div>'
        f"</div>"
        f'<span class="pill" title="{esc(ORIGIN_BLURB[origin])}">'
        f'<span class="dot dot--{origin}"></span>{esc(ORIGIN_LABEL[origin])}</span>'
        f"</div>"
    )

    unit = record.get("unit") or ""
    prefix = record.get("prefix") or ""
    value_row = (
        f'<div class="tile-value-row">'
        f'<span class="tile-value readout">{esc(prefix)}{fmt_value(record)}'
        f'<span class="tile-unit">{esc(unit)}</span></span>'
        f"{delta_html(record)}</div>"
    )

    flags = []
    if record.get("value") is None:
        flags.append(
            f'<p class="tile-flag tile-flag--bad">Nothing to show. '
            f'{esc(record.get("error") or "the source did not respond")}</p>'
        )
    elif origin == "manual" and record.get("source_note"):
        flags.append(f'<p class="tile-flag">{esc(record["source_note"])}</p>')
    elif origin == "cache":
        flags.append(
            f'<p class="tile-flag">Carried over from '
            f"{esc(local_time(record.get('fetched_at'), tz))} — the source did not respond "
            f"on this run.</p>"
        )
    elif record.get("stale"):
        flags.append(
            f'<p class="tile-flag">Last read {esc(local_time(record.get("fetched_at"), tz))}.</p>'
        )

    body = [head, value_row]
    if record.get("value") is not None:
        body.append(sparkline(record))
        body.append(gauges_html(record))
        body.append(foot_stats(record, tz))
    body.extend(flags)

    return (
        f'<article class="{" ".join(classes)}" style="--edge:{edge}">'
        + "".join(part for part in body if part)
        + "</article>"
    )


def featured_html(record, tz: ZoneInfo) -> str:
    origin = record.get("origin", "missing")
    unit = record.get("unit") or ""
    prefix = record.get("prefix") or ""
    url = record.get("url") or ""
    symbol = record.get("symbol") or ""
    link = (
        f'<a href="{esc(url)}" target="_blank" rel="noopener">{esc(symbol)} ↗</a>'
        if url else esc(symbol)
    )

    blurbs = [record.get("note"), record.get("source_note")]
    note = "".join(
        f'<p class="featured-note">{esc(text)}</p>' for text in blurbs if text
    )
    read_line = (
        f'<div class="tile-sym">Read {esc(local_time(record.get("fetched_at"), tz))} · '
        f'{esc(ORIGIN_BLURB[origin])}</div>'
    )

    points = [point for point in (record.get("series") or []) if point[1] is not None]
    has_trend = len(points) >= 2
    # With no trend line there is nothing to fill a second column with, so the
    # hero tile runs full width and its metadata sits on one line underneath.
    layout = "tile featured" if has_trend else "tile featured featured--solo"

    return (
        f'<article class="{layout}" style="--edge:'
        + {"live": "transparent", "manual": "var(--accent)",
           "cache": "var(--muted)", "missing": "var(--down)"}[origin]
        + '">'
        f'<div class="featured-left">'
        f'<div class="tile-head"><div>'
        f'<p class="tile-label">{esc(record["label"])}</p>'
        f'<div class="tile-sym">{link}</div></div>'
        f'<span class="pill" title="{esc(ORIGIN_BLURB[origin])}">'
        f'<span class="dot dot--{origin}"></span>{esc(ORIGIN_LABEL[origin])}</span></div>'
        f'<div class="tile-value-row">'
        f'<span class="tile-value readout">{esc(prefix)}{fmt_value(record)}'
        f'<span class="tile-unit">{esc(unit)}</span></span></div>'
        f"{delta_html(record)}"
        f"{note}"
        f"</div>"
        f'<div class="featured-right">'
        f"{sparkline(record, height=116) if has_trend else ''}"
        f"{gauges_html(record)}"
        f"{foot_stats(record, tz)}"
        f"{read_line}"
        f"</div></article>"
    )


def briefing_html(briefing: dict, tz: ZoneInfo) -> str:
    """The headline strip. Every line is a headline an outlet published,
    verbatim and linked; nothing here is written by this board."""
    stories = (briefing or {}).get("stories") or []
    if not stories:
        return ""

    captured = (briefing or {}).get("captured_at")
    read = (briefing or {}).get("sources_read") or []
    bullets = []
    for story in stories:
        sources = " · ".join(esc(name) for name in story.get("sources", []))
        corroborated = ' class="brief-sources brief-sources--multi"' if len(
            story.get("sources", [])) > 1 else ' class="brief-sources"'
        when = local_time(story.get("published"), tz, fmt="%H:%M")
        bullets.append(
            '<li class="brief-item">'
            f'<a class="brief-headline" href="{esc(story.get("url", ""))}" '
            f'target="_blank" rel="noopener">{esc(story.get("headline", ""))}</a>'
            f'<span{corroborated}>{sources}</span>'
            f'<span class="brief-when">{esc(when)}</span>'
            "</li>"
        )

    stamp = local_time(captured, tz)
    return (
        '<section class="briefing">'
        '<div class="section-head"><h2 class="signage">Business briefing</h2>'
        '<span class="rule"></span>'
        f'<span class="count">{len(read)} outlets · {esc(stamp)}</span></div>'
        f'<ul class="brief-list">{"".join(bullets)}</ul>'
        '<p class="brief-note">Headlines as published, deduplicated across '
        'outlets and ranked by how many ran the story. Nothing here is '
        'rewritten or summarised — follow a link for the article.</p>'
        "</section>"
    )


def table_html(tiles, tz: ZoneInfo) -> str:
    rows = []
    for record in tiles:
        decimals = record.get("decimals", 2)
        change = record.get("change")
        change_text = fmt_number(change, decimals) if change is None else f"{change:+,.{decimals}f}"
        pct = record.get("change_pct")
        pct_text = "—" if pct is None else f"{pct:+.2f}%"
        css = delta_class(record)
        css_class = f' class="{css}"' if css in ("up", "down") else ""
        rows.append(
            f"<tr><td>{esc(record['label'])}</td>"
            f"<td>{esc(record.get('symbol') or record.get('expr') or '—')}</td>"
            f"<td>{esc(record.get('prefix') or '')}{fmt_value(record)}{esc(record.get('unit') or '')}</td>"
            f"<td{css_class}>{esc(change_text)}</td>"
            f"<td{css_class}>{esc(pct_text)}</td>"
            f"<td>{fmt_number(record.get('day_low'), decimals)} – "
            f"{fmt_number(record.get('day_high'), decimals)}</td>"
            f"<td>{fmt_number(record.get('year_low'), decimals)} – "
            f"{fmt_number(record.get('year_high'), decimals)}</td>"
            f"<td>{esc(ORIGIN_LABEL[record.get('origin', 'missing')])}</td>"
            f"<td>{esc(local_time(record.get('fetched_at'), tz, '%d %b %H:%M'))}</td></tr>"
        )
    return (
        '<details class="ledger" id="ledger"><summary>All values as a table</summary>'
        '<div class="table-scroll"><table>'
        "<thead><tr><th>Instrument</th><th>Symbol</th><th>Last</th><th>Change</th>"
        "<th>%</th><th>Day range</th><th>52-week range</th><th>Source</th><th>Read</th>"
        "</tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></div></details>"
    )


SCRIPT = """
(function () {
  document.querySelectorAll('.spark-wrap').forEach(function (wrap) {
    var points;
    try { points = JSON.parse(wrap.dataset.points || '[]'); } catch (e) { return; }
    if (points.length < 2) return;
    var tip = wrap.querySelector('.spark-tip');
    var cursor = wrap.querySelector('.spark-cursor');
    var svg = wrap.querySelector('.spark');
    var prefix = wrap.dataset.prefix || '';
    var unit = wrap.dataset.unit || '';

    function show(index, x) {
      var point = points[index];
      if (!point) return;
      var when = point[0];
      var parsed = new Date(when.length === 10 ? when + 'T00:00:00Z' : when);
      var stamped = isNaN(parsed) ? when : parsed.toLocaleString(undefined, {
        month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'
      });
      tip.textContent = prefix + point[1] + unit + '  ·  ' + stamped;
      tip.style.left = x + 'px';
      wrap.classList.add('is-hovered');
      var box = svg.getBoundingClientRect();
      var path = svg.querySelector('path + path');
      var length = path.getTotalLength();
      var at = path.getPointAtLength(length * (index / (points.length - 1)));
      cursor.setAttribute('cx', at.x);
      cursor.setAttribute('cy', at.y);
      cursor.setAttribute('opacity', '1');
      tip.style.top = (at.y / svg.viewBox.baseVal.height) * box.height + 'px';
    }

    function hide() {
      wrap.classList.remove('is-hovered');
      cursor.setAttribute('opacity', '0');
    }

    wrap.addEventListener('pointermove', function (event) {
      var box = wrap.getBoundingClientRect();
      var ratio = Math.min(1, Math.max(0, (event.clientX - box.left) / box.width));
      show(Math.round(ratio * (points.length - 1)), ratio * box.width);
    });
    wrap.addEventListener('pointerleave', hide);
    wrap.addEventListener('blur', hide);
    wrap.addEventListener('focus', function () {
      var box = wrap.getBoundingClientRect();
      show(points.length - 1, box.width);
    });
  });

  var ledger = document.getElementById('ledger');
  if (ledger) {
    try {
      if (localStorage.getItem('cockpit.ledger') === 'open') ledger.open = true;
    } catch (e) { /* storage blocked — the default closed state is fine */ }
    ledger.addEventListener('toggle', function () {
      try { localStorage.setItem('cockpit.ledger', ledger.open ? 'open' : 'closed'); }
      catch (e) { /* nothing to do */ }
    });
  }
})();
"""


def render(snapshot: dict) -> str:
    try:
        tz = ZoneInfo(snapshot.get("timezone") or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        tz = ZoneInfo("UTC")

    tiles = snapshot["tiles"]
    featured = next((t for t in tiles if t.get("featured") and t.get("value") is not None), None)
    rest = [t for t in tiles if t is not featured]

    counts = {name: 0 for name in ORIGIN_LABEL}
    for record in tiles:
        counts[record.get("origin", "missing")] = counts.get(record.get("origin", "missing"), 0) + 1
    summary = "".join(
        f'<span class="pill"><span class="dot dot--{origin}"></span>'
        f"{count} {esc(ORIGIN_LABEL[origin])}</span>"
        for origin, count in counts.items()
        if count
    )

    sections = []
    for group in snapshot.get("groups", []):
        members = [t for t in rest if t.get("group") == group]
        if not members:
            continue
        sections.append(
            f'<section class="section"><div class="section-head">'
            f'<h2 class="signage">{esc(group)}</h2><span class="rule"></span>'
            f'<span class="count">{len(members)}</span></div>'
            f'<div class="grid">{"".join(tile_html(t, tz) for t in members)}</div></section>'
        )

    sources = sorted({
        (record.get("url") or "", record.get("provider", ""))
        for record in tiles if record.get("url")
    })
    source_items = "".join(
        f'<li><a href="{esc(url)}" target="_blank" rel="noopener">'
        f"{esc(url.replace('https://', '').rstrip('/'))}</a></li>"
        for url, _ in sources[:14]
    )

    legend = "".join(
        f'<span><span class="dot dot--{origin}"></span>'
        f"<b>{esc(ORIGIN_LABEL[origin])}</b> — {esc(ORIGIN_BLURB[origin])}</span>"
        for origin in ORIGIN_LABEL
    )

    stale_minutes = snapshot.get("stale_after_minutes", 90)
    generated = local_time(snapshot.get("generated_at"), tz)

    return f"""<title>{esc(snapshot.get('title', 'Finance Cockpit'))}</title>
{FONT_LINK}
<style>{CSS}</style>
<div class="shell">
  <header class="masthead">
    <div>
      <h1>{esc(snapshot.get('title', 'Finance Cockpit'))}</h1>
      <p class="tagline">{esc(snapshot.get('tagline', ''))}</p>
    </div>
    <div class="masthead-right">
      <div class="stamp">
        <span class="signage">Built</span>
        <span class="value">{esc(generated)}</span>
      </div>
      <div class="origin-summary">{summary}</div>
    </div>
  </header>

  {briefing_html(snapshot.get("briefing"), tz)}

  {featured_html(featured, tz) if featured else ""}

  {"".join(sections)}

  {table_html(tiles, tz)}

  <footer class="colophon">
    <div>
      <h2>Where each number came from</h2>
      <div class="legend">{legend}</div>
    </div>
    <div>
      <h2>Check a number by hand</h2>
      <ul>{source_items}</ul>
    </div>
    <div>
      <h2>Refreshing</h2>
      <p style="margin:0;max-width:34ch">Tiles without a trend line have only one
      reading so far; the line fills in as the board is refreshed. Values are baked
      in at build time, so the
      board shows the state of the market when it was last built — not a live feed.
      Run <code>./refresh.sh</code>, or ask Claude to refresh the cockpit, and this
      same URL updates in place. Tiles unread for more than {esc(stale_minutes)}
      minutes are drawn with a dashed border.</p>
    </div>
  </footer>
</div>
<script>{SCRIPT}</script>
"""


def build(snapshot: dict | None = None, out: Path | None = None) -> Path:
    if snapshot is None:
        snapshot = json.loads(config.SNAPSHOT_FILE.read_text(encoding="utf-8"))
    out = out or (config.DIST_DIR / "index.html")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(snapshot), encoding="utf-8")
    return out
