import os
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


def _build_message(
    current_position,
    cooldown,
    spy_diff,
    tips_diff,
    gold_diff,
    usd_info_available,
    spy_usd_diff,
    tips_usd_diff
):
    text = (
        f"Currently in: {current_position} "
        f"({cooldown} cooldown days remaining)\n\n"
    )

    text += f"SPY EUR-hedged:  {_last_valid(spy_diff):+.2%}\n"
    text += f"TIPS EUR-hedged: {_last_valid(tips_diff):+.2%}\n"
    text += f"GOLD:             {_last_valid(gold_diff):+.2%}\n"

    if usd_info_available:
        text += "\nUSD-based signals:\n"
        text += f"SPY USD:          {_last_valid(spy_usd_diff):+.2%}\n"
        text += f"TIPS USD:         {_last_valid(tips_usd_diff):+.2%}\n"

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

    try:
        spy_usd_close = _prepare_close(spy_usd)
        tips_usd_close = _prepare_close(tips_usd)

        _, spy_usd_diff = _diff_to_sma(spy_usd_close, SPY_SMA)
        _, tips_usd_diff = _diff_to_sma(tips_usd_close, TIPS_SMA)

        usd_info_available = True

    except Exception:
        usd_info_available = False
        spy_usd_diff = None
        tips_usd_diff = None

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

                allocation = (
                    "GOLD"
                    if tips_signal == SELL and gold_signal == BUY
                    else (
                        "MARKET"
                        if indicator == BUY
                        else "CASH"
                    )
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

            last_entry_parsed = (
                [last_entry[0]]
                + [float(x) for x in last_entry[1:5]]
                + [last_entry[5] == "True", int(last_entry[6])]
                + [float(last_entry[7]), float(last_entry[8]), last_entry[9].strip()]
            )

            current_position = "Market" if last_entry_parsed[5] else "Cash"

            if last_entry_parsed[9] == "GOLD":
                current_position = "Gold"

            text = _build_message(
                current_position=current_position,
                cooldown=last_entry_parsed[6],
                spy_diff=spy_diff,
                tips_diff=tips_diff,
                gold_diff=gold_diff,
                usd_info_available=usd_info_available,
                spy_usd_diff=spy_usd_diff,
                tips_usd_diff=tips_usd_diff
            )

            return "Daily Notification", None, text

        last_date = pd.to_datetime(last_entry[0])
        last_index = spy_close.index.get_loc(last_date)
        last_rev_index = last_index - len(spy_close)

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

            allocation = (
                "GOLD"
                if tips_signal == SELL and gold_signal == BUY
                else (
                    "MARKET"
                    if indicator == BUY
                    else "CASH"
                )
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

    new_entry = (
        [new_entry[0]]
        + [float(x) for x in new_entry[1:5]]
        + [new_entry[5] == "True", int(new_entry[6])]
        + [float(new_entry[7]), float(new_entry[8]), new_entry[9].strip()]
    )

    allocation = new_entry[9]
    current_position = "Market" if new_entry[5] else "Cash"

    if allocation == "GOLD":
        current_position = "Gold"

    subject = ""
    subject2 = ""

    if last_entry is not None:
        last_entry = (
            [last_entry[0]]
            + [float(x) for x in last_entry[1:5]]
            + [last_entry[5] == "True", int(last_entry[6])]
            + [float(last_entry[7]), float(last_entry[8]), last_entry[9].strip()]
        )

        previous_allocation = last_entry[9]

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
        current_position=current_position,
        cooldown=new_entry[6],
        spy_diff=spy_diff,
        tips_diff=tips_diff,
        gold_diff=gold_diff,
        usd_info_available=usd_info_available,
        spy_usd_diff=spy_usd_diff,
        tips_usd_diff=tips_usd_diff
    )

    if DAILY_NOTIFICATION and subject == "":
        subject = "Daily Notification"

    return subject, subject2, text
