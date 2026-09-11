#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
export PLAYWRIGHT_BROWSERS_PATH="$SCRIPT_DIR/browsers"
ROOT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
DATA_DIR="${PRACTICAL_TOOLS_DATA:-$HOME/.practical_tools/screenshot}"
mkdir -p "$DATA_DIR"
if [ -x "$SCRIPT_DIR/PracticalToolsAgent" ]; then
  AGENT_KIND="binary"
else
  AGENT_KIND="source"
fi
run_agent() {
  if [ "$AGENT_KIND" = "binary" ]; then
    "$SCRIPT_DIR/PracticalToolsAgent" "$@"
  else
    "${PYTHON_BIN:-python3}" "$ROOT_DIR/agent/agent.py" "$@"
  fi
}

# The center domain is stable.  A reinstall on the same computer therefore
# only needs to repair launchd/schtasks; it must not consume another pair code
# or create a second device.  Use --rebind when a deliberate new pairing is
# required.
MODE="${1:-auto}"
if [ "$MODE" = "--repair" ] || { [ "$MODE" = "auto" ] && [ -f "$DATA_DIR/agent.json" ]; }; then
  run_agent --data-dir "$DATA_DIR" --repair-install
  echo "Agent 已修复。设备身份、中心地址、登录态和定时任务均已保留。"
  exit 0
fi

if [ "$MODE" = "--rebind" ]; then
  shift
fi
DEFAULT_SERVER="${PRACTICAL_TOOLS_SERVER_URL:-https://collab.wnnttzy.kdns.fr}"
read -r -p "中心服务地址 [$DEFAULT_SERVER]: " SERVER
SERVER="${SERVER:-$DEFAULT_SERVER}"
read -r -s -p "一次性配对码（请从已登录的网页生成，10分钟内有效）: " PAIR_CODE
echo
if [ -z "$PAIR_CODE" ]; then
  echo "未提供一次性配对码，已取消。已有 Agent 请直接回车执行修复；首次安装请先生成配对码。" >&2
  exit 1
fi
run_agent --server "$SERVER" --data-dir "$DATA_DIR" --pair "$PAIR_CODE"
echo "Agent 配对成功。token 已保存到系统 Keychain/Credential Manager，agent.json 不保存 token。"
echo "Agent 已配置为开机自启；不要再从下载目录或重复副本手动启动。"
if [ "$AGENT_KIND" = "binary" ]; then
  echo "正式启动文件：$SCRIPT_DIR/PracticalToolsAgent"
else
  echo "正式启动文件：$ROOT_DIR/agent/agent.py"
fi
