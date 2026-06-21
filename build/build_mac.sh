#!/usr/bin/env bash
#
# Build the Spotiflac macOS app end to end.
#
#   1. Freeze the Python sidecar with PyInstaller  -> dist/spotiflac-sidecar/
#   2. Bundle it inside an Electron app + DMG       -> app/dist/
#
# Run this on a Mac (Xcode command-line tools + Python 3.10+ + Node 18+).
# ffmpeg is bundled by spotdl at runtime, so it isn't required at build time.
#
# Code signing / notarization are optional and controlled by env vars:
#   CSC_LINK / CSC_KEY_PASSWORD      - signing certificate (electron-builder)
#   APPLE_ID / APPLE_APP_SPECIFIC_PASSWORD / APPLE_TEAM_ID - notarization
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "==> [1/3] Python environment"
python3 -m venv .venv-build
# shellcheck disable=SC1091
source .venv-build/bin/activate
pip install --upgrade pip wheel
pip install -e ".[lossless]"   # include the Tidal/Qobuz/Deezer (streamrip) backend
pip install pyinstaller

echo "==> [2/3] Freezing the sidecar (+ bundled streamrip 'rip' CLI)"
rm -rf build/work dist/spotiflac-sidecar
pyinstaller build/spotiflac-sidecar.spec --noconfirm \
  --distpath dist --workpath build/work

# Freeze the streamrip CLI next to the sidecar so the Tidal backend works in
# the packaged app (the app sets SPOTIFLAC_RIP to this binary).
pyinstaller build/rip_entry.py --name rip --onefile --noconfirm \
  --distpath dist/spotiflac-sidecar --workpath build/work \
  --collect-all streamrip || echo "WARN: rip CLI not bundled (lossless extra missing)"

echo "==> [3/3] Building the Electron app + DMG"
cd app
npm install
npm run dist:mac

echo
echo "Done. Artifacts are in app/dist/ (.dmg and .zip)."
echo "If you didn't sign, right-click the app and choose Open the first time"
echo "to get past Gatekeeper, or sign + notarize (see build/NOTARIZE.md)."
