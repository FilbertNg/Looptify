from looptify.focus import restack_anchor, should_hide, should_reclaim

SPOTIFY = {100, 101}


def reclaim(**kw):
    base = dict(
        original=1,
        original_pid=50,
        current=2,
        current_pid=100,
        spotify_pids=SPOTIFY,
        original_alive=True,
    )
    base.update(kw)
    return should_reclaim(**base)


def test_reclaims_when_spotify_takes_focus_from_the_user():
    assert reclaim()


def test_leaves_focus_alone_when_the_user_switches_apps():
    assert not reclaim(current_pid=60)


def test_leaves_focus_alone_when_the_user_was_in_spotify():
    assert not reclaim(original_pid=101)


def test_leaves_focus_alone_when_the_original_window_closed():
    assert not reclaim(original_alive=False)


def test_nothing_to_do_while_focus_is_unchanged():
    assert not reclaim(current=1, current_pid=50)


def test_hides_spotify_behind_the_users_window():
    assert should_hide(spotify=9, foreground=1, minimized=False)


def test_does_not_hide_a_minimized_spotify():
    # Nothing would show anyway, and the steal goes to a hidden window.
    assert not should_hide(spotify=9, foreground=1, minimized=True)


def test_does_not_hide_spotify_while_the_user_is_in_it():
    # Hiding the window being looked at would cause the flicker it prevents.
    assert not should_hide(spotify=9, foreground=9, minimized=False)


def test_does_not_hide_without_a_spotify_window():
    assert not should_hide(spotify=0, foreground=1, minimized=False)


def test_restack_anchor_is_the_nearest_window_above():
    assert restack_anchor([(5, False), (6, False)]) == 5


def test_restack_anchor_skips_always_on_top_windows():
    # Slotting under a topmost window would make Spotify topmost too.
    assert restack_anchor([(5, True), (6, False)]) == 6


def test_restack_anchor_none_when_nothing_ordinary_is_above():
    assert restack_anchor([(5, True)]) is None
    assert restack_anchor([]) is None
