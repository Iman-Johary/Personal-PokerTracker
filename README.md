# Poker Tracker

A small, dependency-free Python tool that analyzes **GGPoker 6-max No-Limit Hold'em cash**
hand histories and produces a single self-contained, interactive `dashboard.html`.

The focus is **preflop GTO analysis**: it classifies your (the "Hero") preflop decisions,
compares them against reference GTO ranges, flags deviations, and lets you replay any hand
on a visual poker table with a postflop equity readout.

> **Note:** This repository contains only the analysis tools. Your hand histories and the
> generated dashboard are **not** included — you supply your own exports (see below). Nothing
> leaves your machine; everything runs locally.

## Features

- **Preflop leak detection** — Raise-First-In, facing a single open, and facing a 3-bet, each
  judged against reference ranges with per-position 13×13 charts (raise / call / fold colored).
- **Deviation flags** — `too_tight`, `too_loose`, `limp`, etc., with mixed-strategy cells
  deliberately not flagged.
- **Hand replayer** — step through any hand action-by-action (Prev/Next or arrow keys); the board
  reveals as it's dealt, with a persistent GTO comment at Hero's decision point.
- **Postflop readout** — board texture, Hero's made hand, and (heads-up) Monte-Carlo equity vs an
  estimated villain range — all pure standard-library, no third-party solver.
- **Date-range filtering** — every chart and stat recomputes for All / latest day / last 3 / last 7
  / custom range, with a day-by-day trend table.
- **Extra tabs** — biggest wins & losses, showdowns with win rate, and a hole-cards grid
  (play frequency and net $ per starting hand).

## Requirements

- **Python 3** (standard library only — no `pip install` needed to run the analyzer).
- A modern web browser to open the generated `dashboard.html`.
- *(Optional, dev-only)* [Pillow](https://python-pillow.org/) — only if you want to regenerate the
  reference ranges from range screenshots via `tools/screenshot_import.py`.

## Usage

1. Export your GGPoker hand histories (`.txt`) and place them in a `history/` folder at the
   project root.
2. Run the analyzer:

   ```
   python3 tools/analyze.py
   ```

3. Open the generated `dashboard.html` in your browser.

Re-run the command whenever you add new exports — it rebuilds `dashboard.html` from scratch.

## Project layout

| Path | Purpose |
| --- | --- |
| `tools/analyze.py` | Parser + classifier. Reads `history/*.txt`, builds the data payload, and injects it into the template to produce `dashboard.html`. |
| `tools/template.html` | The entire dashboard UI (HTML/CSS/JS). `analyze.py` replaces the `/*__DATA__*/null` token with the JSON payload. |
| `tools/gto_ranges.py` | Reference GTO ranges (RFI, facing-open, facing-3bet). The place to tune ranges. |
| `tools/gto_data.py` | Auto-generated solver chart data (see credits). |
| `tools/screenshot_ranges.py` | Auto-generated reference ranges (raise/call/fold/all-in frequency per hand) read from solver range screenshots. |
| `tools/screenshot_import.py` | Dev-only importer that OCRs range screenshots into `screenshot_ranges.py` (needs Pillow). |
| `tools/equity.py` | Pure-stdlib postflop engine: 7-card evaluator, made-hand labels, board texture, and Monte-Carlo equity. |

## How ranges are modeled

Standard 6-max **100bb** ranges. Preflop reference ranges combine a solver-derived chart pack
with ranges read from solver range screenshots; mixed-strategy cells (where the solver plays a
hand more than one way) are tracked and **not** flagged as mistakes. Positions use the `HJ`
naming convention.

## Credits

- Solver chart data is imported from
  [AHTOOOXA/poker-charts](https://github.com/AHTOOOXA/poker-charts) (MIT), pekarstas GGPoker pack.

## License

MIT — see [LICENSE](LICENSE).
