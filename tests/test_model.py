from dataclasses import replace
import numpy as np
import pytest
from smartcut.model import Settings, Segment, make_plan, validate_plan, move_boundary, statistics
from smartcut.motion import motion_score


def test_padding_merges_and_clamps():
    settings = Settings(min_idle=.5, padding=.4)
    samples = [(0, 0, False), (1, 1, True), (2, 0, False),
               (2.5, 1, True), (3, 0, False), (4, 1, True)]
    plan = make_plan(samples, 4, settings)
    validate_plan(plan, 4)
    assert all(a.end == b.start for a, b in zip(plan, plan[1:]))
    assert plan[0].start == 0 and plan[-1].end == 4
    assert len(plan) == 3  # first two padded activity intervals overlap


def test_no_motion_and_continuous_motion():
    assert make_plan([(0, 0, False), (9.9, 0, False)], 10, Settings()) == [Segment(0, 10, "remove")]
    plan = make_plan([(0, 0, False), (1, 1, True), (9.9, 1, True)], 10, Settings())
    assert plan == [Segment(0, 10)]
    assert make_plan([(0, 0, False)], .1, Settings()) == [Segment(0, .1)]


def test_sensitivity_and_noise():
    original = np.full((180, 320), 100, np.uint8)
    cursor = original.copy()
    cursor[50:54, 50:54] = 110
    assert motion_score(original, cursor, 100)[1]
    assert not motion_score(original, cursor, 0)[1]
    rng = np.random.default_rng(42)
    noisy = (original.astype(int)+rng.integers(-2, 3, original.shape)).astype(np.uint8)
    assert not motion_score(original, noisy, 100)[1]
    assert not motion_score(original, original+15, 100)[1]


def test_manual_edit_and_duration():
    plan = [Segment(0, 2), Segment(2, 8, "speed", 5), Segment(8, 10)]
    assert statistics(plan)["output"] == pytest.approx(5.2)
    changed = move_boundary(plan, 1, 3, 7, 10)
    assert changed[0].end == 3 and changed[-1].start == 7
    assert statistics(changed)["output"] == pytest.approx(6.8)
    with pytest.raises(ValueError):
        move_boundary(plan, 1, 0, 7, 10)
    with pytest.raises(ValueError):
        validate_plan([Segment(0, 4), Segment(3, 10)], 10)
    with pytest.raises(ValueError):
        Settings(area=(.5, 0, 1, 1)).validate()
