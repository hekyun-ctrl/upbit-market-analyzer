# audited-breakout-v3.3

The October 1 rejection audit found both missed rallies and avoided declines.
This change adds narrowly evidenced exceptions without making all former
leaders eligible or treating same-day retrospective returns as future accuracy.

- An ordinary candidate can retain an RSI heat deduction instead of a hard RSI
  rejection only with a completed 5-minute breakout (volume at least 1.5 times
  the preceding 20 bars), completed 15-minute volume at least average, clean
  candle shape, contiguous recent minutes, top-2-percent eligible leadership,
  positive 15-minute momentum, confirmed WB, normal orderbook and spread, and
  current price within 0.5% above the completed close. Other ordinary gates and
  the original score policy still apply.
- This exception requires the full survival interval, renewed completed-bar
  evidence and normal liquidity. Leadership must remain in the top 2 percent.
- A historical resistance becomes support only after a completed breakout
  close clears it by 0.1%, and current price plus the completed minute close
  hold its 0.2% tolerance. Survival checks retain that support requirement.
  Unbroken overhead resistance is still considered in the actual reward plan.
- The clean impulse's own day high is not added as a second historical zone;
  independently repeated historical peaks remain in the resistance set.
- Screening records now retain the actual evaluated quote, snapshot and ticker
  times, completed candle times and screening metrics. Survival and dispatch
  stages update the quote rather than reusing the initial detection price.

QKC at 18:50 KST was not an RSI-only rejection: low daily traded value and weak
book support were also present, and the 18:55 recheck had a 0.92% spread.
These changes deliberately do not automatically promote that initial alert.
No orders, manual Telegram sends, or production deployments are performed by
these code edits. Evaluate new and unchanged paths on subsequent data before
claiming improvement in win rate or realized returns.
