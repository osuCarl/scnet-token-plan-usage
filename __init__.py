"""SCNet Token Plan usage monitor plugin.

Local metering for scnet.cn Token Plan Credits: aggregates Hermes'
state.db usage records for api.scnet.cn calls and converts them to
Credits with the official multiplier formula. No tools, no hooks — the
value ships through the dashboard backend (dashboard/plugin_api.py) and
the desktop UI (desktop/plugin.js).
"""
