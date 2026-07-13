from nautilus_trader.model.data import Bar


def maxmin_price(bar: Bar, use_wicks: bool = True) -> tuple[float, float]:
    if use_wicks:
        return bar.high, bar.low
    else:
        if bar.close > bar.open:
            return bar.close, bar.open
        else:
            return bar.open, bar.close
