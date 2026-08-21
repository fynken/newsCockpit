"""The cockpit's design tokens and stylesheet.

Palette: cool slate neutrals with a gauge-blue accent, direction marks in
emerald/red. The direction pair (#1baf7a / #d03b3b) was checked with the
data-viz palette validator and clears the CVD, lightness-band and normal-vision
gates in both modes; it is mode-invariant, like a status palette. Direction is
never carried by colour alone — every delta ships an arrow glyph and a signed
number, and the whole board is available as a table.

Type: Archivo for signage (masthead, group headers, tile labels), IBM Plex Mono
for every readout, IBM Plex Sans for prose. Instrument-panel vernacular:
machine-set numerals that hold their width from one refresh to the next.
"""

FONT_LINK = (
    '<link rel="preconnect" href="https://fonts.googleapis.com">\n'
    '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>\n'
    '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
    "family=Archivo:wght@500;600;700&"
    "family=IBM+Plex+Mono:wght@400;500;600&"
    "family=IBM+Plex+Sans:wght@400;500&display=swap\">"
)

CSS = """
:root {
  color-scheme: light;
  --plane:      #f2f4f7;
  --surface:    #ffffff;
  --surface-2:  #f7f9fb;
  --ink:        #0f1620;
  --ink-2:      #4d5763;
  --muted:      #6b7788;
  --hairline:   #dde3ea;
  --hairline-2: #eaeef3;
  --accent:     #2f5fd0;
  --accent-soft: rgba(47, 95, 208, 0.12);
  --up:         #1baf7a;
  --down:       #d03b3b;
  --up-text:    #0a7d55;
  --down-text:  #b3302f;
  --up-wash:    rgba(27, 175, 122, 0.14);
  --down-wash:  rgba(208, 59, 59, 0.13);
  --shadow:     0 1px 2px rgba(15, 22, 32, 0.05), 0 8px 24px -16px rgba(15, 22, 32, 0.28);
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    color-scheme: dark;
    --plane:      #0c1016;
    --surface:    #151b24;
    --surface-2:  #1b222d;
    --ink:        #f2f5f8;
    --ink-2:      #a6b1bf;
    --muted:      #78838f;
    --hairline:   #263040;
    --hairline-2: #1e2632;
    --accent:     #5b8ef0;
    --accent-soft: rgba(91, 142, 240, 0.16);
    --up:         #1baf7a;
    --down:       #d03b3b;
    --up-text:    #3ddc9a;
    --down-text:  #f08a8a;
    --up-wash:    rgba(27, 175, 122, 0.18);
    --down-wash:  rgba(208, 59, 59, 0.20);
    --shadow:     0 1px 2px rgba(0, 0, 0, 0.4), 0 8px 24px -18px rgba(0, 0, 0, 0.8);
  }
}
:root[data-theme="dark"] {
  color-scheme: dark;
  --plane:      #0c1016;
  --surface:    #151b24;
  --surface-2:  #1b222d;
  --ink:        #f2f5f8;
  --ink-2:      #a6b1bf;
  --muted:      #78838f;
  --hairline:   #263040;
  --hairline-2: #1e2632;
  --accent:     #5b8ef0;
  --accent-soft: rgba(91, 142, 240, 0.16);
  --up:         #1baf7a;
  --down:       #d03b3b;
  --up-text:    #3ddc9a;
  --down-text:  #f08a8a;
  --up-wash:    rgba(27, 175, 122, 0.18);
  --down-wash:  rgba(208, 59, 59, 0.20);
  --shadow:     0 1px 2px rgba(0, 0, 0, 0.4), 0 8px 24px -18px rgba(0, 0, 0, 0.8);
}

*, *::before, *::after { box-sizing: border-box; }

body {
  margin: 0;
  background: var(--plane);
  color: var(--ink);
  font-family: "IBM Plex Sans", ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif;
  font-size: 15px;
  line-height: 1.5;
  -webkit-font-smoothing: antialiased;
}

.shell {
  max-width: 1240px;
  margin: 0 auto;
  padding: 28px 20px 64px;
  display: flex;
  flex-direction: column;
  gap: 30px;
}

/* ── signage ─────────────────────────────────────────────────────────── */

.signage {
  font-family: Archivo, ui-sans-serif, system-ui, sans-serif;
  font-weight: 600;
  text-transform: uppercase;
  letter-spacing: 0.13em;
  font-size: 10.5px;
  color: var(--muted);
}

.readout {
  font-family: "IBM Plex Mono", ui-monospace, "SF Mono", Menlo, monospace;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.01em;
}

/* ── masthead ────────────────────────────────────────────────────────── */

.masthead {
  display: flex;
  flex-wrap: wrap;
  gap: 20px 32px;
  align-items: flex-end;
  justify-content: space-between;
  padding-bottom: 20px;
  border-bottom: 1px solid var(--hairline);
}
.masthead h1 {
  font-family: Archivo, ui-sans-serif, system-ui, sans-serif;
  font-weight: 700;
  font-size: clamp(26px, 4vw, 36px);
  letter-spacing: -0.02em;
  line-height: 1.05;
  margin: 0 0 6px;
  text-wrap: balance;
}
.masthead .tagline { color: var(--ink-2); margin: 0; font-size: 14px; }
.masthead-right { display: flex; flex-direction: column; align-items: flex-start; gap: 8px; }
.stamp { display: flex; align-items: baseline; gap: 8px; }
.stamp .value {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
  font-size: 14px;
  color: var(--ink);
}
.origin-summary { display: flex; flex-wrap: wrap; gap: 6px; }

/* ── origin pills ────────────────────────────────────────────────────── */

.pill {
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 3px 9px;
  border-radius: 999px;
  border: 1px solid var(--hairline);
  background: var(--surface);
  font-family: Archivo, ui-sans-serif, system-ui, sans-serif;
  font-weight: 600;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.1em;
  color: var(--ink-2);
  white-space: nowrap;
}
.dot { width: 7px; height: 7px; border-radius: 50%; flex: none; }
.dot--live   { background: var(--up); }
.dot--manual { background: var(--accent); }
.dot--cache  { background: var(--muted); }
.dot--missing{ background: var(--down); }

/* ── sections ────────────────────────────────────────────────────────── */

.section { display: flex; flex-direction: column; gap: 14px; }
.section-head { display: flex; align-items: center; gap: 14px; }
.section-head .rule { flex: 1; height: 1px; background: var(--hairline); }
.section-head .count {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-size: 11px;
  color: var(--muted);
}

.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(248px, 1fr));
  gap: 14px;
}

/* ── tiles ───────────────────────────────────────────────────────────── */

.tile {
  position: relative;
  display: flex;
  flex-direction: column;
  gap: 12px;
  padding: 16px 16px 14px;
  background: var(--surface);
  border: 1px solid var(--hairline);
  border-radius: 10px;
  box-shadow: var(--shadow);
  overflow: hidden;
}
.tile::before {
  content: "";
  position: absolute;
  inset: 0 auto 0 0;
  width: 3px;
  background: var(--edge, transparent);
}
.tile--stale { border-style: dashed; }
.tile--missing { background: var(--surface-2); }

.tile-head { display: flex; align-items: flex-start; justify-content: space-between; gap: 10px; }
.tile-label {
  font-family: Archivo, ui-sans-serif, system-ui, sans-serif;
  font-weight: 600;
  font-size: 12.5px;
  letter-spacing: 0.01em;
  color: var(--ink);
  margin: 0;
}
.tile-sym { font-size: 10.5px; color: var(--muted); margin-top: 2px; }
.tile-sym a { color: inherit; text-decoration: none; border-bottom: 1px solid var(--hairline); }
.tile-sym a:hover { color: var(--accent); border-bottom-color: var(--accent); }

.tile-value-row { display: flex; align-items: baseline; flex-wrap: wrap; gap: 10px; }
.tile-value { font-size: 27px; font-weight: 500; line-height: 1; }
.tile-unit { font-size: 15px; color: var(--ink-2); font-weight: 400; }

.delta {
  display: inline-flex;
  align-items: baseline;
  gap: 5px;
  padding: 3px 8px;
  border-radius: 6px;
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}
.delta--up   { background: var(--up-wash);   color: var(--up-text); }
.delta--down { background: var(--down-wash); color: var(--down-text); }
.delta--flat { background: var(--surface-2); color: var(--ink-2); }
.delta .arrow { font-size: 10px; }
.delta .pct { opacity: 0.8; }

/* ── sparkline ───────────────────────────────────────────────────────── */

.spark-wrap { position: relative; height: 42px; margin: 0 -2px; }
.spark { display: block; width: 100%; height: 100%; overflow: visible; }
.spark-empty {
  height: 16px;
  border-bottom: 1px dashed var(--hairline);
  margin-bottom: 2px;
}
.spark-tip {
  position: absolute;
  z-index: 5;
  pointer-events: none;
  opacity: 0;
  transform: translate(-50%, -118%);
  transition: opacity 90ms linear;
  padding: 4px 7px;
  border-radius: 5px;
  background: var(--ink);
  color: var(--surface);
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
  font-size: 10.5px;
  white-space: nowrap;
  box-shadow: 0 4px 12px -4px rgba(0,0,0,0.4);
}
.spark-wrap.is-hovered .spark-tip { opacity: 1; }

/* ── range gauges ────────────────────────────────────────────────────── */

.gauges { display: flex; flex-direction: column; gap: 7px; }
.gauge { display: grid; grid-template-columns: 26px 1fr auto; align-items: center; gap: 8px; }
.gauge-key {
  font-family: Archivo, ui-sans-serif, system-ui, sans-serif;
  font-weight: 600;
  font-size: 9px;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  color: var(--muted);
}
.gauge-track {
  position: relative;
  height: 4px;
  border-radius: 2px;
  background: var(--hairline-2);
  border: 1px solid var(--hairline);
}
.gauge-fill { position: absolute; inset: 0 auto 0 0; border-radius: 2px; background: var(--accent-soft); }
.gauge-mark {
  position: absolute;
  top: 50%;
  width: 3px;
  height: 12px;
  border-radius: 1.5px;
  transform: translate(-50%, -50%);
  background: var(--mark, var(--accent));
  box-shadow: 0 0 0 2px var(--surface);
}
.gauge-bounds {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
  font-size: 9.5px;
  color: var(--muted);
  white-space: nowrap;
}

.tile-foot {
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  padding-top: 10px;
  border-top: 1px solid var(--hairline-2);
  font-size: 10.5px;
  color: var(--muted);
}
.tile-foot .stat { display: flex; gap: 5px; }
.tile-foot .stat b {
  font-family: "IBM Plex Mono", ui-monospace, monospace;
  font-variant-numeric: tabular-nums;
  font-weight: 500;
  color: var(--ink-2);
}
.tile-flag {
  font-size: 10.5px;
  line-height: 1.4;
  color: var(--ink-2);
  background: var(--surface-2);
  border-radius: 6px;
  padding: 7px 9px;
}
.tile-flag--bad { color: var(--down-text); background: var(--down-wash); }

/* ── featured tile ───────────────────────────────────────────────────── */

.featured { display: grid; grid-template-columns: minmax(260px, 340px) 1fr; gap: 26px; align-items: stretch; }
.featured .tile-value { font-size: clamp(44px, 7vw, 62px); }
.featured .tile-unit { font-size: 24px; }
.featured .tile-label { font-size: 14px; }
.featured .spark-wrap { height: 116px; }
.featured .delta { font-size: 13.5px; padding: 4px 10px; }
.featured-note {
  font-size: 12.5px;
  color: var(--ink-2);
  line-height: 1.55;
  max-width: 46ch;
  margin: 0;
}
.featured-left { display: flex; flex-direction: column; align-items: flex-start; gap: 14px; justify-content: center; }
.featured-right { display: flex; flex-direction: column; gap: 12px; justify-content: center; min-width: 0; }
.featured--solo { grid-template-columns: 1fr; gap: 14px; }
.featured--solo .featured-right {
  flex-direction: row;
  flex-wrap: wrap;
  align-items: baseline;
  justify-content: flex-start;
  gap: 6px 26px;
  padding-top: 12px;
  border-top: 1px solid var(--hairline-2);
}
.featured--solo .tile-foot { border-top: none; padding-top: 0; }
@media (max-width: 720px) {
  .featured { grid-template-columns: 1fr; gap: 18px; }
}

/* ── table view ──────────────────────────────────────────────────────── */

.ledger { border: 1px solid var(--hairline); border-radius: 10px; background: var(--surface); overflow: hidden; }
.ledger > summary {
  cursor: pointer;
  list-style: none;
  padding: 13px 16px;
  display: flex;
  align-items: center;
  gap: 9px;
  font-family: Archivo, ui-sans-serif, system-ui, sans-serif;
  font-weight: 600;
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 0.11em;
  color: var(--ink-2);
}
.ledger > summary::-webkit-details-marker { display: none; }
.ledger > summary::before { content: "▸"; color: var(--muted); font-size: 10px; }
.ledger[open] > summary::before { content: "▾"; }
.ledger > summary:hover { color: var(--accent); }
.table-scroll { overflow-x: auto; border-top: 1px solid var(--hairline); }
table { border-collapse: collapse; width: 100%; min-width: 720px; font-size: 12.5px; }
th, td { padding: 9px 16px; text-align: right; border-bottom: 1px solid var(--hairline-2); white-space: nowrap; }
th:first-child, td:first-child { text-align: left; }
thead th {
  font-family: Archivo, ui-sans-serif, system-ui, sans-serif;
  font-weight: 600;
  font-size: 9.5px;
  text-transform: uppercase;
  letter-spacing: 0.09em;
  color: var(--muted);
  background: var(--surface-2);
}
tbody td { font-family: "IBM Plex Mono", ui-monospace, monospace; font-variant-numeric: tabular-nums; color: var(--ink-2); }
tbody td:first-child { font-family: "IBM Plex Sans", sans-serif; color: var(--ink); }
tbody tr:last-child td { border-bottom: none; }
td .up { color: var(--up-text); }
td .down { color: var(--down-text); }

/* ── colophon ────────────────────────────────────────────────────────── */

.colophon {
  display: flex;
  flex-wrap: wrap;
  gap: 22px 40px;
  padding-top: 22px;
  border-top: 1px solid var(--hairline);
  font-size: 12px;
  color: var(--muted);
  line-height: 1.6;
}
.colophon h2 {
  font-family: Archivo, ui-sans-serif, system-ui, sans-serif;
  font-weight: 600;
  font-size: 10px;
  text-transform: uppercase;
  letter-spacing: 0.12em;
  color: var(--ink-2);
  margin: 0 0 8px;
}
.colophon ul { margin: 0; padding: 0; list-style: none; display: flex; flex-direction: column; gap: 4px; }
.colophon a { color: var(--ink-2); text-decoration: none; border-bottom: 1px solid var(--hairline); }
.colophon a:hover { color: var(--accent); border-bottom-color: var(--accent); }
.colophon .legend { display: flex; flex-direction: column; gap: 5px; }
.colophon .legend span { display: flex; align-items: center; gap: 7px; }

a:focus-visible, summary:focus-visible, .spark-wrap:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
  border-radius: 3px;
}
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { transition-duration: 0.01ms !important; animation-duration: 0.01ms !important; }
}
"""
