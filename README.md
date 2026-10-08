<div align="center">

<img src="docs/assets/logo/looptify-logo.svg" alt="Looptify logo" width="112">

# Looptify

**Loops the current Spotify track — or moves on through your playlist — just before it ends, so the post-track ad never fires.**

[![tests](https://github.com/FilbertNg/Looptify/actions/workflows/tests.yml/badge.svg)](https://github.com/FilbertNg/Looptify/actions/workflows/tests.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![python: 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
![platform: Windows](https://img.shields.io/badge/platform-Windows-blue.svg)
![no premium needed](https://img.shields.io/badge/Spotify-Free%20account-1db954.svg)

</div>

---

https://github.com/user-attachments/assets/81c370c3-fafb-4ec4-a8bb-ed813ac63b5e

## What it does

Spotify Free plays an ad after a track finishes. If the song is restarted, or a different
song is started, a moment *before* it finishes, Spotify never registers the ending and no
ad plays. Doing that by hand means watching every song and reacting within a second.

Looptify does it for you, about 1.5 seconds before the end of every song. It can repeat
the current song, or move through the playlist you have open: in order, shuffled, or at
random.

## Requirements

- Windows 10 or 11
- Python 3.11 or newer
- The Spotify desktop app (Microsoft Store or standalone installer)

A free Spotify account is all you need: no Premium, no developer account, no API key, and
no login.

## Install

```bash
git clone https://github.com/FilbertNg/Looptify.git
cd Looptify
pip install -r requirements.txt
```

## Quick start

1. Open Spotify and play a song.
2. Run `python -m looptify`, or double-click `run.bat`.

Looptify starts **armed**: the song that's playing will repeat instead of ending. Leave the
console window open; Looptify works in the background and doesn't need focus.

| Shortcut | Action |
|---|---|
| **Ctrl+Alt+L** | Arm or disarm |
| **Ctrl+Alt+Shift+L** | Change mode |
| **Ctrl+C**, or close the window | Quit |

A notification appears in the corner of the screen whenever you arm, disarm or change
mode. When disarmed, Looptify only watches and songs play out normally.

## Modes

| Mode | Just before the song ends |
|---|---|
| **Loop** (default) | Restarts the same song |
| **In Order** | Plays the next song in the playlist, returning to the first after the last |
| **Shuffle Loop** | Plays a random song you haven't heard this round; nothing repeats until every song has played |
| **Pure Random** | Plays any song at random, which can be the same one again |

Press **Ctrl+Alt+Shift+L** to switch modes, or set `mode` in `config.toml` to choose the
mode Looptify starts in.

### Using In Order, Shuffle Loop or Pure Random

These modes start the next song the same way you would: by pressing its play button in
the playlist. To make that possible:

- **Spotify restarts once.** The first time you switch to one of these modes, Looptify
  restarts Spotify with the settings it needs. Press play again when Spotify reopens.
- **Keep the playlist open in Spotify.** The next song comes from the playlist on screen
  in Spotify. If you browse to another page, Looptify repeats the current song until you
  return to a playlist.
- **Keep Spotify open, not minimized.** It can sit behind other windows or on another
  monitor. While Spotify is minimized, Looptify may have to repeat the current song
  instead of moving on.

Spotify stays out of sight when a song changes; you don't need to look at it at all.

## Ad muting

If an ad plays anyway (for example, while Looptify was disarmed), Looptify mutes Spotify
for the length of the ad and unmutes it when music returns.

## Settings

Settings live in `config.toml` in the Looptify folder. Each one is explained in the file
itself; delete a line to use its default. The ones you're most likely to change:

| Setting | Default | Purpose |
|---|---|---|
| `mode` | `"loop"` | Mode at startup: `"loop"`, `"in_order"`, `"shuffle_loop"` or `"pure_random"` |
| `hotkey` | `"ctrl+alt+l"` | Arm/disarm shortcut. Modifiers: `ctrl`, `alt`, `shift`, `win` |
| `mode_hotkey` | `"ctrl+alt+shift+l"` | Change-mode shortcut. Must differ from `hotkey` |
| `start_armed` | `true` | Start armed when Looptify opens |
| `show_notifications` | `true` | Show the on-screen notifications |
| `notification_seconds` | `3.0` | How long a notification stays on screen |
| `lead_seconds` | `1.5` | How many seconds before the end of a song Looptify acts |

Timing and ad-detection settings are covered in
[How Looptify works](docs/how-it-works.md#advanced-settings).

## Troubleshooting

| Problem | Solution |
|---|---|
| The console keeps showing `waiting for Spotify...` | Play a song in Spotify. Spotify only becomes visible to Looptify once something has played. |
| `Could not register hotkey` | Another app already uses that shortcut. Choose a different `hotkey` or `mode_hotkey` in `config.toml`. |
| An ad still played | Increase `lead_seconds` to `2.5`. If Crossfade is on in Spotify's playback settings, set `lead_seconds` higher than the crossfade length. |
| Songs end noticeably early | Decrease `lead_seconds` towards `1.0`. |
| A playlist mode keeps repeating the same song | Open the playlist you're playing from in Spotify, and restore Spotify if it's minimized. If you restarted Spotify yourself, restart Looptify too. |
| Something else | Check the console window: Looptify prints a line explaining each action, then [open an issue](https://github.com/FilbertNg/Looptify/issues). |

## Responsible use

Looptify only does what you could do by hand: it restarts songs and presses Spotify's own
play buttons. It doesn't modify Spotify, block network traffic, or access your account.

Spotify's terms of service discourage circumventing ads, so use your own judgement. If you
want to support the artists you listen to, Spotify Premium is the direct way to do it.

## Learn more

- [How Looptify works](docs/how-it-works.md): the technical details and known limitations
- [Contributing](CONTRIBUTING.md): development setup and guidelines
- [Report a problem](https://github.com/FilbertNg/Looptify/issues)

## License

[MIT](LICENSE) © 2026 Filbert Ng
