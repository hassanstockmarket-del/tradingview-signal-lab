import os
from datetime import datetime, timezone, timedelta

import requests
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from flask import Flask, request, jsonify

app = Flask(__name__)

DATABASE_URL = os.environ["DATABASE_URL"]
WEBHOOK_SECRET = os.environ.get("WEBHOOK_SECRET", "")
ALPACA_API_KEY = os.environ["ALPACA_API_KEY"]
ALPACA_SECRET_KEY = os.environ["ALPACA_SECRET_KEY"]

ALPACA_DATA_URL = "https://data.alpaca.markets"


def get_conn():
    return psycopg.connect(DATABASE_URL, row_factory=dict_row)


def init_db():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signals (
                id BIGSERIAL PRIMARY KEY,
                received_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                ticker TEXT NOT NULL,
                signal TEXT NOT NULL,
                direction TEXT NOT NULL,
                timeframe TEXT,
                price DOUBLE PRECISION,
                tv_time BIGINT,
                payload JSONB NOT NULL,

                day1_price DOUBLE PRECISION,
                day1_return DOUBLE PRECISION,
                day1_checked_at TIMESTAMPTZ,

                day2_price DOUBLE PRECISION,
                day2_return DOUBLE PRECISION,
                day2_checked_at TIMESTAMPTZ
            )
        """)

        conn.execute("""
            ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS day1_price DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS day1_return DOUBLE PRECISION
        """)

        conn.execute("""
            ALTER TABLE signals
            ADD COLUMN IF NOT EXISTS day1_checked_at TIMESTAMPTZ
        """)

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

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_ticker_time
            ON signals(ticker, received_at DESC)
        """)

        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_signals_signal
            ON signals(signal)
        """)


def alpaca_headers():
    return {
        "APCA-API-KEY-ID": ALPACA_API_KEY,
        "APCA-API-SECRET-KEY": ALPACA_SECRET_KEY
    }


def get_daily_bars(ticker, start_time, end_time):
    url = f"{ALPACA_DATA_URL}/v2/stocks/{ticker}/bars"

    params = {
        "timeframe": "1Day",
        "start": start_time.isoformat(),
        "end": end_time.isoformat(),
        "adjustment": "raw",
        "feed": "iex",
        "limit": 100
    }

    response = requests.get(
        url,
        headers=alpaca_headers(),
        params=params,
        timeout=20
    )

    response.raise_for_status()

    return response.json().get("bars", [])


def calculate_return(signal_price, future_price, direction):
    if not signal_price or signal_price <= 0:
        return None

    raw_return = ((future_price - signal_price) / signal_price) * 100

    if direction == "SHORT":
        raw_return *= -1

    return round(raw_return, 4)


def evaluate_signal(row):
    signal_time = row["received_at"]
    now = datetime.now(timezone.utc)

    # Fetch enough calendar time to cover weekends/holidays.
    bars = get_daily_bars(
        row["ticker"],
        signal_time - timedelta(days=1),
        now + timedelta(days=1)
    )

    # Only daily bars after the calendar date on which signal arrived.
    signal_date = signal_time.date()

    future_bars = []

    for bar in bars:
        bar_time = datetime.fromisoformat(
            bar["t"].replace("Z", "+00:00")
        )

        if bar_time.date() > signal_date:
            future_bars.append(bar)

    updates = {}

    # First completed trading day after signal.
    if row["day1_price"] is None and len(future_bars) >= 1:
        day1_price = float(future_bars[0]["c"])

        updates["day1_price"] = day1_price
        updates["day1_return"] = calculate_return(
            row["price"],
            day1_price,
            row["direction"]
        )

    # Second completed trading day after signal.
    if row["day2_price"] is None and len(future_bars) >= 2:
        day2_price = float(future_bars[1]["c"])

        updates["day2_price"] = day2_price
        updates["day2_return"] = calculate_return(
            row["price"],
            day2_price,
            row["direction"]
        )

    if updates:
        with get_conn() as conn:

            if "day1_price" in updates:
                conn.execute("""
                    UPDATE signals
                    SET day1_price = %s,
                        day1_return = %s,
                        day1_checked_at = NOW()
                    WHERE id = %s
                """, (
                    updates["day1_price"],
                    updates["day1_return"],
                    row["id"]
                ))

            if "day2_price" in updates:
                conn.execute("""
                    UPDATE signals
                    SET day2_price = %s,
                        day2_return = %s,
                        day2_checked_at = NOW()
                    WHERE id = %s
                """, (
                    updates["day2_price"],
                    updates["day2_return"],
                    row["id"]
                ))

    return updates


def evaluate_pending():
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT *
            FROM signals
            WHERE price IS NOT NULL
              AND (day1_price IS NULL OR day2_price IS NULL)
            ORDER BY id ASC
            LIMIT 200
        """).fetchall()

    evaluated = 0

    for row in rows:
        try:
            updates = evaluate_signal(row)

            if updates:
                evaluated += 1

        except Exception as e:
            print(
                f"Evaluation error for {row['ticker']} "
                f"signal {row['id']}: {e}"
            )

    return evaluated


