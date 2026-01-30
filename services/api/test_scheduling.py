import pytest
from services.api.storage.spaced_repetition import calculate_next_interval
from services.api.models import PuzzleResult

def test_first_pass():
    # New puzzle (interval=None)
    interval, ease = calculate_next_interval(None, 2.0, "pass")
    assert interval == 1
    assert ease == 2.05

def test_pass_after_1_day():
    interval, ease = calculate_next_interval(1, 2.0, "pass")
    assert interval == 3
    assert ease == 2.05

def test_pass_after_3_days():
    # round(3 * 2.0) = 6
    interval, ease = calculate_next_interval(3, 2.0, "pass")
    assert interval == 6
    assert ease == 2.05

def test_fail_reset():
    # Fail resets interval to 1 and reduces ease
    interval, ease = calculate_next_interval(10, 2.5, "fail")
    assert interval == 1
    assert ease == 2.3

def test_ease_bounds():
    # Max ease 2.8
    _, ease = calculate_next_interval(5, 2.78, "pass")
    assert ease == 2.8
    
    # Min ease 1.3
    _, ease = calculate_next_interval(5, 1.4, "fail")
    assert ease == 1.3

def test_round_logic():
    # round(5 * 1.35) = 6.75 -> 7
    interval, _ = calculate_next_interval(5, 1.35, "pass")
    assert interval == 7
