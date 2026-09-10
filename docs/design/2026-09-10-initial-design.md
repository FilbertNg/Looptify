# Looptify — Design

**Date:** 2026-09-10
**Status:** Approved for planning
**Repository:** https://github.com/FilbertNg/Looptify

> Drafted under the working title "Spotify Looper"; the filename preserves that for
> git history. The project name is **Looptify**.

## Problem

Spotify Free on Windows desktop plays an audio ad after a track finishes. Pressing the
previous-track button shortly before the track ends restarts it without registering a
completion, so no ad plays. Done by hand this means watching the progress bar and
catching the last second — easy to miss.

## Goal

A background Windows tool that watches Spotify's playback position and fires the
previous-track command ~1.5 s before the track ends, looping the current song
indefinitely while armed. Armed and disarmed with a global hotkey.

## Non-goals

- Playback control beyond loop and mute (no queue, playlist, or library manipulation)
- Network-level ad blocking or patching the Spotify client
- Cross-platform support (Windows only)
- Anything requiring Spotify Premium or a Spotify developer application

## Verified environment (2026-09-10)

| Fact | Value |
|---|---|
| OS | Windows 11 Home Single Language 10.0.26200 |
| Python | 3.12.9, pip 25.2 |
| Already installed | `pywin32` 310, `psutil` 7.0.0 |
| Spotify build | **Microsoft Store** (`SpotifyAB.SpotifyMusic_zpdnekdrzrea0`), process name `Spotify.exe` |
| Crossfade | **Off** — no crossfade key in the Store build's `LocalState/Spotify/prefs` |

The Store build runs in an AppContainer. This does not affect SMTC (a system-wide
broker) and should not affect per-process audio session lookup, since the process is
still a normal `Spotify.exe` with its own PID — but per-process mute is the one place
where the Store packaging could surprise us, so it is verified explicitly during
implementation.

## Approach

### Data source: Windows System Media Transport Controls

Spotify publishes playback state to SMTC — the OS media feed behind the Windows media
overlay. `GlobalSystemMediaTransportControlsSessionManager` exposes, per session:
title, artist, album, playback status, and timeline properties (position, end time,
last-updated timestamp).

Alternatives rejected:

- **Spotify Web API** (`GET /v1/me/player`) — requires OAuth app registration, adds
  100–300 ms network latency per poll, is rate limited, and its control endpoints
  (seek, previous) are Premium-only. SMTC would still be needed for the action.
- **Reading the UI** (UI Automation or OCR of the `0:43 / 3:17` text) — brittle against
  any Spotify redesign, and requires the window to be visible.

### Loop action

`TrySkipPreviousAsync()` on the Spotify SMTC session, rather than sending a global
`VK_MEDIA_PREV_TRACK` key, which Windows may route to a different media app. Because
the command fires near the end of the track (position well past 3 s), Spotify's
previous-track behaviour restarts the current track rather than moving back one.

At startup the tool probes `TryChangePlaybackPositionAsync(0)`. If Spotify honours it,
seeking to 0:00 is preferred — it is more precise and does not depend on Spotify's
"restart if past 3 s" rule. Skip-previous remains the fallback.

### Timing

SMTC does not tick. It pushes an update only on events (play, pause, seek, track
change), so a naive position read can be up to a second stale. Position is therefore
extrapolated:

```
estimated_position = Position + (now_utc - LastUpdatedTime)   # only while status == Playing
remaining          = EndTime - estimated_position
fire when remaining <= lead_seconds
```

Poll interval 150 ms. `lead_seconds` defaults to 1.5.

Guards:

- Act only when playback status is `Playing`.
- Ignore a session whose end time is zero or absent.
- After firing, enter a cooldown (default 3 s) and require the observed position to
  fall below 5 s before the next fire is allowed. Both conditions must hold. This
  prevents a double-fire within one pass.
- Never loop an item detected as an ad.

If the user later enables Crossfade, `lead_seconds` must exceed the crossfade duration,
since the next track begins before the current one ends. This is documented in the
config file rather than auto-detected.

### Ad handling

