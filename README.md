<div align="center">

# Looptify

**Loops the current Spotify track just before it ends, so the post-track ad never fires.**

[![tests](https://github.com/FilbertNg/Looptify/actions/workflows/tests.yml/badge.svg)](https://github.com/FilbertNg/Looptify/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
![platform: Windows](https://img.shields.io/badge/platform-Windows-blue.svg)
![no premium needed](https://img.shields.io/badge/Spotify-Free%20account-1db954.svg)

</div>

---

## Demo

<!--
  To show an inline video player instead of the link below:
  open a new GitHub issue on this repo, drag docs/assets/demo.mp4 into the
  comment box, wait for it to upload, then paste the resulting
  https://github.com/user-attachments/... URL here on its own line.
  GitHub renders that as a real player. Relative .mp4 paths do not work.
-->

**[▶ Watch the demo](docs/assets/demo.mp4)** — armed, counting down, looping at the boundary.

```console
Looptify — ctrl+alt+l to arm/disarm. Ctrl+C or close this window to quit.
Ad muting: ON — artist in ['Spotify'] or no album/track and under 60s

[ARMED ] ▶ Steve Lacy - oh yeah?                    2:47/2:50  loop in   1.4s
[loop] restarted via seek
[ARMED ] ▶ Steve Lacy - oh yeah?                    0:01/2:50  loop in 169.0s
```

## The problem

Spotify Free plays an ad after a track finishes. The well-known workaround is to hit
previous-track a moment before the song ends — the track restarts, Spotify never
registers a completion, and no ad plays.

The catch is you have to *notice* the song ending and react in time. Miss it by a
second and you get the ad anyway.

Looptify watches the playback position and does it for you, about 1.5 seconds before
the end, every time.

## Install

```bash
git clone https://github.com/FilbertNg/Looptify.git
cd Looptify
pip install -r requirements.txt
```

**Requirements:** Windows 10/11 · Python 3.11+ · Spotify desktop (Store or standalone).
A **free** Spotify account is fine — no Premium, no developer account, no API key, no login.

## Usage

Start Spotify, play something, then run `python -m looptify` — or double-click `run.bat`.

**Looptify starts armed**, so whatever is playing begins looping straight away.

| | |
|---|---|
| **Ctrl+Alt+L** | Arm / disarm, from anywhere — Looptify doesn't need focus |
| **Ctrl+C**, or closing the window | Quit, cleanly |

Every time you arm or disarm, a notification fades in at the corner of the screen —
**Looptify Activated** or **Looptify Deactivated** — holds for three seconds, then
fades out. It runs silently in the background otherwise, so that's usually the only
way to tell what state it's in.

**ARMED** loops the current song indefinitely and shows a countdown to the next loop.
Skip to a different song and it loops that one instead. **IDLE** just watches; tracks
play out normally.

## How it works

Spotify publishes playback state to **SMTC** (System Media Transport Controls), the
Windows API behind the media overlay. Looptify reads position, duration and play state
from it. No network calls, no Spotify API, no reading the screen.

#### SMTC doesn't tick

It pushes an update only on events, and on Spotify that turns out to be roughly every
**4.5 seconds** — measured staleness reached **4.489s**. Polling the reported position
and firing at "1.5 seconds remaining" would fire *three seconds after* the track had
already ended. The tool would look correct and never work.

So the position is extrapolated instead. Every timeline update carries the timestamp it
was stamped at, and while playback runs the true position is that value plus the elapsed
time since:

```python
estimated_position = position + (now − last_updated)
```

Measured against real playback, this predicts the true position to within about **20 ms**
across a full 4.5-second gap — which is what makes a 1.5-second lead safe. You can watch
it work: `position` sits frozen for seconds while the estimate climbs smoothly, then the
next push lands exactly where the estimate predicted.

#### Restarting the track

Looptify seeks to 0:00 through SMTC. That's exact, and unlike pressing previous-track it
doesn't depend on Spotify's "restart if past 3 seconds" rule. If a client doesn't support
seeking it falls back to skip-previous. Both target Spotify's own media session rather
than broadcasting a global media key, so neither can hit a different media app.

Two guards stop one pass through the end of a track from firing repeatedly: a cooldown,
and a requirement that playback is actually observed back near the start.

#### Structure

Every Windows API call lives in a thin adapter; every decision lives in one pure module.

```
looptify/
├── logic.py     ← pure: extrapolation, ad detection, the fire decision
├── smtc.py         adapter: read playback state, seek
├── audio.py        adapter: mute Spotify
├── hotkey.py       adapter: global hotkey
├── toast.py        adapter: on-screen notifications
├── console.py      adapter: encoding, clean shutdown
└── cli.py          poll loop and wiring
```

That split is why the timing rules can be tested against synthetic values instead of
waiting three minutes for a real song to end — and why CI runs the whole suite on a
machine with no Spotify installed.

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
| `start_armed` | `true` | Begin looping as soon as Looptify opens |
| `show_notifications` | `true` | Show the on-screen arm/disarm notification |
| `notification_seconds` | `3.0` | How long it holds, excluding fades |
| `ad_markers` | `["Spotify"]` | Artist names identifying an ad, matched exactly |
| `detect_ads_by_structure` | `true` | Also detect ads by shape — no album, no track number, short |
| `ad_max_duration_seconds` | `60.0` | Longest an ad can be, for the rule above |
| `log_tracks` | `false` | Log every track change with its raw media fields |

> [!IMPORTANT]
> **Using Crossfade?** With Crossfade on (Settings → Playback), the next track begins
> *before* the current one ends. Raise `lead_seconds` above your crossfade duration or
> the loop will fire too late.

## Ad muting

If an ad slips through — you left Looptify disarmed, or Spotify fired a mid-session ad
break — Looptify mutes Spotify while it plays and unmutes when real music returns. The
ad still plays, silently.

Detection uses two independent rules, either of which is enough. Both were built from
real captured ads rather than guessed.

**By artist.** Spotify's house ads report `artist='Spotify'`:

```
title='Dengarkan musik tanpa iklan.'  artist='Spotify'  album=''  track_number=0
title='Nikmati musik tanpa iklan.'    artist='Spotify'  album=''  track_number=0
```

Both from the same break. The titles differ and are localised — those are Indonesian —
so a title can never be a reliable marker, while the artist stays `Spotify`. Matching is
**exact**, because a substring search for "Spotify" would also match legitimate releases:
*Spotify Singles* is a real series of over 1,000 tracks, and *Spotify Sessions* EPs exist
from Dua Lipa, Sia and Twenty One Pilots. Muting those would be muting real music.

**By structure.** A list of artist names can only catch Spotify's own ads — a third-party
advertiser reports its own brand, and no list can enumerate every advertiser. But ads are
structurally different from catalogue tracks regardless of who made them:

| | Ad | Real track |
|---|---|---|
| `album` | *(empty)* | `mosi mosi?` |
| `track_number` | `0` | `1` |
| `duration` | 14–30s | 163s |

Every real Spotify track belongs to a release, so it carries an album and a track number.
Ads carry neither and run short. All three conditions must hold, which keeps an untagged
local file from being muted unless it's also very short. This rule needs no names, so it
works in any country and any language.

To capture your own, set `log_tracks = true`, run **disarmed**, and wait for an ad — a
`[track] …` line prints its real fields. Add the artist to `ad_markers`, then set
`log_tracks = false`. Setting `ad_markers = []` *and* `detect_ads_by_structure = false`
turns muting off entirely.

Looptify also clears any leftover mute on startup, so a run killed mid-ad can't leave
Spotify permanently silent.

## Troubleshooting

| Symptom | Fix |
|---|---|
| `waiting for Spotify...` never clears | Spotify must be running **and** have played something this session — it doesn't publish a media session until then |
| `Could not register hotkey` | Another app owns that combination. Change `hotkey` in `config.toml` |
| An ad played anyway | The loop fired too late. Raise `lead_seconds` to `2.5`. With Crossfade on, raise it above your crossfade duration |
| Track cuts off noticeably early | Lower `lead_seconds` toward `1.0` |
| Ad muting does nothing | Check the `Ad muting:` line at startup — it names the rules that are live |
| It loops twice in a row | Raise `cooldown_seconds`, and please [open an issue](https://github.com/FilbertNg/Looptify/issues) — that shouldn't happen |

## Scope and limitations

Looptify sends documented Windows media commands and nothing else. It does **not** patch,
modify or inject into the Spotify client, block or intercept network traffic, modify audio
streams, or touch your account, credentials or library. It automates a keypress you could
perform by hand. That's the whole tool.

Spotify's terms discourage circumventing ads, so use your own judgement. If you want to
support the artists you listen to, Premium is the direct way to do it.

**v0.1.0 has only been exercised on one machine**, against the Microsoft Store build of
Spotify, in one country. The standalone installer and other markets should work but are
untested — [bug reports](https://github.com/FilbertNg/Looptify/issues) very welcome.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The design record from before implementation is
in [docs/design/](docs/design/), if you want the reasoning behind the technical choices.

```bash
pip install -r requirements-dev.txt
python -m pytest tests/
```

## License

[MIT](LICENSE) © 2026 Filbert Ng
