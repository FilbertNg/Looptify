import random

from looptify.planner import (
    RESCUE_SECONDS,
    Cancel,
    Locate,
    Plan,
    Prepare,
    clear_press,
    fallback_reason,
    mark_pressed,
    prepare_skip,
    rechoose,
    should_rescue,
    skip_refusal,
    skip_verdict,
    skip_wait_reason,
    step,
)
from looptify.playlist import Mode, PageInfo

PAGE = PageInfo("Liked Songs", 10)
SONG_A = ("UNETHICAL", "Faouzia")
SONG_B = ("Bunga Maaf", "The Lantis")


class FixedRng(random.Random):
    """Always draws `row`."""

    def __init__(self, row: int) -> None:
        super().__init__(0)
        self.row = row

    def randint(self, a: int, b: int) -> int:
        return self.row

    def choice(self, seq):
        return self.row if self.row in seq else seq[0]


def run(plan=None, *, mode=Mode.IN_ORDER, track=SONG_A, page=PAGE, located=None, rng=None):
    return step(
        plan or Plan(),
        mode=mode,
        track=track,
        page=page,
        located=located,
        rng=rng or random.Random(0),
    )


def located_plan(row, *, mode=Mode.IN_ORDER, rng=None):
    """A plan for SONG_A after the worker answered that it sits at `row`."""
    plan, _ = run(mode=mode)
    return run(plan, mode=mode, located=(plan.locate_seq, row), rng=rng)


def test_a_new_track_is_located_first():
    plan, actions = run()
    assert actions == [Locate(1, "UNETHICAL", "Faouzia")]
    assert plan.locating and plan.target is None


def test_located_row_becomes_current_and_the_next_is_prepared():
    plan, actions = located_plan(4)
    assert plan.current_row == 4 and not plan.locating
    assert plan.target == 5
    assert actions == [Prepare(5)]


def test_an_answer_to_an_older_locate_is_ignored():
    plan, _ = run()
    plan2, actions = run(plan, located=(plan.locate_seq - 1, 4))
    assert plan2.locating and actions == []


def test_a_song_missing_from_the_list_starts_in_order_at_row_one():
    plan, actions = located_plan(None)
    assert plan.current_row is None
    assert actions == [Prepare(1)]


def test_a_quiet_poll_changes_nothing():
    plan, _ = located_plan(4)
    plan2, actions = run(plan, located=(plan.locate_seq, 4))
    assert actions == [] and plan2 == plan


def test_the_pressed_row_becomes_current_without_locating():
    plan, _ = located_plan(4)
    plan, actions = run(mark_pressed(plan), track=SONG_B)
    assert plan.current_row == 5 and plan.pressed_row is None
    assert not plan.locating
    assert actions == [Prepare(6)]


def test_a_song_the_user_started_is_located():
    plan, _ = located_plan(4)
    plan2, actions = run(plan, track=SONG_B)
    assert actions == [Locate(plan.locate_seq + 1, "Bunga Maaf", "The Lantis")]
    assert plan2.target is None


def test_loop_mode_plans_nothing():
    plan, actions = run(mode=Mode.LOOP)
    assert actions == [] and plan.target is None and not plan.locating


def test_switching_to_loop_cancels_preparation():
    plan, _ = located_plan(4)
    plan2, actions = run(plan, mode=Mode.LOOP)
    assert actions == [Cancel()] and plan2.target is None


def test_switching_out_of_loop_locates_again():
    plan, _ = run(mode=Mode.LOOP)
    _, actions = run(plan, mode=Mode.IN_ORDER)
    assert actions == [Locate(1, "UNETHICAL", "Faouzia")]


def test_changing_mode_rechooses_without_locating():
    plan, _ = located_plan(4)
    plan2, actions = run(plan, mode=Mode.PURE_RANDOM, rng=FixedRng(9))
    assert actions == [Prepare(9)]
    assert plan2.current_row == 4 and plan2.target == 9


def test_no_tracklist_cancels_preparation():
    plan, _ = located_plan(4)
    plan2, actions = run(plan, page=None)
    assert actions == [Cancel()] and plan2.target is None


def test_an_ad_cancels_preparation():
    plan, _ = located_plan(4)
    _, actions = run(plan, track=None)
    assert actions == [Cancel()]


def test_opening_another_playlist_locates_again():
    plan, _ = located_plan(4)
    _, actions = run(plan, page=PageInfo("Chill", 30))
    assert actions == [Locate(plan.locate_seq + 1, "UNETHICAL", "Faouzia")]


def test_drawing_the_current_row_means_loop():
    plan, actions = located_plan(4, mode=Mode.PURE_RANDOM, rng=FixedRng(4))
    assert plan.target is None and actions == [Cancel()]


def test_rechoose_draws_again_after_a_loop():
    plan, _ = located_plan(4, mode=Mode.PURE_RANDOM, rng=FixedRng(4))
    plan2, actions = rechoose(plan, FixedRng(7))
    assert plan2.target == 7 and actions == [Prepare(7)]


def test_rechoose_does_nothing_while_locating_or_looping():
    locating, _ = run()
    assert rechoose(locating, random.Random(0)) == (locating, [])
    looping, _ = run(mode=Mode.LOOP)
    assert rechoose(looping, random.Random(0)) == (looping, [])


