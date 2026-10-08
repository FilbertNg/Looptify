from looptify.launcher import REQUIRED_FLAGS, has_required_flags, is_main_process

# Captured with psutil on 2026-10-08.
FLAGGED = [
    "Spotify.exe",
    "--force-renderer-accessibility=complete",
    "--enable-features=UiaProvider",
    "--disable-features=CalculateNativeWinOcclusion",
]
STORE_PLAIN = [
    r"C:\Program Files\WindowsApps\SpotifyAB.SpotifyMusic_1.301.234.0_x64__zpdnekdrzrea0\Spotify.exe"
]
RENDERER = [
    r"C:\Program Files\WindowsApps\SpotifyAB.SpotifyMusic_1.301.234.0_x64__zpdnekdrzrea0\Spotify.exe",
    "--type=renderer",
    "--no-pre-read-main-dll",
]
# The first attempt: exposed nothing.
PLAIN_ACCESSIBILITY = ["Spotify.exe", "--force-renderer-accessibility"]


def test_browser_processes_are_main():
    assert is_main_process(FLAGGED)
    assert is_main_process(STORE_PLAIN)


def test_helper_processes_are_not_main():
    assert not is_main_process(RENDERER)


def test_flagged_launch_is_accepted():
    assert has_required_flags(FLAGGED)


def test_plain_launch_needs_the_flags():
    assert not has_required_flags(STORE_PLAIN)


def test_plain_accessibility_flag_is_not_enough():
    assert not has_required_flags(PLAIN_ACCESSIBILITY)


def test_flag_order_does_not_matter():
    assert has_required_flags(["Spotify.exe", *reversed(REQUIRED_FLAGS)])
