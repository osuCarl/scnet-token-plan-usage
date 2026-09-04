# scnet-token-plan-usage

English | [中文](README.md)

**Real-time usage monitor for scnet.cn Token Plan — a Hermes Agent desktop plugin**

See your Token Plan Credits consumption live in the Hermes desktop app: cycle-to-date used/remaining, today's burn, per-model breakdown, and daily trend — no more opening the console web page.

## Why

SCNet exposes no programmatic usage endpoint (the console page is session-cookie authenticated; the `sk-tp-` API key only unlocks inference routes). This plugin takes another path: **Hermes already records every API call's token buckets into the local `state.db`**. The plugin aggregates those records and converts them to Credits with the official billing formula.

## Billing formula

```
credits = model_multiplier * (uncached_input/120 + cached_input/2000 + output/28.33)
```

- Unit-price anchors come from SCNet's published Kimi-K2.6 data (60,000 Credits ≈ 7.2M uncached input / 120M cached input / 1.7M output, at multiplier 1.00×)
- Per-model multipliers ship with the 2026-09-01 official values (GLM-5.3=2.29, Kimi-K3=4.12, GLM-5.3-Flash=0.15, …). **SCNet adjusts them weekly** — you can correct any model's multiplier in the plugin settings
- Hermes' recorded `input_tokens` already excludes cache hits, matching the billing semantics exactly

## Install

```bash
git clone https://github.com/osuCarl/scnet-token-plan-usage.git
cd scnet-token-plan-usage
./install.sh          # Git Bash on Windows / macOS / Linux
# or manually: copy the whole directory to ~/.hermes/plugins/scnet-usage/
```

Then:

1. Enable the plugin:
   ```bash
   hermes plugins enable scnet-usage
   ```
2. Restart the Hermes desktop app (backend routes mount at process start)
3. Settings → Plugins → turn on "SCNet Usage Monitor"
4. A usage chip appears in the status bar; click it or use ⌘K "SCNet: open usage panel"
5. In the panel's gear menu, set your plan tier (Basic/Standard/Pro/Flagship) and purchase day to see remaining quota and the progress bar

## Features

- **Statusbar chip**: always-on remaining Credits; turns amber under 25%, red under 10%
- **Usage pane**: cycle/today Credits, progress bar, daily bar chart, per-model rows (multiplier, tokens, call count)
- **Auto-refresh every 30s** (React Query polling; manual refresh command in ⌘K)
- **Configurable**: plan quota, cycle start day, per-model multiplier overrides — stored in local `config.json`

## Limitations (important)

- Only counts calls made **by Hermes**. Usage from other tools sharing the same Token Plan key (Cursor, Claude Code, Cline, …) is not in this data — reconcile against the console "Token 用量" page
- The estimate is a formula conversion; SCNet adjusts multipliers weekly, so numbers may drift slightly from the console. Correct the multiplier in settings when they do

## Data & privacy

- All data comes from the local `state.db` (read-only SQLite); no requests leave your machine
- Never reads, stores, or transmits your API key

## License

MIT