def test_a_single_song_list_loops_in_every_mode():
    one = PageInfo("One", 1)
    for mode in (Mode.IN_ORDER, Mode.SHUFFLE_LOOP, Mode.PURE_RANDOM):
        plan, _ = run(mode=mode, page=one)
        plan, _ = run(plan, mode=mode, page=one, located=(plan.locate_seq, 1))
        assert plan.target is None, mode


def test_shuffle_through_the_planner_plays_every_row_once():
    rng = random.Random(3)

    def track(row):
        return (f"song {row}", "artist")

    plan, _ = step(
        Plan(), mode=Mode.SHUFFLE_LOOP, track=track(1), page=PAGE, located=None, rng=rng
    )
    plan, _ = step(
        plan,
        mode=Mode.SHUFFLE_LOOP,
        track=track(1),
        page=PAGE,
        located=(plan.locate_seq, 1),
        rng=rng,
    )
    order = [1]
    for _ in range(9):
        plan = mark_pressed(plan)
        row = plan.pressed_row
        order.append(row)
        plan, _ = step(
            plan, mode=Mode.SHUFFLE_LOOP, track=track(row), page=PAGE, located=None, rng=rng
        )
    assert sorted(order) == list(range(1, 11))


def test_rescue_when_a_press_never_started_a_new_song():
    plan, _ = located_plan(4)
    pressed = mark_pressed(plan)
    assert should_rescue(pressed, SONG_A, RESCUE_SECONDS)
    assert not should_rescue(pressed, SONG_A, 1.0)
    assert not should_rescue(plan, SONG_A, 0.1)


def test_clear_press_lines_the_same_row_up_again():
    plan, _ = located_plan(4)
    cleared, actions = clear_press(mark_pressed(plan))
    assert cleared.pressed_row is None and actions == [Prepare(5)]
    assert not should_rescue(cleared, SONG_A, 0.1)


def test_fallback_reasons():
    assert fallback_reason(run(mode=Mode.LOOP)[0], None) is None
    assert "no tracklist" in fallback_reason(run(page=None)[0], None)
    assert "finding" in fallback_reason(run()[0], None)
    plan, _ = located_plan(4)
    assert "row 5" in fallback_reason(plan, None)
    assert fallback_reason(plan, 5) is None
    looping, _ = located_plan(4, mode=Mode.PURE_RANDOM, rng=FixedRng(4))
    assert fallback_reason(looping, None) is None


def test_skip_refusals():
    plan, _ = located_plan(4)
    assert skip_refusal(True, Mode.IN_ORDER, plan) is None
    assert "deactivated" in skip_refusal(False, Mode.IN_ORDER, plan)
    assert "Shuffle Loop" in skip_refusal(True, Mode.LOOP, run(mode=Mode.LOOP)[0])
    crashed, _ = run(plan, mode=Mode.LOOP)
    assert "unavailable" in skip_refusal(True, Mode.IN_ORDER, crashed)
    assert "Nothing" in skip_refusal(True, Mode.IN_ORDER, run(track=None)[0])
    assert "playlist" in skip_refusal(True, Mode.IN_ORDER, run(page=None)[0])
    one, _ = run(page=PageInfo("One", 1))
    assert "only one" in skip_refusal(True, Mode.IN_ORDER, one)


def test_a_skip_presses_once_the_next_row_is_ready():
    plan, _ = located_plan(4)
    assert skip_verdict(plan, SONG_A, None) == "wait"
    assert skip_verdict(plan, SONG_A, 3) == "wait"
    assert skip_verdict(plan, SONG_A, 5) == "press"


def test_a_skip_waits_while_the_playing_song_is_located():
    plan, _ = run()
    assert skip_verdict(plan, SONG_A, None) == "wait"
    assert "found" in skip_wait_reason(plan)
    plan, _ = located_plan(4)
    assert "row 5" in skip_wait_reason(plan)


def test_a_skip_is_dropped_once_the_song_moves_on():
    plan, _ = located_plan(4)
    assert skip_verdict(plan, SONG_B, 5) == "drop"
    assert skip_verdict(mark_pressed(plan), SONG_A, 5) == "drop"
    looping, _ = run(plan, mode=Mode.LOOP)
    assert skip_verdict(looping, SONG_A, None) == "drop"
    no_page, _ = run(plan, page=None)
    assert skip_verdict(no_page, SONG_A, None) == "drop"


def test_a_skip_in_pure_random_never_lands_on_the_current_song():
    plan, _ = located_plan(4, mode=Mode.PURE_RANDOM, rng=FixedRng(4))
    assert plan.target is None
    skipped, actions = prepare_skip(plan, FixedRng(4))
    assert skipped.target not in (None, 4) and actions == [Prepare(skipped.target)]


def test_prepare_skip_leaves_a_lined_up_row_alone():
    plan, _ = located_plan(4)
    assert prepare_skip(plan, random.Random(0)) == (plan, [])
    locating, _ = run()
    assert prepare_skip(locating, random.Random(0)) == (locating, [])
