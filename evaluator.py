import os
from datetime import datetime, timezone, timedelta

import requests
import psycopg
from psycopg.rows import dict_row


DATABASE_URL = os.environ["DATABASE_URL"]
ALPACA_API_KEY = os.environ["ALPACA_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_SECRET_KEY"]

ALPACA_DATA_URL = "https://data.alpaca.markets"


def get_conn():
    return psycopg.connect(
        DATABASE_URL,
        row_factory=dict_row
    )


def alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY
    }


def init_db():
    with get_conn() as conn:

        # 1 HOUR
        conn.execute("""
            ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS hour1_price DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS hour1_return DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS hour1_checked_at TIMESTAMPTZ
        """)

        # END OF DAY
        conn.execute("""
            ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS eod_price DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS eod_return DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS eod_checked_at TIMESTAMPTZ
        """)

        # DAY 2
        conn.execute("""
            ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS day2_price DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS day2_return DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS day2_checked_at TIMESTAMPTZ
        """)


def calculate_return(signal_price, future_price, direction):
    if signal_price is None or signal_price <= 0:
        return None

    if future_price is None:
        return None

    result = (
        (future_price - signal_price)
        / signal_price
    ) * 100

    if str(direction).upper() == "SHORT":
        result *= -1

    return round(result, 4)


def get_bars(ticker, timeframe, start_time, end_time, limit):
    url = f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/bars"

    params = {
        "timeframe": timeframe,
        "start": start_time.isoformat(),
        "end": end_time.isoformat(),
        "adjustment": "raw",
        "feed": "iex",
        "limit": limit
    }

    response = requests.get(
        url,
        headers=alpaca_headers(),
        params=params,
        timeout=30
    )

    # Some old signals are not US stocks supported by
    # Alpaca's stock-bars endpoint.
    if response.status_code == 400:
        print(f"SKIP | {ticker} | Alpaca does not accept this symbol")
        return []

    response.raise_for_status()

    data = response.json()

    # Alpaca can return "bars": null.
    # Always return a list to the evaluator.
    return data.get("bars") or []


def get_minute_bars(ticker, start_time, end_time):
    return get_bars(
        ticker,
        "1Min",
        start_time,
        end_time,
        10000
    )


def get_daily_bars(ticker, start_time, end_time):
    return get_bars(
        ticker,
        "1Day",
        start_time,
        end_time,
        100
    )


def parse_bar_time(value):
    return datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )


def evaluate_hour1(row, now):
    if row.get("hour1_price") is not None:
        return

    signal_time = row["received_at"]
    target = signal_time + timedelta(hours=1)

    if now < target:
        return

    bars = get_minute_bars(
        row["ticker"],
        target - timedelta(minutes=5),
        target + timedelta(minutes=30)
    )

    if not bars:
        return

    chosen = None

    for bar in bars:
        bar_time = parse_bar_time(bar["t"])

        if bar_time >= target:
            chosen = bar
            break

    if chosen is None:
        return

    price = float(chosen["c"])

    result = calculate_return(
        row["price"],
        price,
        row["direction"]
    )

    with get_conn() as conn:
        conn.execute("""
            UPDATE signals
            SET hour1_price = %s,
                hour1_return = %s,
                hour1_checked_at = NOW()
            WHERE id = %s
        """, (
            price,
            result,
            row["id"]
        ))


def evaluate_eod(row, now):
    if row.get("eod_price") is not None:
        return

    signal_time = row["received_at"]

    bars = get_daily_bars(
        row["ticker"],
        signal_time - timedelta(days=1),
        now + timedelta(days=1)
    )

    if not bars:
        return

    signal_date = signal_time.date()
    same_day = None

    for bar in bars:
        bar_time = parse_bar_time(bar["t"])

        if bar_time.date() == signal_date:
            same_day = bar
            break

    if same_day is None:
        return

    # Do not save today's daily close before the day is complete.
    if now.date() <= signal_date:
        return

    price = float(same_day["c"])

    result = calculate_return(
        row["price"],
        price,
        row["direction"]
    )

    with get_conn() as conn:
        conn.execute("""
            UPDATE signals
            SET eod_price = %s,
                eod_return = %s,
                eod_checked_at = NOW()
            WHERE id = %s
        """, (
            price,
            result,
            row["id"]
        ))


def evaluate_day2(row, now):
    if row.get("day2_price") is not None:
        return

    signal_time = row["received_at"]

    bars = get_daily_bars(
        row["ticker"],
        signal_time - timedelta(days=1),
        now + timedelta(days=1)
    )

    if not bars:
        return

    signal_date = signal_time.date()
    future_bars = []

    for bar in bars:
        bar_time = parse_bar_time(bar["t"])

        if bar_time.date() > signal_date:
            future_bars.append(bar)

    # Need two trading days after the signal day.
    if len(future_bars) < 2:
        return

    price = float(future_bars[1]["c"])

    result = calculate_return(
        row["price"],
        price,
        row["direction"]
    )

    with get_conn() as conn:
        conn.execute("""
            UPDATE signals
            SET day2_price = %s,
                day2_return = %s,
                day2_checked_at = NOW()
            WHERE id = %s
        """, (
            price,
            result,
            row["id"]
        ))


def main():
    init_db()

    now = datetime.now(timezone.utc)

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT *
            FROM signals
            WHERE price IS NOT NULL
            ORDER BY id ASC
            LIMIT 500
        """).fetchall()

    checked = 0
    errors = 0

    for row in rows:
        try:
            evaluate_hour1(row, now)
            evaluate_eod(row, now)
            evaluate_day2(row, now)

            checked += 1

        except Exception as e:
            errors += 1

            print(
                f"ERROR | ID={row['id']} | "
                f"{row['ticker']} | {e}"
            )

    print(
        f"Evaluation finished | "
        f"signals checked: {checked} | "
        f"errors: {errors}"
    )


if __name__ == "__main__":
    main()
