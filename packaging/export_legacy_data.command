#!/bin/bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
if [[ -z "${LEGACY_DATA_ROOT:-}" ]]; then
  if [[ -d "$HOME/.practical_tools" ]]; then LEGACY_ROOT="$HOME/.practical_tools"; else LEGACY_ROOT="$HOME/MAC策划实用小工具"; fi
else LEGACY_ROOT="$LEGACY_DATA_ROOT"; fi
if [[ -z "${SCREENSHOT_ROOT:-}" ]]; then
  if [[ -d "$LEGACY_ROOT/screenshot" ]]; then SCREENSHOT_ROOT="$LEGACY_ROOT/screenshot"; else SCREENSHOT_ROOT="$LEGACY_ROOT/截图数据"; fi
else SCREENSHOT_ROOT="$SCREENSHOT_ROOT"; fi
OUTPUT="${1:-$HOME/Desktop/PracticalToolsMigration-$(date +%Y%m%d-%H%M%S).ptmigration.zip}"
exec "$PYTHON_BIN" "$SCRIPT_DIR/scripts/export_legacy_bundle.py" --legacy-data-root "$LEGACY_ROOT" --screenshot-root "$SCREENSHOT_ROOT" --output "$OUTPUT"
