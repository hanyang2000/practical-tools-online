#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

PYTHON_BIN="${PYTHON_BIN:-$ROOT/.venv/bin/python}"
MAKENSIS_BIN="${MAKENSIS_BIN:-makensis}"

if [[ ! -x "$PYTHON_BIN" ]]; then
  echo "找不到 Python：$PYTHON_BIN" >&2
  exit 2
fi
if ! command -v "$MAKENSIS_BIN" >/dev/null 2>&1 && [[ ! -x "$MAKENSIS_BIN" ]]; then
  echo "找不到 makensis，请安装 NSIS 3.x。" >&2
  exit 2
fi

echo "[1/3] 刷新当前源码对应的 Windows staging..."
"$PYTHON_BIN" packaging/build_release.py

echo "[2/4] 构建独立中心控制台..."
"$PYTHON_BIN" -m PyInstaller --noconfirm --clean packaging/center_console.spec
if [[ -f "$ROOT/dist/PracticalToolsCenterConsole/PracticalToolsCenterConsole.exe" ]]; then
  cp -f "$ROOT/dist/PracticalToolsCenterConsole/PracticalToolsCenterConsole.exe" "$ROOT/dist/PracticalToolsOnlinePortable/PracticalToolsCenterConsole.exe"
else
  echo "当前构建环境没有生成 Windows 控制台 exe，安装包将保留现有 StartCenter.cmd 启动链并跳过控制台快捷方式。"
fi

echo "[3/4] 读取版本并准备输出目录..."
APP_VERSION="$($PYTHON_BIN -c 'from app import __version__; print(__version__)')"
mkdir -p dist/installer

echo "[4/4] 编译 NSIS Windows 安装包..."
"$MAKENSIS_BIN" "-DAPP_VERSION=$APP_VERSION" packaging/windows/installer/PracticalToolsOnline.nsi
echo "已生成：$ROOT/dist/installer/PracticalToolsOnline-Setup-v$APP_VERSION.exe"