When the current SMTC item is detected as an ad, Spotify's audio session is muted via
per-process volume control (`pycaw`), and unmuted when a real track returns. Muting
applies whether or not the looper is armed. The ad still plays; it plays silently.

Ad detection is **derived from observed data, not guessed**. Spotify reports ads
through SMTC with distinguishing field values (typically a blank artist, or a title
such as "Advertisement"), but the exact fields vary by client version. Implementation
therefore includes a logging mode that records every SMTC field on track change; the
detector is written against a captured real ad.

Concretely: the config holds an `ad_markers` list, and an empty list means detection is
off and nothing is ever muted. The list ships empty. It is populated only after a real
ad has been captured through the logging mode, so the tool cannot mute real music on
the strength of a guess.

### Activation

Global hotkey **Ctrl+Alt+L** toggles ARMED / IDLE, registered with `RegisterHotKey`
via `pywin32` — no administrator rights and no low-level keyboard hook. The hotkey
runs on its own thread with a message pump. The combination is configurable.

While ARMED, whatever song is currently playing loops indefinitely; if the user
manually skips, the tool re-arms on the new track. While IDLE, tracks play out
normally.

## Structure

Windows-specific I/O is confined to thin adapters. All decision logic is pure functions
that can be unit-tested without Spotify running.

```
looptify/
  __main__.py  # python -m looptify
  logic.py     # PURE: extrapolate_position(), should_loop(), is_ad(), state machine
  smtc.py      # adapter: read session, skip-previous / seek
  audio.py     # adapter: per-process mute of Spotify.exe (pycaw)
  hotkey.py    # adapter: global hotkey via RegisterHotKey (pywin32)
  cli.py       # console loop: ARMED/IDLE, current track, live countdown to loop point
config.toml    # lead_seconds, hotkey, poll_interval, ad markers
tests/
  test_logic.py
run.bat
requirements.txt
```

| Unit | Does | Depends on |
|---|---|---|
| `logic.py` | Given a timeline snapshot and current state, decides whether to loop, mute, or do nothing | nothing (pure) |
| `smtc.py` | Returns a snapshot dataclass; issues loop commands | `winsdk` |
| `audio.py` | Mute/unmute Spotify by PID | `pycaw`, `psutil` |
| `hotkey.py` | Calls a callback when the hotkey fires | `pywin32` |
| `cli.py` | Wires the above; renders console status | all of the above |

The snapshot dataclass is the interface between the adapter and the logic, which is
what lets the timing rules be tested against synthetic values.

## Dependencies to add

- `winsdk` — SMTC bindings. If it does not install cleanly on Python 3.12, fall back to
  the official `winrt-runtime` + `winrt-Windows.Media.Control` projection packages.
- `pycaw` + `comtypes` — per-process audio session volume.

`pywin32` and `psutil` are already present.

## Testing

Unit-tested (pure, no Spotify required):

- `extrapolate_position` — including a paused session, where extrapolation must not
  advance, and a stale last-updated timestamp
- `should_loop` — fires inside the lead window, does not fire outside it, does not fire
  when paused, does not fire on unknown duration, does not fire twice within cooldown,
  re-arms after position returns to zero
- `is_ad` — against captured real SMTC field values, plus real tracks that must not
  match (notably tracks with unusual or missing metadata)

Manually verified (requires Spotify):

- SMTC snapshot reports a plausible position and duration for a playing track
- Skip-previous restarts the current track; whether seek-to-zero is honoured
- Per-process mute affects only Spotify, given the Store packaging
- Hotkey registers and toggles without administrator rights
- End-to-end: an armed track loops at the boundary with no ad

## Delivery

Console version first, with live status showing ARMED/IDLE, current track, and a
countdown to the loop point — so the timing logic can be observed and tuned directly.
A system-tray wrapper (`pystray`) is a possible follow-up, not part of this scope.

## Risks

| Risk | Mitigation |
|---|---|
| Spotify's SMTC position is coarser than assumed | Extrapolation plus a 1.5 s lead absorbs roughly a second of staleness; lead is configurable and tuned against observed behaviour |
| Ad detection false-positives, muting real music | Detector written only against captured real ad data; ships disabled until an ad is captured |
| Store-app packaging blocks per-process mute | Verified explicitly; if blocked, fall back to muting the whole session or dropping the mute feature |
| Looping still registers a play and triggers an ad | The manual version of this trick already works for the user, so the behaviour is established; confirmed end-to-end |

