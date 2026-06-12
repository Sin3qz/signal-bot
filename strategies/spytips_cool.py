import os
import json
import pandas as pd
import yahooquery as yq
from .constants import *
import numpy as np
import time


SPY_EUR_TICKER = "IBCF.DE"
TIPS_EUR_TICKER = "IBC5.DE"
GOLD_TICKER = "4GLD.DE"

SPY_USD_TICKER = "^SP500TR"
TIPS_USD_TICKER = "TIP"

STATUS_FILE = "letsgo_status.json"


def _download_history(ticker):
    return yq.Ticker(ticker).history(
        period="max",
        adj_ohlc=True,
        adj_timezone=False
    )


def _prepare_close(df):
    date_level_str = pd.Index([str(x) for x in df.index.get_level_values("date")])
    colon_mask = date_level_str.str.contains(":")

    df.index = pd.to_datetime(
        date_level_str.where(
            ~colon_mask,
            date_level_str.str.split(" ").str[0]
        )
    )

    close = pd.to_numeric(df["close"], errors="coerce")
    close = close.dropna()
    close = close[~close.index.duplicated(keep="last")]
    close = close.sort_index()

    berlin_today = pd.Timestamp.now(tz="Europe/Berlin").date()
    berlin_yesterday = berlin_today - pd.Timedelta(days=1)

    close = close[close.index.date <= berlin_yesterday]

    return close


def _diff_to_sma(close, sma_window):
    sma_rolling = close.rolling(window=sma_window).mean()
    diff = (close - sma_rolling) / sma_rolling

    return sma_rolling, diff


def _last_valid(series):
    clean = series.dropna()

    if clean.empty:
        return np.nan

    return clean.iloc[-1]


def _last_weekday_on_or_before(date_value):
    d = pd.Timestamp(date_value)

    while d.weekday() >= 5:
        d = d - pd.Timedelta(days=1)

    return d.strftime("%Y-%m-%d")


def _expected_fresh_date():
    berlin_today = pd.Timestamp.now(tz="Europe/Berlin").date()
    berlin_yesterday = berlin_today - pd.Timedelta(days=1)

    return _last_weekday_on_or_before(berlin_yesterday)


def _date_de(date_string):
    try:
        return pd.to_datetime(date_string).strftime("%d.%m.%Y")
    except Exception:
        return str(date_string)


def _save_status(status):
    with open(STATUS_FILE, "w") as f:
        json.dump(status, f, indent=2)


def _has_value_changed(current_status, previous_status):
    if not previous_status:
        return False

    fields = [
        "current",
        "sma",
        "diffPct"
    ]

    for field in fields:
        if (
            field in current_status
            and field in previous_status
            and isinstance(current_status[field], (int, float))
            and isinstance(previous_status[field], (int, float))
        ):
            if abs(current_status[field] - previous_status[field]) > 0.000001:
                return True

    return False


def _build_signal_status(
    key,
    name,
    ticker,
    close,
    sma_rolling,
    diff
):
    expected_date = _expected_fresh_date()

    current_date = close.index[-1].strftime("%Y-%m-%d")
    previous_date = close.index[-2].strftime("%Y-%m-%d") if len(close) >= 2 else None

    current_status = {
        "key": key,
        "name": name,
        "ticker": ticker,
        "currentDate": current_date,
        "previousDate": previous_date,
        "expectedDate": expected_date,
        "current": float(close.iloc[-1]),
        "sma": float(sma_rolling.iloc[-1]),
        "diffPct": float(diff.iloc[-1] * 100)
    }

    previous_status = None

    if len(close) >= 2:
        previous_status = {
            "current": float(close.iloc[-2]),
            "sma": float(sma_rolling.iloc[-2]),
            "diffPct": float(diff.iloc[-2] * 100)
        }

    plausible_date = current_date >= expected_date
    value_changed = _has_value_changed(current_status, previous_status)

    current_status["plausibleDate"] = plausible_date
    current_status["valueChanged"] = value_changed
    current_status["fresh"] = plausible_date and value_changed

    return current_status


def _status_icon(status):
    if status and status.get("fresh"):
        return "✅"

    return "❌"


def _status_date(status):
    if not status:
        return "-"

    return _date_de(status.get("currentDate"))


def _parse_history_entry(entry):
    return (
        [entry[0]]
        + [float(x) for x in entry[1:5]]
        + [entry[5] == "True", int(entry[6])]
        + [float(entry[7]), float(entry[8]), entry[9].strip()]
    )


