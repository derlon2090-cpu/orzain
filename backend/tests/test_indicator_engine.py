from decimal import Decimal

import pytest

from app.indicator_engine import (
    condition_values,
    exponential_moving_average,
    macd_histogram,
    relative_strength_index,
    required_candles,
    simple_moving_average,
)


def test_sma_and_ema_are_seeded_from_first_complete_window():
    values = list(map(Decimal, ["1", "2", "3", "4"]))
    assert simple_moving_average(values, 3) == [None, None, Decimal("2"), Decimal("3")]
    assert exponential_moving_average(values, 3) == [None, None, Decimal("2"), Decimal("3")]


def test_rsi_uses_wilder_smoothing_and_stable_flat_market_value():
    assert relative_strength_index(list(map(Decimal, ["1", "2", "3", "2"])), 2) == [None, None, Decimal("100"), Decimal("50")]
    assert relative_strength_index(list(map(Decimal, ["5", "5", "5", "5"])), 2)[-1] == Decimal("50")


def test_macd_histogram_has_two_latest_values_and_explicit_history_requirement():
    closes = list(map(Decimal, ["10", "11", "10", "12", "11", "13", "12", "14", "13"]))
    result = macd_histogram(closes, 2, 3, 2)
    assert len(result) == len(closes)
    assert result[-2] is not None and result[-1] is not None
    rule = {"indicator": "MACD", "fast_period": 2, "slow_period": 3, "signal_period": 2}
    assert required_candles(rule) == 5
    bars = [{"close": value} for value in closes]
    assert condition_values(rule, bars) == (result[-2], result[-1])


def test_indicator_rejects_non_finite_market_input():
    with pytest.raises(ValueError, match="invalid_indicator_input"):
        simple_moving_average([Decimal("1"), Decimal("NaN")], 2)
