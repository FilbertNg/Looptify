# Looptify

**Loops the current Spotify track just before it ends, so the post-track ad never fires.**

[![tests](https://github.com/FilbertNg/Looptify/actions/workflows/tests.yml/badge.svg)](https://github.com/FilbertNg/Looptify/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python: 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
![platform: Windows](https://img.shields.io/badge/platform-Windows-blue.svg)

---

## Demo

```
Looptify — press ctrl+alt+l to arm/disarm, Ctrl+C to quit.
Ad muting is OFF (ad_markers is empty in config.toml).

[ARMED ] ▶ Steve Lacy - oh yeah?                    2:47/2:50 loop in   1.4s
[loop] restarted via seek
[ARMED ] ▶ Steve Lacy - oh yeah?                    0:01/2:50 loop in 169.0s
```

## What it does

If you use Spotify Free on desktop, an ad plays after a track finishes. The
well-known workaround is to hit the previous-track button a moment before the song
ends — the track restarts, Spotify never registers a completion, and no ad plays.

The problem is you have to *notice* the song ending and react in time. Miss it by a
second and you get the ad.

Looptify watches the playback position and does it for you. Press the hotkey to arm
it, and roughly 1.5 seconds before the track ends it seeks back to 0:00. The song
loops forever until you disarm it.

## Requirements

- **Windows 10 or 11** — Looptify uses the Windows media API
- **Python 3.11+**
- **Spotify desktop** — either the Microsoft Store build or the standalone installer
- **Spotify Free is fine.** No Premium, no developer account, no API key, no login

## Install

```bash
git clone https://github.com/FilbertNg/Looptify.git
cd Looptify
pip install -r requirements.txt
```

## Usage

Start Spotify, play something, then run:

```bash
python -m looptify
```

or double-click `run.bat`.

| | |
|---|---|
| **Ctrl+Alt+L** | Arm / disarm. Works even when Looptify isn't the focused window. |
| **Ctrl+C** | Quit. |

**IDLE** means Looptify is watching but doing nothing — tracks play out normally.
**ARMED** means the current song will loop indefinitely, and a countdown to the next
loop appears. Skip to a different song while armed and it loops that one instead.

## Configuration

Edit `config.toml`. Delete any line to use its default.

| Key | Default | What it does |
|---|---|---|
| `lead_seconds` | `1.5` | Fire the loop this many seconds before the track ends |
| `cooldown_seconds` | `3.0` | Minimum gap between two loops |
| `restart_threshold_seconds` | `5.0` | Playback must fall below this before another loop is allowed |
| `max_drift_seconds` | `10.0` | Ignore playback data staler than this |
| `poll_interval` | `0.15` | How often to check, in seconds |
| `hotkey` | `"ctrl+alt+l"` | Arm/disarm key. Modifiers: `ctrl`, `alt`, `shift`, `win` |
| `ad_markers` | `["Spotify"]` | Artist names identifying an ad, matched exactly. **Empty disables muting** |
| `log_tracks` | `false` | Log every track change with its raw media fields |

> **Using Crossfade?** If you have Crossfade enabled in Spotify (Settings →
> Playback), the next track begins *before* the current one ends. Raise
> `lead_seconds` above your crossfade duration or the loop will fire too late.

### Ad muting

If an ad slips through — you left Looptify disarmed, or Spotify fired a mid-session
ad break — Looptify mutes Spotify while it plays and unmutes when real music returns.
The ad still plays, silently.

Detection matches the **artist** field exactly, against `ad_markers`. That's a
deliberately narrow rule, built from real captured ads rather than guessed:

```
title='Dengarkan musik tanpa iklan.'  artist='Spotify'  album=''
title='Nikmati musik tanpa iklan.'    artist='Spotify'  album=''
```

Both are from the same ad break. The titles differ and are localised — those are
Indonesian — so the title is useless as a marker, while the artist stays `Spotify`.
And matching exactly rather than by substring is what stops a legitimate
**Spotify Singles** release from being muted as an ad.

If your client reports ads differently, capture yours:

1. Set `log_tracks = true` and run Looptify **disarmed**
2. Wait for an ad — a `[track] ...` line prints with its real fields
3. Add the artist it reports to `ad_markers`
4. Set `log_tracks = false`

Setting `ad_markers = []` turns muting off entirely.

## How it works

Spotify publishes playback state to **SMTC** (System Media Transport Controls), the
Windows API behind the media overlay. Looptify reads position, duration and play state
from it. No network calls, no Spotify API, no reading the screen.

The interesting part is that **SMTC does not tick.** It pushes an update only on
events, and on Spotify that turns out to be roughly every 4.5 seconds — position
readings were measured up to **4.489 s stale**. Polling the reported position naively
and firing at "1.5 seconds remaining" would fire *three seconds after* the track had
already ended.

So Looptify extrapolates instead. Every timeline update carries the timestamp it was
stamped at, and while playback is running the real position is that value plus the
elapsed time since:

```
estimated_position = position + (now − last_updated)
```

Measured against real playback, this predicts the true position to within about
**20 ms** across a full 4.5-second gap, which is what makes a 1.5-second lead safe.

To restart the track, Looptify seeks to 0:00 via SMTC. That is exact, and unlike
pressing previous-track it doesn't depend on Spotify's "restart if past 3 seconds"
rule. If a client doesn't support seeking, it falls back to skip-previous. Both are
aimed at Spotify's own media session rather than broadcast as a global media key,
so they can't hit a different media app.

Two guards stop a single pass through the end of a track from firing repeatedly: a
cooldown, and a requirement that playback is actually observed back near the start.

## Troubleshooting

**"waiting for Spotify..." never goes away**
Spotify must be running *and* have played something this session — it doesn't publish
a media session until then.

**"Could not register hotkey"**
Another application owns that combination. Change `hotkey` in `config.toml`.

**An ad played anyway**
The loop fired too late. Raise `lead_seconds` to `2.5` and try again. If you have
Crossfade enabled, raise it above your crossfade duration.

**The track cuts off noticeably early**
Lower `lead_seconds` toward `1.0`.

**Ad muting does nothing**
`ad_markers` is empty, which is the default. See [Ad muting](#ad-muting).

**It loops twice in a row**
Raise `cooldown_seconds`. Please also open an issue — that shouldn't happen.

## Scope and limitations

Looptify sends documented Windows media commands and nothing else. Specifically, it
does **not**:

- patch, modify, or inject into the Spotify client
- block, filter, or intercept any network traffic
- modify audio streams
- touch your account, credentials, or library

It automates a keypress you could perform by hand. That is the whole tool.

Note that Spotify's terms discourage circumventing ads, so use your own judgement.
If you want to support the artists you listen to, Premium is the direct way to do it.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Bug reports and pull requests are welcome.

## License

[MIT](LICENSE) © 2026 Filbert Ng