def _position_from_entry(entry):
    current_position = "Market" if entry[5] else "Cash"

    if entry[9] == "GOLD":
        current_position = "Gold"

    return current_position


def _allocation_from_signals(indicator, tips_signal, gold_signal):
    if tips_signal == SELL and gold_signal == BUY:
        return "GOLD"

    if indicator == BUY:
        return "MARKET"

    return "CASH"


def _build_message(
    current_position,
    cooldown,
    spy_diff,
    tips_diff,
    gold_diff,
    usd_info_available,
    spy_usd_diff,
    tips_usd_diff,
    signal_status
):
    spy_status = signal_status["signals"].get("spy_eur")
    tips_status = signal_status["signals"].get("tips_eur")
    gold_status = signal_status["signals"].get("gold")
    spy_usd_status = signal_status["signals"].get("spy_usd")
    tips_usd_status = signal_status["signals"].get("tips_usd")

    text = (
        f"Currently in: {current_position} "
        f"({cooldown} cooldown days remaining)\n\n"
    )

    text += (
        f"SPY EUR-hedged:  {_status_icon(spy_status)} "
        f"{_last_valid(spy_diff):+.2%}\n"
        f"Kursdatum: {_status_date(spy_status)}\n\n"
    )

    text += (
        f"TIPS EUR-hedged: {_status_icon(tips_status)} "
        f"{_last_valid(tips_diff):+.2%}\n"
        f"Kursdatum: {_status_date(tips_status)}\n\n"
    )

    text += (
        f"GOLD:             {_status_icon(gold_status)} "
        f"{_last_valid(gold_diff):+.2%}\n"
        f"Kursdatum: {_status_date(gold_status)}\n"
    )

    if usd_info_available:
        text += "\nUSD-based signals:\n"

        text += (
            f"SPY USD:          {_status_icon(spy_usd_status)} "
            f"{_last_valid(spy_usd_diff):+.2%}\n"
            f"Kursdatum: {_status_date(spy_usd_status)}\n\n"
        )

        text += (
            f"TIPS USD:         {_status_icon(tips_usd_status)} "
            f"{_last_valid(tips_usd_diff):+.2%}\n"
            f"Kursdatum: {_status_date(tips_usd_status)}\n"
        )

    return text


