"""Deterministic technical indicators over closed candle data using Decimal only."""
from decimal import Decimal


def _decimal(value) -> Decimal:
    result = value if isinstance(value, Decimal) else Decimal(str(value))
    if not result.is_finite():
        raise ValueError("invalid_indicator_input")
    return result


def simple_moving_average(values: list[Decimal], period: int) -> list[Decimal | None]:
    values = [_decimal(value) for value in values]
    if period < 2 or len(values) < period:
        return []
    result: list[Decimal | None] = [None] * (period - 1)
    running = sum(values[:period], Decimal("0"))
    result.append(running / period)
    for index in range(period, len(values)):
        running += values[index] - values[index - period]
        result.append(running / period)
    return result


def exponential_moving_average(values: list[Decimal], period: int) -> list[Decimal | None]:
    values = [_decimal(value) for value in values]
    if period < 2 or len(values) < period:
        return []
    result: list[Decimal | None] = [None] * (period - 1)
    current = sum(values[:period], Decimal("0")) / period
    result.append(current)
    alpha = Decimal("2") / Decimal(period + 1)
    for value in values[period:]:
        current = (value - current) * alpha + current
        result.append(current)
    return result


def relative_strength_index(closes: list[Decimal], period: int) -> list[Decimal | None]:
    closes = [_decimal(value) for value in closes]
    if period < 2 or len(closes) < period + 1:
        return []
    deltas = [closes[index] - closes[index - 1] for index in range(1, len(closes))]
    gains = [max(delta, Decimal("0")) for delta in deltas]
    losses = [max(-delta, Decimal("0")) for delta in deltas]
    average_gain = sum(gains[:period], Decimal("0")) / period
    average_loss = sum(losses[:period], Decimal("0")) / period

    def score(gain: Decimal, loss: Decimal) -> Decimal:
        if gain == 0 and loss == 0:
            return Decimal("50")
        if loss == 0:
            return Decimal("100")
        if gain == 0:
            return Decimal("0")
        relative_strength = gain / loss
        return Decimal("100") - Decimal("100") / (Decimal("1") + relative_strength)

    result: list[Decimal | None] = [None] * period
    result.append(score(average_gain, average_loss))
    for index in range(period, len(deltas)):
        average_gain = (average_gain * (period - 1) + gains[index]) / period
        average_loss = (average_loss * (period - 1) + losses[index]) / period
        result.append(score(average_gain, average_loss))
    return result


def macd_histogram(closes: list[Decimal], fast_period: int, slow_period: int, signal_period: int) -> list[Decimal | None]:
    if fast_period < 2 or slow_period <= fast_period or signal_period < 2:
        return []
    if len(closes) < slow_period + signal_period - 1:
        return []
    fast = exponential_moving_average(closes, fast_period)
    slow = exponential_moving_average(closes, slow_period)
    first_valid = slow_period - 1
    line = [fast[index] - slow[index] for index in range(first_valid, len(closes))]
    signal = exponential_moving_average(line, signal_period)
    result: list[Decimal | None] = [None] * first_valid
    for index, signal_value in enumerate(signal):
        line_value = line[index]
        result.append(None if signal_value is None else line_value - signal_value)
    return result


def required_candles(condition: dict) -> int:
    indicator = condition.get("indicator")
    if indicator == "RSI":
        return int(condition["period"]) + 2
    if indicator in {"SMA", "EMA"}:
        return int(condition["period"]) + 1
    if indicator == "MACD":
        return int(condition["slow_period"]) + int(condition["signal_period"])
    return 2


def condition_series(condition: dict, bars: list[dict]) -> list[Decimal | None]:
    indicator = condition.get("indicator")
    if indicator not in {"RSI", "SMA", "EMA", "MACD"}:
        raise ValueError("unsupported_indicator")
    closes = [_decimal(bar["close"]) for bar in bars]
    if indicator == "RSI":
        result = relative_strength_index(closes, int(condition["period"]))
    elif indicator == "SMA":
        result = simple_moving_average(closes, int(condition["period"]))
    elif indicator == "EMA":
        result = exponential_moving_average(closes, int(condition["period"]))
    else:
        result = macd_histogram(closes, int(condition["fast_period"]), int(condition["slow_period"]), int(condition["signal_period"]))
    if len(result) != len(bars) or len(result) < 2 or result[-2] is None or result[-1] is None:
        raise ValueError("insufficient_closed_candles_for_indicator")
    return result


def condition_values(condition: dict, bars: list[dict]) -> tuple[Decimal, Decimal]:
    values = condition_series(condition, bars)
    return values[-2], values[-1]
