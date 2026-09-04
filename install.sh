#!/usr/bin/env bash
# Install scnet-token-plan-usage into the Hermes plugins directory.
# Works in Git Bash on Windows, macOS, and Linux.

set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"

# Resolve the Hermes home (respect $HERMES_HOME, fall back to the default).
if [ -n "${HERMES_HOME:-}" ]; then
  HOME_DIR="$HERMES_HOME"
elif [ "$(uname -s)" = "Linux" ] || [ "$(uname -s)" = "Darwin" ]; then
  HOME_DIR="$HOME/.hermes"
else
  # Windows Git Bash: the app data location used by the git install.
  HOME_DIR="$LOCALAPPDATA/hermes"
fi

DEST="$HOME_DIR/plugins/scnet-usage"

if [ ! -d "$HOME_DIR" ]; then
  echo "ERROR: Hermes home not found at $HOME_DIR" >&2
  echo "Set HERMES_HOME or install Hermes first: https://hermes-agent.nousresearch.com" >&2
  exit 1
fi

mkdir -p "$DEST/dashboard" "$DEST/desktop"

cp "$SRC/plugin.yaml"      "$DEST/"
cp "$SRC/__init__.py"      "$DEST/"
cp "$SRC/dashboard/manifest.json" "$DEST/dashboard/"
cp "$SRC/dashboard/plugin_api.py" "$DEST/dashboard/"
cp "$SRC/desktop/plugin.js"       "$DEST/desktop/"

# config.json (plan tier etc.) is intentionally NOT copied — it is user-local
# state and will be created on first use.

echo "Installed to: $DEST"
echo ""
echo "Next steps:"
echo "  1. hermes plugins enable scnet-usage"
echo "  2. Restart the Hermes desktop app"
echo "  3. Settings → Plugins → enable 'SCNet Usage Monitor'"
echo "  4. Click the statusbar chip, set your plan tier in the gear menu"
