# Spotiflac

A friendly, **stable** desktop app for downloading your Spotify music — a fork
of [spotdl/spotify-downloader](https://github.com/spotdl/spotify-downloader)
with a native macOS UI and a hardening layer built for **long, unattended
runs** (think multi-thousand-track libraries, overnight).

> Spotiflac uses the same approach as spotdl: it reads track metadata from
> Spotify and downloads matching audio from YouTube Music / other providers.
> Only download content you have the right to.

---

## What this fork adds

| | spotdl | Spotiflac |
|---|---|---|
| Interface | CLI (+ datastar web) | **Electron desktop app** (macOS) |
| Long-run resume | download archive | **Per-job checkpoints** — resume after a crash/quit |
| Failures | logged | **Retry w/ backoff + per-track attempt budget + failure report** |
| Rate limits | yt-dlp retries | **Adaptive, process-wide throttle** that backs off on 429s |
| Memory | unbounded over time | **Bounded batches + memory watchdog** |
| Profiles | n/a | **Look up a user and browse their public playlists** |

The upstream `spotdl/` package is vendored **unmodified** so the fork stays
easy to rebase on new releases. Everything new lives in two packages alongside
it: `spotiflac/` (the stability engine) and `server/` (the local API the UI
talks to), plus `app/` (the Electron front-end).

---

## Architecture

```
┌──────────────────────────┐     HTTP + WebSocket      ┌────────────────────────┐
│  Electron app (app/)      │ ───────────────────────▶ │  Python sidecar         │
│  • renderer UI            │  127.0.0.1 (local only)   │  (server/ → FastAPI)    │
│  • spawns + supervises    │ ◀─── live progress ────── │  • JobManager           │
│    the sidecar            │                           │  • WebSocket broadcast  │
└──────────────────────────┘                           └───────────┬────────────┘
                                                                    │
                                                        ┌───────────▼────────────┐
                                                        │  spotiflac/ engine      │
                                                        │  checkpoint · retry ·   │
                                                        │  resources · jobs       │
                                                        └───────────┬────────────┘
                                                                    │
                                                        ┌───────────▼────────────┐
                                                        │  spotdl/ (vendored)     │
                                                        └─────────────────────────┘
```

The sidecar binds `127.0.0.1` only and the Electron main process supervises its
lifecycle (spawns it, waits for `SPOTIFLAC_READY <port>`, kills it on quit).

---

## The stability layer (`spotiflac/`)

Long runs fail in boring, predictable ways: the network blips, a provider
rate-limits you, the machine sleeps, the app is quit halfway. Spotiflac
addresses each:

- **`checkpoint.py`** — every track's outcome is persisted to an **atomically
  written** JSON file keyed deterministically by `(query, output)`. Re-running
  the same job skips what already succeeded. A corrupt checkpoint is quarantined
  and the run continues rather than crashing.
- **`retry.py`** — `retry_call()` wraps flaky calls with exponential backoff +
  jitter, honouring `Retry-After`. `AdaptiveRateLimiter` is a process-wide gate
  that slows everything when 429s appear and decays back to full speed when they
  stop — additive-increase / multiplicative-decrease.
- **`resources.py`** — a conservative default thread count, plus a background
  `MemoryWatchdog` that logs RSS, forces GC past a soft limit, and signals the
  orchestrator to shrink batch size + flush state past a hard limit.
- **`jobs.py`** — the orchestrator. Searches (with retry), filters out
  already-done tracks, downloads in **bounded batches**, checkpoints after each
  batch, retries failures up to a budget, and emits structured progress events.
  Pausable and cancellable at batch boundaries. It also clears spotdl's
  class-level progress map between batches to stop it growing over a long run.
- **`logging_setup.py`** — a rotating per-job log and a machine- + human-readable
  **failed-tracks report** written when the job ends.

These pieces are covered by `tests_spotiflac/` and need no network or ffmpeg.

---

## Develop

Requires Python 3.10–3.14, Node 18+, and ffmpeg available at runtime.

```bash
# Python side
python3 -m venv .venv && source .venv/bin/activate
pip install -e .

# Run the sidecar on its own (great for API testing)
python -m server          # prints SPOTIFLAC_READY <port>, serves on 127.0.0.1

# Run the desktop app in dev (spawns the sidecar via your python)
cd app && npm install && npm run dev
```

Run the stability tests:

```bash
pip install pytest && python -m pytest tests_spotiflac/ -q
```

---

## Build the macOS app

On a Mac with Xcode command-line tools:

```bash
./build/build_mac.sh
```

This freezes the Python sidecar with PyInstaller
(`build/spotiflac-sidecar.spec`) and bundles it inside an Electron app + DMG via
`electron-builder`. The hardened runtime + entitlements are configured for the
PyInstaller bootloader and network access. Signing and notarization are
optional and documented in [`build/NOTARIZE.md`](build/NOTARIZE.md).

> This repository is developed in a Linux container, so the `.app`/`.dmg` must
> be produced on macOS — the scripts and entitlements are ready to run there.

---

## API (sidecar)

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/health` | liveness + version |
| `POST` | `/api/credentials` | set Spotify credentials (optional; sensible defaults) |
| `POST` | `/api/jobs` | start a download job |
| `GET` | `/api/jobs` / `/api/jobs/{uid}` | list / inspect jobs |
| `POST` | `/api/jobs/{uid}/{pause,resume,cancel}` | control a job |
| `GET` | `/api/profile?user=…` | resolve a profile + list its public playlists |
| `WS` | `/ws` | live progress events |

---

## Profiles

Enter a Spotify **username** or **profile link** and Spotiflac lists that user's
public playlists, each downloadable with one click. Note: the Spotify Web API
has **no free-text user search**, so you look people up by handle/link rather
than by display name.

---

## Credits & license

Built on the excellent [spotDL](https://github.com/spotdl/spotify-downloader).
MIT licensed, same as upstream — see [`LICENSE`](LICENSE).

---

<details>
<summary>Upstream spotdl documentation</summary>

The original spotdl CLI remains fully available (`spotdl ...`). See the
[spotDL docs](https://spotdl.rtfd.io) for CLI usage, configuration, and audio
provider details.

</details>
