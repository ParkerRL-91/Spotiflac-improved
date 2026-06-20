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
pip install -e .
pip install pyinstaller

echo "==> [2/3] Freezing the sidecar"
rm -rf build/work dist/spotiflac-sidecar
pyinstaller build/spotiflac-sidecar.spec --noconfirm \
  --distpath dist --workpath build/work

echo "==> [3/3] Building the Electron app + DMG"
cd app
npm install
npm run dist:mac

echo
echo "Done. Artifacts are in app/dist/ (.dmg and .zip)."
echo "If you didn't sign, right-click the app and choose Open the first time"
echo "to get past Gatekeeper, or sign + notarize (see build/NOTARIZE.md)."
