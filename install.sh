#!/usr/bin/env bash
# chartgen installer
# Usage: curl -fsSL https://raw.githubusercontent.com/wangyin717/chartgen-cli/main/install.sh | bash
set -euo pipefail

BIN_NAME="chartgen"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.chartgen"

# ── release download URLs（每次发版更新这里）────────────────────────────────────
VERSION="v0.1.2"
URL_DARWIN_ARM64="https://digit-force.coding.net/api/user/digit-force/project/biaopin-swiftagent/depot/chartgen-cli/git/releases/attachments/download/334534"
URL_DARWIN_X86_64=""   # 待补充
URL_LINUX_X86_64=""    # 待补充

# ── colours ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()  { echo -e "${GREEN}[chartgen]${NC} $*"; }
warn()  { echo -e "${YELLOW}[chartgen]${NC} $*"; }
die()   { echo -e "${RED}[chartgen] error:${NC} $*" >&2; exit 1; }
step()  { echo -e "${BLUE}▶${NC} $*"; }

# ── detect platform ───────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"

case "$OS" in
    Darwin)
        case "$ARCH" in
            arm64)  DOWNLOAD_URL="$URL_DARWIN_ARM64" ;;
            x86_64) DOWNLOAD_URL="$URL_DARWIN_X86_64" ;;
            *)      die "Unsupported architecture: $ARCH" ;;
        esac
        ;;
    Linux)
        case "$ARCH" in
            x86_64) DOWNLOAD_URL="$URL_LINUX_X86_64" ;;
            *)      die "Unsupported architecture: $ARCH (only x86_64 supported on Linux)" ;;
        esac
        ;;
    *) die "Unsupported OS: $OS. Supports macOS and Linux only." ;;
esac

[ -z "$DOWNLOAD_URL" ] && die "No binary available for $OS/$ARCH yet."
step "Detected: $OS/$ARCH → $VERSION"

# ── download binary ───────────────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
TEMP_BIN="$(mktemp)"

step "Downloading $ARTIFACT..."
curl -fsSL --progress-bar "$DOWNLOAD_URL" -o "$TEMP_BIN" \
    || die "Download failed: $DOWNLOAD_URL"

chmod +x "$TEMP_BIN"
mv "$TEMP_BIN" "$BIN_DIR/$BIN_NAME"
info "Installed: $BIN_DIR/$BIN_NAME"

# ── Playwright browser (generate_ppt feature) ─────────────────────────────────
step "Installing Playwright browser binary (for generate_ppt — may take a few minutes)..."
"$BIN_DIR/$BIN_NAME" _playwright_install 2>/dev/null \
    || warn "Playwright browser install failed. Run later: chartgen _playwright_install"

# ── Linux clipboard hint ──────────────────────────────────────────────────────
if [ "$OS" = "Linux" ]; then
    if ! command -v xclip >/dev/null 2>&1 && ! command -v xsel >/dev/null 2>&1; then
        warn "xclip / xsel not found — /copy command (clipboard) will be unavailable."
        warn "Optional: sudo apt install xclip"
    fi
fi

# ── config file ───────────────────────────────────────────────────────────────
mkdir -p "$CONFIG_DIR" "$BIN_DIR"
CONFIG_FILE="$CONFIG_DIR/config"
if [ ! -f "$CONFIG_FILE" ]; then
    cat > "$CONFIG_FILE" <<'EOF'
# chartgen config
#
# -- Option 1: ChartGen hosted service (recommended, no LLM key needed) --------
# Sign up at https://chartgen.ai to get your Access Token, then uncomment:
# SERVER_URL=https://api.chartgen.ai
# ACCESS_TOKEN=your-access-token-here

# -- Option 2: Bring Your Own Key (BYOK) ---------------------------------------
# Setting LLM_API_KEY switches to BYOK mode (takes priority over Option 1).
#
# DeepSeek
# LLM_PROVIDER=deepseek
# LLM_API_KEY=sk-xxx
# LLM_MODEL=deepseek-chat

# Zhipu GLM
# LLM_PROVIDER=glm
# LLM_API_KEY=xxx
# LLM_MODEL=glm-4.6

# OpenAI
# LLM_PROVIDER=openai
# LLM_API_KEY=sk-xxx
# LLM_MODEL=gpt-4o

# Claude (Anthropic)
# LLM_PROVIDER=claude
# LLM_API_KEY=sk-ant-xxx
# LLM_MODEL=claude-sonnet-4-6

# -- Optional third-party data sources (omit → feature degrades gracefully) ----
# GOOGLE_TRENDS_API=
# GUGUDATA_API=
# TUSHARE_API=
# PERPLEXITY_API_KEY=
EOF
    info "Config template written: $CONFIG_FILE"
    warn "Edit $CONFIG_FILE and set at least one LLM_PROVIDER + LLM_API_KEY."
else
    info "Config already exists, skipping: $CONFIG_FILE"
fi

# ── PATH ──────────────────────────────────────────────────────────────────────
SHELL_RC=""
case "${SHELL:-}" in
    */zsh)  SHELL_RC="$HOME/.zshrc"  ;;
    */bash) SHELL_RC="$HOME/.bashrc" ;;
esac

if ! echo "$PATH" | tr ':' '\n' | grep -qx "$BIN_DIR"; then
    if [ -n "$SHELL_RC" ]; then
        if ! grep -qF "export PATH=\"$BIN_DIR" "$SHELL_RC" 2>/dev/null; then
            echo "" >> "$SHELL_RC"
            echo "# Added by chartgen installer" >> "$SHELL_RC"
            echo "export PATH=\"$BIN_DIR:\$PATH\"" >> "$SHELL_RC"
            info "Added $BIN_DIR to $SHELL_RC"
        fi
    fi
    echo ""
    warn "Run the following to activate (or open a new terminal):"
    warn "  export PATH=\"$BIN_DIR:\$PATH\""
fi

# ── done ──────────────────────────────────────────────────────────────────────
echo ""
info "Done! Next steps:"
info "  1. Edit $CONFIG_FILE — set LLM_PROVIDER and LLM_API_KEY"
info "  2. cd into your project directory"
info "  3. Run: ${BIN_NAME}"
