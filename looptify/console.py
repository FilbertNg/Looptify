"""Console output setup.

The Windows console defaults to a legacy code page (cp1252 here), so printing a
track title containing anything outside Latin-1 — Japanese, Korean, Cyrillic,
emoji, and plenty of ordinary punctuation — raises UnicodeEncodeError and kills
the process. Spotify track names hit this constantly, so every entry point
reconfigures stdout before printing anything.
"""

from __future__ import annotations

import sys


def enable_unicode_output() -> None:
    """Switch stdout/stderr to UTF-8, replacing anything unrepresentable.

    `errors="replace"` matters as much as the encoding: a terminal font that
    cannot render a glyph should show a placeholder, never crash the looper
    mid-track.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                # A redirected or already-closed stream; printing still works.
                pass
