#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT_DIR"
if [ -z "${PYTHON_BIN:-}" ]; then
  if [ -x "$ROOT_DIR/.venv/bin/python" ]; then
    PYTHON_BIN="$ROOT_DIR/.venv/bin/python"
  else
    PYTHON_BIN="python3"
  fi
fi
export PYINSTALLER_CONFIG_DIR="${PYINSTALLER_CONFIG_DIR:-/tmp/practical-agent-pyinstaller}"

if ! "$PYTHON_BIN" -m PyInstaller --version >/dev/null 2>&1; then
  echo "未找到 PyInstaller，请先执行：$PYTHON_BIN -m pip install -r agent/requirements.txt" >&2
  exit 2
fi

"$PYTHON_BIN" -m PyInstaller --noconfirm --clean --workpath /tmp/practical-agent-build --distpath "$ROOT_DIR/dist" packaging/agent_mac.spec
PACKAGE_DIR="$ROOT_DIR/dist/PracticalToolsAgent-package"
mkdir -p "$PACKAGE_DIR"
mv -f "$ROOT_DIR/dist/PracticalToolsAgent" "$PACKAGE_DIR/PracticalToolsAgent"
cp agent/screenshot/config.yaml "$PACKAGE_DIR/config.yaml"
cp packaging/install_agent.command "$PACKAGE_DIR/install_agent.command"
chmod +x "$PACKAGE_DIR/PracticalToolsAgent" "$PACKAGE_DIR/install_agent.command"
echo "Mac Agent 已生成：$PACKAGE_DIR/PracticalToolsAgent"