def spy_tips_cool():
    for i in range(TRY_COUNT):
        try:
            spy_eur = _download_history(SPY_EUR_TICKER)
            tips_eur = _download_history(TIPS_EUR_TICKER)
            gold = _download_history(GOLD_TICKER)

            spy_usd = _download_history(SPY_USD_TICKER)
            tips_usd = _download_history(TIPS_USD_TICKER)

        except Exception as e:
            print(f"({i + 1}/{TRY_COUNT}) Failed to download data from Yahoo Finance: {e}")
            time.sleep(2)
            continue

        if spy_eur.empty or tips_eur.empty or gold.empty:
            print(f"({i + 1}/{TRY_COUNT}) Failed to download EUR-hedged signal data.")
            time.sleep(2)
        else:
            break

    else:
        return (
            "Error",
            "Failed to download data from Yahoo Finance after multiple attempts.",
            "Please try again later manually"
        )

    spy_close = _prepare_close(spy_eur)
    tips_close = _prepare_close(tips_eur)
    gold_close = _prepare_close(gold)

    spy_sma_rolling, spy_diff = _diff_to_sma(spy_close, SPY_SMA)
    tips_sma_rolling, tips_diff = _diff_to_sma(tips_close, TIPS_SMA)
    gold_sma_rolling, gold_diff = _diff_to_sma(gold_close, SPY_SMA)

    usd_info_available = False
    spy_usd_diff = None
    tips_usd_diff = None
    spy_usd_close = None
    tips_usd_close = None
    spy_usd_sma_rolling = None
    tips_usd_sma_rolling = None

    try:
        spy_usd_close = _prepare_close(spy_usd)
        tips_usd_close = _prepare_close(tips_usd)

        spy_usd_sma_rolling, spy_usd_diff = _diff_to_sma(
            spy_usd_close,
            SPY_SMA
        )

        tips_usd_sma_rolling, tips_usd_diff = _diff_to_sma(
            tips_usd_close,
            TIPS_SMA
        )

        usd_info_available = True

    except Exception:
        usd_info_available = False
        spy_usd_diff = None
        tips_usd_diff = None

    signal_status = {
        "updated": pd.Timestamp.now(tz="Europe/Berlin").isoformat(),
        "signals": {}
    }

    signal_status["signals"]["spy_eur"] = _build_signal_status(
        "spy_eur",
        "SPY EUR-hedged",
        SPY_EUR_TICKER,
        spy_close,
        spy_sma_rolling,
        spy_diff
    )

    signal_status["signals"]["tips_eur"] = _build_signal_status(
        "tips_eur",
        "TIPS EUR-hedged",
        TIPS_EUR_TICKER,
        tips_close,
        tips_sma_rolling,
        tips_diff
    )

    signal_status["signals"]["gold"] = _build_signal_status(
        "gold",
        "Gold",
        GOLD_TICKER,
        gold_close,
        gold_sma_rolling,
        gold_diff
    )

    if usd_info_available:
        signal_status["signals"]["spy_usd"] = _build_signal_status(
            "spy_usd",
            "SPY USD",
            SPY_USD_TICKER,
            spy_usd_close,
            spy_usd_sma_rolling,
            spy_usd_diff
        )

        signal_status["signals"]["tips_usd"] = _build_signal_status(
            "tips_usd",
            "TIPS USD",
            TIPS_USD_TICKER,
            tips_usd_close,
            tips_usd_sma_rolling,
            tips_usd_diff
        )

    signal_status["needsRetry"] = any(
        not s.get("fresh", False)
        for s in signal_status["signals"].values()
    )

    _save_status(signal_status)

    fileName = (
        HISTORY_FILENAME
        + "_"
        + str(SPY_SMA)
        + "_"
        + str(TIPS_SMA)
        + "_"
        + str(COOLDOWN_DAYS)
        + "_EURHEDGED_GOLD"
        + ".txt"
    )

    last_entry = None

    if not os.path.exists(fileName):
        consecutive_days = 1

        for i in range(2, min(len(spy_diff), len(tips_diff), len(gold_diff))):
            previous_signal = (
                spy_diff.iloc[-i] > 0
                and tips_diff.iloc[-i] > 0
            )

            current_signal = (
                spy_diff.iloc[-i + 1] > 0
                and tips_diff.iloc[-i + 1] > 0
            )

            if previous_signal == current_signal:
                consecutive_days += 1
            else:
                consecutive_days = 1

            if consecutive_days >= COOLDOWN_DAYS:
                break

        else:
            print("Could not find a continuous sequence of cooldown days.")
            return (
                "Error",
                "Could not find a continuous sequence of cooldown days.",
                "This happens if the data is not sufficient or the cooldown days are too high."
            )

        with open(fileName, "w") as f:
            indicator = None
            cooldown = 0

            for j in range(i, 0, -1):
                if (
                    np.isnan(spy_diff.iloc[-j])
                    or np.isnan(tips_diff.iloc[-j])
                    or np.isnan(gold_diff.iloc[-j])
                ):
                    return (
                        "Error",
                        None,
                        "SMA calculation failed, please try again later. Some indicators are NaN."
                    )

                spy_signal = BUY if spy_diff.iloc[-j] > 0 else SELL
                tips_signal = BUY if tips_diff.iloc[-j] > 0 else SELL
                gold_signal = BUY if gold_diff.iloc[-j] > 0 else SELL

                total_indicator = (
                    BUY
                    if spy_signal == BUY and tips_signal == BUY
                    else SELL
                )

                if cooldown > 0:
                    cooldown -= 1

                if total_indicator == BUY and cooldown == 0:
                    if indicator == SELL:
                        cooldown = COOLDOWN_DAYS

                    indicator = BUY

                elif cooldown == 0:
                    if indicator == BUY:
                        cooldown = COOLDOWN_DAYS

                    indicator = SELL

                allocation = _allocation_from_signals(
                    indicator,
                    tips_signal,
                    gold_signal
                )

                f.write(
                    f"{spy_close.index[-j]},"
                    f"{spy_close.iloc[-j]},"
                    f"{tips_close.iloc[-j]},"
                    f"{spy_sma_rolling.iloc[-j]},"
                    f"{tips_sma_rolling.iloc[-j]},"
                    f"{indicator == BUY},"
                    f"{cooldown},"
                    f"{gold_close.iloc[-j]},"
                    f"{gold_sma_rolling.iloc[-j]},"
                    f"{allocation}\n"
                )

    else:
        with open(fileName, "r") as f:
            file_c = f.readlines()

        last_entry = file_c[-1].split(",")

        if last_entry[0] == str(spy_close.index[-1]):
            print("Already checked today")

            last_entry_parsed = _parse_history_entry(last_entry)

            text = _build_message(
                current_position=_position_from_entry(last_entry_parsed),
                cooldown=last_entry_parsed[6],
                spy_diff=spy_diff,
                tips_diff=tips_diff,
                gold_diff=gold_diff,
                usd_info_available=usd_info_available,
                spy_usd_diff=spy_usd_diff,
                tips_usd_diff=tips_usd_diff,
                signal_status=signal_status
            )

            return "Daily Notification", None, text

        last_date = pd.to_datetime(last_entry[0])

        valid_dates_after_last_entry = (
            spy_close.index[spy_close.index > last_date]
        )

        if len(valid_dates_after_last_entry) == 0:
            print("No new valid trading day after last history entry")

            last_entry_parsed = _parse_history_entry(last_entry)

            text = _build_message(
                current_position=_position_from_entry(last_entry_parsed),
                cooldown=last_entry_parsed[6],
                spy_diff=spy_diff,
                tips_diff=tips_diff,
                gold_diff=gold_diff,
                usd_info_available=usd_info_available,
                spy_usd_diff=spy_usd_diff,
                tips_usd_diff=tips_usd_diff,
                signal_status=signal_status
            )

            return "Daily Notification", None, text

        first_new_date = valid_dates_after_last_entry[0]
        last_index = spy_close.index.get_loc(first_new_date)
        last_rev_index = last_index - len(spy_close) - 1

        cooldown = int(last_entry[6])
        indicator = BUY if last_entry[5] == "True" else SELL

        assert last_rev_index < -1

        for j in range(last_rev_index + 1, 0):
            if (
                np.isnan(spy_diff.iloc[j])
                or np.isnan(tips_diff.iloc[j])
                or np.isnan(gold_diff.iloc[j])
            ):
                return (
                    "Error",
                    None,
                    "SMA calculation failed, please try again later. Some indicators are NaN."
                )

            spy_signal = BUY if spy_diff.iloc[j] > 0 else SELL
            tips_signal = BUY if tips_diff.iloc[j] > 0 else SELL
            gold_signal = BUY if gold_diff.iloc[j] > 0 else SELL

            total_indicator = (
                BUY
                if spy_signal == BUY and tips_signal == BUY
                else SELL
            )

            if cooldown > 0:
                cooldown -= 1

            if total_indicator == BUY and cooldown == 0:
                if indicator == SELL:
                    cooldown = COOLDOWN_DAYS

                indicator = BUY

            elif cooldown == 0:
                if indicator == BUY:
                    cooldown = COOLDOWN_DAYS

                indicator = SELL

            allocation = _allocation_from_signals(
                indicator,
                tips_signal,
                gold_signal
            )

            with open(fileName, "a") as f:
                f.write(
                    f"{spy_close.index[j]},"
                    f"{spy_close.iloc[j]},"
                    f"{tips_close.iloc[j]},"
                    f"{spy_sma_rolling.iloc[j]},"
                    f"{tips_sma_rolling.iloc[j]},"
                    f"{indicator == BUY},"
                    f"{cooldown},"
                    f"{gold_close.iloc[j]},"
                    f"{gold_sma_rolling.iloc[j]},"
                    f"{allocation}\n"
                )

    with open(fileName, "r") as f:
        file_c = f.readlines()

    new_entry = file_c[-1].split(",")

    new_entry = _parse_history_entry(new_entry)

    allocation = new_entry[9]

    subject = ""
    subject2 = ""

    if last_entry is not None:
        last_entry_parsed = _parse_history_entry(last_entry)

        previous_allocation = last_entry_parsed[9]

        if allocation != previous_allocation:
            if allocation == "MARKET":
                subject = "REGIME CHANGED: GO LONG NOW"

            elif allocation == "CASH":
                subject = "REGIME CHANGED: GO IN CASH NOW"

            elif allocation == "GOLD":
                subject = "REGIME CHANGED: GO IN GOLD NOW"

    else:
        if allocation == "MARKET":
            subject = "GO LONG NOW"

        elif allocation == "CASH":
            subject = "GO IN CASH NOW"

        elif allocation == "GOLD":
            subject = "GO IN GOLD NOW"

    text = _build_message(
        current_position=_position_from_entry(new_entry),
        cooldown=new_entry[6],
        spy_diff=spy_diff,
        tips_diff=tips_diff,
        gold_diff=gold_diff,
        usd_info_available=usd_info_available,
        spy_usd_diff=spy_usd_diff,
        tips_usd_diff=tips_usd_diff,
        signal_status=signal_status
    )

    if DAILY_NOTIFICATION and subject == "":
        subject = "Daily Notification"

    return subject, subject2, text
