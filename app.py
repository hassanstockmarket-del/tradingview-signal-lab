import os
from datetime import datetime, timezone
from flask import Flask, request, jsonify
import psycopg
from psycopg.rows import dict_row

app = Flask(__name__)

def get_conn():
    return psycopg.connect(os.environ["DATABASE_URL"], row_factory=dict_row)

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
                payload JSONB NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_ticker_time ON signals(ticker, received_at DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_signals_signal ON signals(signal)")

@app.get("/")
def home():
    return jsonify(status="ok", service="TradingView Signal Lab")

@app.get("/health")
def health():
    return jsonify(status="ok")

@app.post("/tradingview")
def tradingview():
    secret = os.environ.get("WEBHOOK_SECRET", "")
    if secret and request.args.get("key") != secret:
        return jsonify(error="unauthorized"), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify(error="Send valid JSON"), 400

    ticker = str(data.get("ticker", "")).strip().upper()
    signal = str(data.get("signal", "")).strip().upper()
    direction = str(data.get("direction", "")).strip().upper()
    timeframe = str(data.get("timeframe", "")).strip()

    if not ticker or not signal or direction not in {"LONG", "SHORT", "NEUTRAL"}:
        return jsonify(error="ticker, signal and valid direction are required"), 400

    try:
        price = float(data["price"]) if data.get("price") is not None else None
        tv_time = int(data["time"]) if data.get("time") is not None else None
    except (TypeError, ValueError):
        return jsonify(error="price/time format invalid"), 400

    with get_conn() as conn:
        row = conn.execute("""
            INSERT INTO signals (ticker, signal, direction, timeframe, price, tv_time, payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s)
            RETURNING id, received_at
        """, (ticker, signal, direction, timeframe, price, tv_time, psycopg.types.json.Jsonb(data))).fetchone()

    return jsonify(ok=True, id=row["id"], received_at=row["received_at"].isoformat()), 201

@app.get("/signals")
def signals():
    limit = min(max(request.args.get("limit", 100, type=int), 1), 1000)
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT id, received_at, ticker, signal, direction, timeframe, price, tv_time
            FROM signals ORDER BY id DESC LIMIT %s
        """, (limit,)).fetchall()
    return jsonify(rows)
if os.environ.get("DATABASE_URL"):
    init_db()
if __name__ == "__main__":
    init_db()
    port = int(os.environ.get("PORT", 10000))
    app.run(host="0.0.0.0", port=port)