---

## Open-source release

The project is published at `github.com/FilbertNg/Looptify` for people who are tired of
Spotify ads and like looping a song on repeat. This phase runs **after** the tool works
end-to-end — the README needs real screenshots and real behaviour to describe, not
aspirational ones.

### Naming

The repository is **Looptify**, so the code adopts that name: package `looptify/`,
entry point `python -m looptify`, config `config.toml` at the repo root. The working
title "Spotify Looper" is dropped everywhere except this document's history.

### Repository files

| File | Purpose |
|---|---|
| `README.md` | The landing page. Structure below. |
| `LICENSE` | **MIT** — recommended default for a small utility; the owner may choose otherwise before release |
| `.gitignore` | Python: `__pycache__/`, `*.pyc`, `.venv/`, `.pytest_cache/`, `dist/`, plus a local `config.toml` override if one is added |
| `CONTRIBUTING.md` | Short: dev setup, how to run `pytest`, what contributions are wanted |
| `.github/workflows/tests.yml` | CI on `windows-latest` running `pytest` — the logic tests are pure, so they pass in CI without Spotify installed |
| `docs/assets/` | Screenshot and demo GIF referenced by the README |

CI is worth the small effort here: a green check on a repo that automates a media
player is a credible signal that the logic is actually tested, and the pure-function
split in this design is exactly what makes it possible.

### README structure

1. **Title, one-line pitch, badges** — license, Python version, platform
2. **Demo** — a GIF of the console showing the countdown and the loop firing at the
   boundary. This is the single most persuasive element; it goes near the top.
3. **What it does** — the manual previous-button trick, automated, with the timing
   explained in two sentences
4. **Requirements** — Windows 10/11, Python 3.10+, Spotify desktop (Store or standalone
   build), no Premium and no Spotify developer account
5. **Install** — clone, `pip install -r requirements.txt`, `run.bat`
6. **Usage** — Ctrl+Alt+L to arm, what ARMED and IDLE mean, reading the console output
7. **Configuration** — table of every key in `config.toml`, including the explicit
   warning that Crossfade requires raising `lead_seconds` above the crossfade duration
8. **How it works** — SMTC as the data source, why position must be extrapolated from
   `LastUpdatedTime`, why `TrySkipPreviousAsync` on the Spotify session beats a global
   media key. Written for a developer skimming to judge whether the approach is sound.
9. **Troubleshooting** — Spotify not detected, hotkey already taken by another app,
   loop firing too early or too late, ad muting doing nothing because `ad_markers` is
   still empty
10. **Scope and limitations** — stated plainly: Looptify sends documented Windows media
    commands and nothing else. It does not patch or modify the Spotify client, inject
    into its process, block network requests, or alter audio streams. It automates a
    keypress the user could perform by hand. Note that Spotify's terms discourage
    circumventing ads, and that Premium remains the way to support artists directly.
    This section is honesty first, and it also stops the repo from reading as an
    ad-blocker, which it technically is not.
11. **Contributing** and **License**

### GitHub-side presentation

- **About / description:** one line, e.g. "Loops the current Spotify track just before
  it ends, so the post-track ad never fires. Windows, Python, no Premium needed."
- **Topics:** `spotify`, `windows`, `python`, `automation`, `smtc`, `media-controls`,
  `loop`, `no-ads`
- **Release `v0.1.0`** tagged once the end-to-end verification passes
- **Issue templates:** one bug-report template asking for Windows version, Spotify build
  (Store vs standalone), and the console output — the three things needed to diagnose
  anything here. A feature template is not worth it at this size.
- Commit style stays conventional (`feat:`, `fix:`, `docs:`), matching the existing
  `docs: plan` commit.

### Ordering

Repository polish is the final phase of the implementation plan. The README's demo GIF,
screenshots, troubleshooting entries, and the populated `ad_markers` list all depend on
having run the tool against real playback, so writing them earlier would mean writing
them twice.