@app.get("/")
def home():
    return jsonify(
        status="ok",
        service="TradingView Signal Lab",
        evaluation="1 trading day + 2 trading days"
    )


@app.get("/health")
def health():
    return jsonify(status="ok")


@app.post("/tradingview")
def tradingview():
    secret = request.args.get("key", "")

    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        return jsonify(error="unauthorized"), 401

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify(error="Send valid JSON"), 400

    ticker = str(data.get("ticker", "")).strip().upper()
    signal = str(data.get("signal", "")).strip().upper()
    direction = str(data.get("direction", "")).strip().upper()
    timeframe = str(data.get("timeframe", "")).strip()

    if not ticker:
        return jsonify(error="ticker is required"), 400

    if not signal:
        return jsonify(error="signal is required"), 400

    if direction not in {"LONG", "SHORT", "NEUTRAL"}:
        return jsonify(error="invalid direction"), 400

    try:
        price = (
            float(data["price"])
            if data.get("price") is not None
            else None
        )

        tv_time = (
            int(data["time"])
            if data.get("time") is not None
            else None
        )

    except (TypeError, ValueError):
        return jsonify(error="price/time format invalid"), 400

    with get_conn() as conn:
        row = conn.execute("""
            INSERT INTO signals
            (
                ticker,
                signal,
                direction,
                timeframe,
                price,
                tv_time,
                payload
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            RETURNING id, received_at
        """, (
            ticker,
            signal,
            direction,
            timeframe,
            price,
            tv_time,
            Jsonb(data)
        )).fetchone()

    # Every new TradingView signal also evaluates older signals.
    try:
        evaluate_pending()
    except Exception as e:
        print(f"Pending evaluation error: {e}")

    return jsonify(
        ok=True,
        id=row["id"],
        received_at=row["received_at"].isoformat()
    ), 201


@app.get("/signals")
def signals():
    limit = min(
        max(request.args.get("limit", 100, type=int), 1),
        1000
    )

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                id,
                received_at,
                ticker,
                signal,
                direction,
                timeframe,
                price,
                tv_time,
                day1_price,
                day1_return,
                day1_checked_at,
                day2_price,
                day2_return,
                day2_checked_at
            FROM signals
            ORDER BY id DESC
            LIMIT %s
        """, (limit,)).fetchall()

    return jsonify(rows)


@app.get("/evaluate")
def evaluate():
    secret = request.args.get("key", "")

    if WEBHOOK_SECRET and secret != WEBHOOK_SECRET:
        return jsonify(error="unauthorized"), 401

    evaluated = evaluate_pending()

    return jsonify(
        ok=True,
        evaluated=evaluated
    )


init_db()


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 10000))

    app.run(
        host="0.0.0.0",
        port=port
    )
