#!/usr/bin/env bash
# 本地构建脚本，用于测试 PyInstaller 打包
# Usage: bash build.sh
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
info() { echo -e "${GREEN}[build]${NC} $*"; }
step() { echo -e "${BLUE}▶${NC} $*"; }
die()  { echo -e "${RED}[build] error:${NC} $*" >&2; exit 1; }

OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS-$ARCH" in
    Darwin-arm64)  OUT="chartgen-darwin-arm64" ;;
    Darwin-x86_64) OUT="chartgen-darwin-x86_64" ;;
    Linux-x86_64)  OUT="chartgen-linux-x86_64" ;;
    *) die "Unsupported: $OS-$ARCH" ;;
esac

step "Installing build tools..."
uv add --dev pyinstaller python-minifier

step "Building binary..."
uv run pyinstaller \
    --onefile \
    --name chartgen \
    --add-data "chartgen_cli/agent_loop/prompt:chartgen_cli/agent_loop/prompt" \
    --add-data "chartgen_cli/tools/prompt:chartgen_cli/tools/prompt" \
    --hidden-import=playwright \
    --hidden-import=pyecharts \
    --hidden-import=pandas \
    --hidden-import=numpy \
    --hidden-import=matplotlib \
    --hidden-import=pptx \
    --collect-all=pyecharts \
    chartgen_cli/main.py

mv dist/chartgen "dist/$OUT"
info "Done: dist/$OUT ($(du -sh dist/$OUT | cut -f1))"
info "Test with: ./dist/$OUT"
