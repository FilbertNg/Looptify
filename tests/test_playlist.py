import random

from looptify.playlist import (
    MODE_BLURBS,
    MODE_LABELS,
    Mode,
    PageInfo,
    RowText,
    ShuffleBag,
    choose_next,
    find_row,
    next_mode,
    step_toward,
    update_bag,
)


def test_mode_cycle_visits_every_mode_then_returns_to_loop():
    seen = [Mode.LOOP]
    for _ in range(4):
        seen.append(next_mode(seen[-1]))
    assert seen == [
        Mode.LOOP,
        Mode.IN_ORDER,
        Mode.SHUFFLE_LOOP,
        Mode.PURE_RANDOM,
        Mode.LOOP,
    ]


def test_mode_values_match_config_spelling():
    assert [m.value for m in Mode] == [
        "loop",
        "in_order",
        "shuffle_loop",
        "pure_random",
    ]


def test_every_mode_has_a_label_and_a_blurb():
    assert set(MODE_LABELS) == set(Mode) == set(MODE_BLURBS)


def test_loop_never_picks_a_row():
    assert choose_next(Mode.LOOP, 10, 4, ShuffleBag(), random.Random(0)) is None


def test_empty_list_picks_nothing_in_any_mode():
    for mode in Mode:
        assert choose_next(mode, 0, None, ShuffleBag(), random.Random(0)) is None


def test_in_order_moves_to_the_next_row():
    assert choose_next(Mode.IN_ORDER, 10, 4, ShuffleBag(), random.Random(0)) == 5


def test_in_order_wraps_from_the_last_row_to_the_first():
    assert choose_next(Mode.IN_ORDER, 10, 10, ShuffleBag(), random.Random(0)) == 1


def test_in_order_starts_at_row_one_when_current_is_unknown():
    assert choose_next(Mode.IN_ORDER, 10, None, ShuffleBag(), random.Random(0)) == 1


def test_pure_random_covers_the_whole_range_including_current():
    rng = random.Random(1)
    picks = {
        choose_next(Mode.PURE_RANDOM, 10, 3, ShuffleBag(), rng) for _ in range(500)
    }
    assert picks == set(range(1, 11))


def _shuffle_order(count: int, plays: int, seed: int) -> list[int]:
    """Drive the bag the way the planner does: record current, then choose."""
    page = PageInfo("P", count)
    rng = random.Random(seed)
    bag, current, order = ShuffleBag(), None, []
    for _ in range(plays):
        bag = update_bag(bag, page, current)
        current = choose_next(Mode.SHUFFLE_LOOP, count, current, bag, rng)
        order.append(current)
    return order


def test_shuffle_first_cycle_plays_every_row_once():
    assert sorted(_shuffle_order(10, 10, seed=7)) == list(range(1, 11))


def test_shuffle_never_repeats_a_row_before_every_row_has_played():
    order = _shuffle_order(10, 60, seed=11)
    played: set[int] = set()
    previous = None
    for row in order:
        assert row != previous, "the same song twice in a row"
        if row in played:
            assert len(played) == 10, f"row {row} repeated after {len(played)} rows"
            # The song that ended the cycle counts as the new cycle's first.
            played = {previous}
        played.add(row)
        previous = row


def test_shuffle_with_one_row_loops_it():
    bag = update_bag(ShuffleBag(), PageInfo("P", 1), 1)
    assert choose_next(Mode.SHUFFLE_LOOP, 1, 1, bag, random.Random(0)) == 1


def test_bag_resets_for_a_different_playlist():
    bag = ShuffleBag("Liked Songs", frozenset({1, 2}))
    got = update_bag(bag, PageInfo("Chill", 10), 3)
    assert got == ShuffleBag("Chill", frozenset({3}))


def test_bag_drops_rows_past_a_shrunk_list():
    bag = ShuffleBag("P", frozenset({2, 9}))
    assert update_bag(bag, PageInfo("P", 5), 1).played == frozenset({1, 2})


def test_full_bag_restarts_with_the_current_row():
    bag = ShuffleBag("P", frozenset(range(1, 10)))
    assert update_bag(bag, PageInfo("P", 10), 10).played == frozenset({10})


def test_full_bag_with_unknown_current_empties():
    bag = ShuffleBag("P", frozenset(range(1, 11)))
    assert update_bag(bag, PageInfo("P", 10), None).played == frozenset()


ROWS = {
    1: RowText("UNETHICAL Faouzia", ("Faouzia",)),
    2: RowText("Dinero (Bass Boost TikTok) E Trinidad Cardona", ("Trinidad Cardona",)),
    3: RowText(
        "On My Way Alan Walker, Sabrina Carpenter, Farruko",
        ("Alan Walker", "Sabrina Carpenter", "Farruko"),
    ),
    4: RowText("On My Way Home Enya", ("Enya",)),
}


def test_find_row_matches_title_and_artist():
    assert find_row(ROWS, "UNETHICAL", "Faouzia") == 1


def test_find_row_ignores_an_explicit_badge_after_the_title():
    assert find_row(ROWS, "Dinero (Bass Boost TikTok)", "Trinidad Cardona") == 2


def test_find_row_matches_smtc_first_artist_only():
    assert find_row(ROWS, "On My Way", "Alan Walker") == 3


def test_find_row_needs_the_artist_when_a_title_prefixes_another():
    # Row 4 starts with "On My Way Home", but it's by Enya, not Alan Walker.
    assert find_row(ROWS, "On My Way Home", "Alan Walker") is None
    assert find_row(ROWS, "On My Way Home", "Enya") == 4


def test_find_row_returns_the_lowest_duplicate():
    rows = {7: RowText("Lonely Akon", ("Akon",)), 3: RowText("Lonely Akon", ("Akon",))}
    assert find_row(rows, "Lonely", "Akon") == 3


def test_find_row_misses_unknown_songs_and_empty_titles():
    assert find_row(ROWS, "Bunga Maaf", "The Lantis") is None
    assert find_row(ROWS, "", "Faouzia") is None


def test_step_toward_scrolls_down_from_the_last_rendered_row():
    assert step_toward({10, 11, 12}, 500) == 12


def test_step_toward_scrolls_up_from_the_first_rendered_row():
    assert step_toward({600, 601}, 3) == 600


def test_step_toward_stops_when_target_is_rendered_or_nothing_is():
    assert step_toward({4, 5, 6}, 5) is None
    assert step_toward(set(), 5) is None
