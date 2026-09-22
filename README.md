# TradingView Signal Lab

Webhook receiver for TradingView alerts.

## Endpoint
POST `/tradingview?key=YOUR_SECRET`

Expected JSON:
```json
{"ticker":"NVDA","signal":"STRUCTURE_UP","direction":"LONG","timeframe":"30","price":100.25,"time":0}
```

Required Render environment variables:
- `DATABASE_URL` — Render Postgres connection string
- `WEBHOOK_SECRET` — a private random secret
