#!/usr/bin/env bash
# Build an MCPB bundle for arxiv-mcp-server.
# Outputs: mcpb-build/arxiv-mcp-server-<version>.mcpb
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VERSION="$(python3 -c "import tomllib; d=tomllib.load(open('$ROOT/pyproject.toml','rb')); print(d['project']['version'])")"
BUILD_DIR="$ROOT/mcpb-build"

echo "Building arxiv-mcp-server v$VERSION MCPB bundle..."

# Clean and create structure
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR/server/vendor"

# Generate a 512x512 teal icon (pure Python, no deps)
python3 - "$BUILD_DIR/icon.png" <<'PYEOF'
import struct, zlib, sys

def make_png(width, height, r, g, b):
    def chunk(name, data):
        c = name + data
        return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xFFFFFFFF)
    scanline = b'\x00' + bytes([r, g, b] * width)
    return (
        b'\x89PNG\r\n\x1a\n'
        + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 2, 0, 0, 0))
        + chunk(b'IDAT', zlib.compress(scanline * height))
        + chunk(b'IEND', b'')
    )

with open(sys.argv[1], 'wb') as f:
    f.write(make_png(512, 512, 20, 108, 148))
print(f"Generated icon.png")
PYEOF

# Copy manifest and stamp version
python3 - "$ROOT/manifest.json" "$BUILD_DIR/manifest.json" "$VERSION" <<'PYEOF'
import json, sys
with open(sys.argv[1]) as f:
    m = json.load(f)
m['version'] = sys.argv[3]
with open(sys.argv[2], 'w') as f:
    json.dump(m, f, indent=2)
print(f"Stamped manifest version: {sys.argv[3]}")
PYEOF

# Copy server source
cp -r "$ROOT/src/arxiv_mcp_server" "$BUILD_DIR/server/arxiv_mcp_server"
echo "Copied server source"

# Vendor runtime dependencies (stdio transport only — skip uvicorn/sse-starlette/black)
echo "Vendoring dependencies..."
pip install \
  --target "$BUILD_DIR/server/vendor" \
  --no-user \
  --quiet \
  "arxiv>=2.1.0" \
  "httpx>=0.24.0" \
  "python-dateutil>=2.8.2" \
  "pydantic>=2.8.0" \
  "mcp>=1.2.0" \
  "aiohttp>=3.9.1" \
  "python-dotenv>=1.0.0" \
  "pydantic-settings>=2.1.0" \
  "aiofiles>=23.2.1" \
  "anyio>=4.2.0"
echo "Dependencies vendored"

# Pack with mcpb
echo "Packing..."
cd "$BUILD_DIR"
npx --yes @anthropic-ai/mcpb pack

ARTIFACT=$(ls "$BUILD_DIR"/*.mcpb 2>/dev/null | head -1)
if [[ -z "$ARTIFACT" ]]; then
  echo "ERROR: no .mcpb file found after packing" >&2
  exit 1
fi

echo "Built: $ARTIFACT"
