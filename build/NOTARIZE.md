# Signing & notarizing Spotiflac for macOS

The build produces a working `.app`/`.dmg` without signing, but unsigned apps
trip Gatekeeper ("Spotiflac can't be opened because Apple cannot check it").
For distribution you'll want to sign and notarize.

## 1. Prerequisites

- An Apple Developer account ($99/yr).
- A **Developer ID Application** certificate in your login keychain.
- App-specific password for your Apple ID (appleid.apple.com → Security).

## 2. Sign

electron-builder signs automatically when it finds a certificate. Either let it
read the keychain, or point it at an exported `.p12`:

```bash
export CSC_LINK="$HOME/certs/developerID.p12"
export CSC_KEY_PASSWORD="••••••"
```

The hardened runtime is already enabled in `app/package.json`, using
`build/entitlements.mac.plist` (allows the PyInstaller bootloader + network).

## 3. Notarize

electron-builder will submit to Apple's notary service when these are set:

```bash
export APPLE_ID="you@example.com"
export APPLE_APP_SPECIFIC_PASSWORD="abcd-efgh-ijkl-mnop"
export APPLE_TEAM_ID="ABCDE12345"
```

Then run the normal build:

```bash
./build/build_mac.sh
```

## 4. Verify

```bash
spctl --assess --type execute --verbose "app/dist/mac-arm64/Spotiflac.app"
codesign --verify --deep --strict --verbose=2 "app/dist/mac-arm64/Spotiflac.app"
```

Both should report `accepted` / `valid on disk`.

## Notes

- The Python sidecar inside `Contents/Resources/sidecar` is signed as part of
  the deep signing pass; `disable-library-validation` is required because the
  bundled native wheels (yt-dlp, rapidfuzz, pydantic-core) aren't signed by us.
- Universal builds: the spec freezes for the architecture you build on. To ship
  both, build the sidecar on an Apple-silicon and an Intel machine (or under
  `arch -x86_64`) and run `electron-builder --mac --arm64 --x64` accordingly.
