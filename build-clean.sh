#!/bin/bash
# build-clean.sh - Creates a clean PhantomCLI distribution
# Run this before uploading to the server

set -e

echo "⚡ PhantomCLI Clean Build"
echo "========================"

VERSION=$(python -c "from omnicli import __version__; print(__version__)")
echo "Version: $VERSION"

# Create temp directory.
# IMPORTANT: zip files must be FLAT (no top-level phantomcli-X.Y.Z/ dir).
# `_do_update` (cli.py) and the Windows updater both extract the zip directly
# into the install dir with `Expand-Archive -Force` / `z.extract(...)`. A
# nested top-level dir creates a sibling subfolder that the running app never
# loads — which is exactly why /update silently no-ops on Windows.
TMP_DIR=$(mktemp -d)
DIST_DIR="$TMP_DIR/payload"

echo "Creating clean distribution in $DIST_DIR..."
mkdir -p "$DIST_DIR"

# Copy only source files (no build artifacts)
cp -r omnicli/ run.py requirements.txt README.md LICENSE "$DIST_DIR/" 2>/dev/null || true

# Remove any accidentally copied artifacts
find "$DIST_DIR" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
find "$DIST_DIR" -name '*.pyc' -delete 2>/dev/null || true
find "$DIST_DIR" -name '*.pyo' -delete 2>/dev/null || true

# Show what's included
echo ""
echo "📦 Package contents:"
find "$DIST_DIR" -type f | head -20
echo "... ($(find "$DIST_DIR" -type f | wc -l) total files)"

# Calculate size
SIZE=$(du -sh "$DIST_DIR" | cut -f1)
echo ""
echo "📊 Package size: $SIZE"

# Create zip — files at root (zip from inside payload/ so members have no prefix)
OUTPUT="phantomcli-source-v$VERSION.zip"
(cd "$DIST_DIR" && zip -r "$OUTPUT" .)
mv "$DIST_DIR/$OUTPUT" ./

# Cleanup
rm -rf "$TMP_DIR"

echo ""
echo "✅ Clean distribution created: $OUTPUT"
echo "   Size: $(du -h "$OUTPUT" | cut -f1)"
echo ""
echo "Upload to: https://phantom.aravindlabs.tech/phantomcli/downloads/"
