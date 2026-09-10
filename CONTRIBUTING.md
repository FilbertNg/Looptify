# Contributing to Looptify

Thanks for taking a look. Bug reports and pull requests are both welcome.

## Setup

```bash
git clone https://github.com/FilbertNg/Looptify.git
cd Looptify
pip install -r requirements-dev.txt
python -m pytest tests/
```

You need Windows and Python 3.11+. You do **not** need Spotify running to work on the
logic or to run the test suite.

## The one architectural rule

`looptify/logic.py` must never import `winsdk`, `pycaw`, `pywin32`, or `psutil`.

All the decision-making lives there as pure functions, and all the Windows API calls
live in thin adapters (`smtc.py`, `audio.py`, `hotkey.py`, `console.py`). That split is
the only reason the test suite runs in CI on a machine with no Spotify installed, and
the only reason the timing rules can be tested against synthetic values instead of by
waiting three minutes for a real song to end.

If you're adding behaviour, ask which side of that line it belongs on. Anything that
decides *what to do* is logic and gets tests. Anything that *does it* is an adapter.

## Testing

```bash
python -m pytest tests/ -v
```

Adapters aren't unit-tested — mocking `winsdk` would only test the mock. They have
manual verification scripts instead:

```bash
python scripts/verify_smtc.py     # read-only; watch position extrapolation work
python scripts/verify_hotkey.py   # press the hotkey, confirm it fires
```

If you change timing behaviour, please add a case to `tests/test_logic.py` alongside
the existing ones. The measured constants in those tests (the 4.489 s staleness, the
20 ms extrapolation accuracy) came from real playback — if you have data that
contradicts them, that's worth an issue on its own.

## Style

- Conventional commits: `feat:`, `fix:`, `docs:`, `test:`, `chore:`
- Type hints on public functions
- Module-level docstrings explaining *why*, not just what

## Ideas that would be welcome

- A system-tray version, so it doesn't need a console window
- Support for other SMTC-publishing players
- A Linux port via MPRIS — the pure logic would carry over unchanged
