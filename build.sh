#!/bin/bash
set -e

CYAN='\033[0;36m'; GREEN='\033[0;32m'; YELLOW='\033[0;33m'; BOLD='\033[1m'; NC='\033[0m'

echo ""
echo -e "${CYAN}${BOLD}"
echo "  ██████╗ ██╗  ██╗ █████╗ ███╗   ██╗████████╗ ██████╗ ███╗   ███╗"
echo "  ██╔══██╗██║  ██║██╔══██╗████╗  ██║╚══██╔══╝██╔═══██╗████╗ ████║"
echo "  ██████╔╝███████║███████║██╔██╗ ██║   ██║   ██║   ██║██╔████╔██║"
echo "  ██╔═══╝ ██╔══██║██╔══██║██║╚██╗██║   ██║   ██║   ██║██║╚██╔╝██║"
echo "  ██║     ██║  ██║██║  ██║██║ ╚████║   ██║   ╚██████╔╝██║ ╚═╝ ██║"
echo "  ╚═╝     ╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═══╝   ╚═╝    ╚═════╝ ╚═╝     ╚═╝"
echo -e "${NC}${BOLD}  PhantomCLI v3.0.0  ·  Aravind Labs  ·  Official Build${NC}"
echo ""

OS="$(uname -s)"; ARCH="$(uname -m)"
echo -e "${CYAN}[SYS]${NC} $OS / $ARCH"

# ── Clean ────────────────────────────────────────────────────────────────────
echo -e "${CYAN}[1/5]${NC} Cleaning old builds..."
rm -rf dist/ build/ *.spec dist_protected/ omnicli_obf/

# ── Dependencies ─────────────────────────────────────────────────────────────
echo -e "${CYAN}[2/5]${NC} Installing dependencies..."
pip install -r requirements.txt --quiet
pip install pyarmor --quiet   # source obfuscation

# ── Playwright ───────────────────────────────────────────────────────────────
echo -e "${CYAN}[3/5]${NC} Installing Playwright Chromium..."
playwright install chromium

# ── PyArmor obfuscation (source protection) ───────────────────────────────
echo -e "${CYAN}[4/5]${NC} Obfuscating source code with PyArmor..."
# Obfuscate the omnicli package only (not run.py entry point)
# RFT mode renames internal identifiers making reverse engineering impractical
pyarmor gen \
  --output omnicli_obf \
  --recursive \
  omnicli/ 2>/dev/null || {
    echo -e "${YELLOW}[WARN] PyArmor gen failed — building without obfuscation${NC}"
    cp -r omnicli omnicli_obf
}

# Check if obfuscation produced valid output
if [ -d "omnicli_obf/omnicli" ]; then
  OBF_SRC="omnicli_obf/omnicli"
else
  OBF_SRC="omnicli_obf"
fi

# ── PyInstaller ───────────────────────────────────────────────────────────────
echo -e "${CYAN}[5/5]${NC} Compiling binary with PyInstaller..."
pyinstaller \
  --name phantomcli \
  --onefile \
  --hidden-import stdiomask \
  --hidden-import fastapi \
  --hidden-import uvicorn \
  --hidden-import uvicorn.logging \
  --hidden-import uvicorn.loops \
  --hidden-import uvicorn.loops.auto \
  --hidden-import uvicorn.protocols \
  --hidden-import uvicorn.protocols.http \
  --hidden-import uvicorn.protocols.http.auto \
  --hidden-import uvicorn.lifespan \
  --hidden-import uvicorn.lifespan.on \
  --hidden-import sqlite3 \
  --hidden-import cryptography \
  --hidden-import plotext \
  --hidden-import websockets \
  --hidden-import jinja2 \
  --hidden-import packaging \
  --hidden-import rich \
  --add-data "omnicli/templates:omnicli/templates" \
  --add-data "omnicli/tui.py:omnicli" \
  --add-data "omnicli/settings.py:omnicli" \
  --add-data "omnicli/commands.py:omnicli" \
  run.py \
  --noconfirm --clean

# ── Rename ────────────────────────────────────────────────────────────────────
if [ "$OS" = "Linux" ]; then
  mv dist/phantomcli dist/phantomcli-linux
  BINARY="phantomcli-linux"
elif [ "$OS" = "Darwin" ]; then
  mv dist/phantomcli dist/phantomcli-macos
  BINARY="phantomcli-macos"
else
  BINARY="phantomcli"
fi

# ── Cleanup obfuscation artifacts ─────────────────────────────────────────────
rm -rf omnicli_obf/

echo ""
echo -e "${GREEN}${BOLD}[✔] Build complete!${NC}"
echo -e "    Binary : ${CYAN}dist/$BINARY${NC}"
echo -e "    Size   : $(du -sh dist/$BINARY | cut -f1)"
echo ""
echo -e "  Deploy:"
echo -e "  ${CYAN}cp dist/$BINARY /var/www/phantom/downloads/$BINARY${NC}"
echo -e "  ${CYAN}chmod +x /var/www/phantom/downloads/$BINARY${NC}"
echo ""
echo -e "  ${YELLOW}⚡ PhantomCLI v2.0.0 · Aravind Labs${NC}"
echo ""
