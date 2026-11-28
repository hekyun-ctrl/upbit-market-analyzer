# LBank futures observation service (prototype)

This is a **read-only** watcher for LBank BTC/USDT USDT-margined perpetuals.
It does **not** authenticate, inspect the user's positions, place orders, or auto-trade.
The 5-minute bars are **closed candles**; signals are informational, not orders.

## Separate Railway service
Do not change the existing upbit-market-analyzer service. Create a new service from
this repository with a start command:
```sh
pip install -r requirements-lbank.txt && python lbank_futures_monitor.py
```
Use a separate service name such as `lbank-futures-monitor`.
If Railway build installs the main requirements only, keep the start command shown.

Environment variables:
- `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` (set privately in Railway)
- `LBANK_POLL_SECONDS=60`
- `LBANK_ALERT_COOLDOWN_SECONDS=900`

No LBank API keys, withdrawals or order permissions are required.

## Verify before production
1. In the same Railway region/network, ensure ccxt exposes `BTC/USDT:USDT` as a swap.
2. Confirm OHLCV 5m timestamp/units and that data is futures, not spot.
3. Confirm at least 40 completed candles and non-stale timestamps.
4. Run on paper and compare independently with the LBank screen.
5. Verify Telegram delivery, duplicate/cooldown control and error logs.
6. Validate alerts against historical candles before calling them trading signals.
7. Keep existing Upbit deployment, secrets, storage, scheduler and code untouched.

The model does not report an entry price, stop loss or target because those
require spread, slippage, market regime and an explicit sizing policy.
