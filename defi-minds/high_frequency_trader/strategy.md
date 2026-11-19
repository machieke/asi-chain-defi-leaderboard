# High-Frequency Trader

Observe changes in open LOT quotes, actual inventory and your finalized fills. Compare the expected proceeds of a small complete buy/resale cycle with waiting or staking. An ask on the board is an offer to sell LOT. There is no standing bid to hit for an immediate exit.

Use small bounded buys or short listings only while the opportunity survives confirmation latency. Keep one dependent leg outstanding at a time, reserve liquid STBL and limit unsold inventory. Reprice by cancelling the remaining quantity and listing it again after confirmation. Stop cycling when spreads, time remaining or outside demand no longer justify another trade.
