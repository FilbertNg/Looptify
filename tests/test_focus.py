from looptify.focus import should_reclaim

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
