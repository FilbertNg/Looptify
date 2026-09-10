import pytest

from looptify.hotkey import MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, parse_hotkey


def test_parses_the_default_hotkey():
    mods, vk = parse_hotkey("ctrl+alt+l")
    assert mods == MOD_CONTROL | MOD_ALT
    assert vk == ord("L")


def test_is_case_and_space_insensitive():
    assert parse_hotkey("  CTRL + ALT + L ") == parse_hotkey("ctrl+alt+l")


def test_supports_all_modifiers():
    mods, vk = parse_hotkey("ctrl+alt+shift+win+p")
    assert mods == MOD_CONTROL | MOD_ALT | MOD_SHIFT | MOD_WIN
    assert vk == ord("P")


def test_accepts_control_as_an_alias_for_ctrl():
    assert parse_hotkey("control+l") == parse_hotkey("ctrl+l")


def test_digits_are_supported():
    _, vk = parse_hotkey("ctrl+alt+7")
    assert vk == ord("7")


def test_function_keys_are_supported():
    _, vk = parse_hotkey("ctrl+f9")
    assert vk == 0x70 + 8  # VK_F1 is 0x70, so F9 is 0x78


def test_rejects_a_missing_key():
    with pytest.raises(ValueError, match="no key"):
        parse_hotkey("ctrl+alt")


def test_rejects_an_unknown_token():
    with pytest.raises(ValueError, match="hyper"):
        parse_hotkey("hyper+l")


def test_rejects_a_multi_character_key():
    with pytest.raises(ValueError, match="banana"):
        parse_hotkey("ctrl+banana")
