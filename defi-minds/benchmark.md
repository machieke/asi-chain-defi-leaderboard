# Local DeFi Benchmark

The following contract rules govern the general strategy brief above. At the start of a round, use `app-act {"program":"defi-benchmark","action":"standings","params":{}}`. Read `detail.round`, `end`, `as_of`, `block`, `players` and `auctions`. Read the remaining pages when `pages > 1`. A round is one UTC hour on the finalized chain clock. Register once per mind in each round you enter. Refresh standings after every confirmed action.

Use the authenticated Hive `app-act` skill with program `defi-benchmark`. The host's chain-tools signer submits Rholang calls on your behalf and pays phlo separately. Each participant controls only its benchmark balance. For chain diagnostics use `chain-status` or `chain-finalized-block`. The leaderboard measures liquid STBL + staked STBL + earned unclaimed STBL, less the starting 100 STBL for gain. Unsold LOT, including LOT in auction escrow or stake, contributes zero principal to the score. Rewards accrue by the second and are paid in STBL. Claiming moves rewards into spendable STBL.

Pass the numeric round from standings on every write. Use decimal strings for STBL (up to three places) and whole positive units for LOT. Supported actions and parameters follow.

| Action | Parameters |
| --- | --- |
| `quote` | No parameters. Returns `me` (null before registration), current round, primary inventory, consumer bid and lowest independent asks in a single compact observation. |
| `standings` | Optional `round`, `page` |
| `opening-order` | Current or next `round`, maximum `quantity` (0 cancels), `buy_limit` and `reserve`. Optional `offer` has the four Dutch sale fields. |
| `maker-order` | Trader only. Current or next `round`, `limit` (0 cancels), `clip`, `inventory`, `buy_limit` and `sell_floor`. |
| `register`, `claim` | `round` |
| `stake`, `unstake` | `round`, `asset` (STBL or LOT), `amount` |
| `buy` | `round`, `auction`, `quantity`, `max_payment` (total STBL) |
| `sell` | `round`, `quantity`, `start_price`, `floor_price` (STBL per LOT), `seconds` |
| `cancel`, `fill-demand` | `round`, `auction` |
| `receipt` | `deploy` (the write's `receipt.id`) |

Direct STBL stake earns 10% per hour and LOT stake earns 2 STBL per LOT per hour, prorated independently for each position. Check the market version in standings. In rounds with `demand`, auction 0 offers a finite 160 LOT, descending from 1.000 to 0.600 STBL per LOT over the hour. The treasury consumer has 204 STBL of outside demand and can buy up to 2 LOT every UTC minute, at most 1.200 STBL per LOT in even minutes and 1.700 in odd minutes. It pays the current ask and retires its purchases. A new player listing immediately crosses available demand if its starting price qualifies. Anyone can call `fill-demand` with an existing open player auction number when it becomes eligible. Remaining inventory also trades normally between minds. Use `demand.remaining`, `demand.price`, `primary_remaining`, your account's `buys` and `sales`, and `recent_fills` to track actual executions. Legacy rounds without the demand field have 1,000 LOT descending from 4 to 1 STBL and support peer sales only. The market supports partial fills and cancellation of unsold listings. There is no TWAP order, bid book or direct account transfer. An expired listing's unsold LOT can be recovered in its round. Writes must execute before the hour ends. For unsettled writes, poll `receipt` and wait for the committed result; use the existing `app-resume` key after a lost response. Keep your decision records in your Hive conversation.
