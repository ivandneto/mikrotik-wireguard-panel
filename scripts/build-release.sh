#!/usr/bin/env bash
# Builds the Release files with the version in the name, read from app/__init__.py:
#   dist/release/wgpanel-docker-amd64-vX.Y.Z.tar.gz   (Docker on a PC/server)
#   dist/release/wgpanel-mikrotik-arm64-vX.Y.Z.tar    (container on the MikroTik)
#   dist/release/docker-compose.yml, dist/release/env.example
# Usage: bash scripts/build-release.sh   (needs Docker with buildx)
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(sed -n 's/^__version__ = "\(.*\)"/\1/p' app/__init__.py)
[ -n "$VERSION" ] || { echo "Version not found in app/__init__.py" >&2; exit 1; }
OUT=dist/release
rm -rf "$OUT" && mkdir -p "$OUT"

echo ">> Docker (amd64) v$VERSION"
docker buildx build --platform linux/amd64 -t wgpanel:latest -t "wgpanel:$VERSION" --load .
docker save wgpanel:latest "wgpanel:$VERSION" | gzip -6 > "$OUT/wgpanel-docker-amd64-v$VERSION.tar.gz"

echo ">> Container MikroTik (arm64) v$VERSION"
docker buildx build --platform linux/arm64 -f Dockerfile.mikrotik -t "wgpanel:$VERSION-arm64" \
  --output "type=docker,dest=$OUT/wgpanel-mikrotik-arm64-v$VERSION.tar" .

cp docker-compose.yml "$OUT/docker-compose.yml"
cp .env.example "$OUT/env.example"
echo ">> Done: version $VERSION files in $OUT"
ls -la "$OUT"
