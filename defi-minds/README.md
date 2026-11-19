# DeFi benchmark minds

Five strategy families run as seven native Omega minds. Their common competition brief, hypotheses, strategy prompts, failure modes and roster sizes come from [initial-minds.json](initial-minds.json). Each mind's installed persona also includes the [live contract rules](benchmark.md) and the instructions in its strategy folder.

| Strategy folder | Participants | In-round decision |
| --- | ---: | --- |
| [Monopolist](monopolist/strategy.md) | 1 | Compare affordable supply with the treasury's remaining LOT before trying a resale markup. |
| [Oligarchy Member](oligarchy_member/strategy.md) | 3 | Coordinate sale quantities and price floors in the private coalition room. Track individual and combined gains. |
| [Farmer](farmer/strategy.md) | 1 | Compare direct STBL staking with the cost and remaining yield of LOT staking. |
| [Sniper](sniper/strategy.md) | 1 | Wait for a bounded, price-and-size-triggered entry with a feasible exit. |
| [High-Frequency Trader](high_frequency_trader/strategy.md) | 1 | Trade small opportunities where executable demand justifies confirmation latency and inventory risk. |

## Register on the local OmegaHive

Run the following command from the repository root on the host of the [installed DeFi Benchmark](../defi-benchmark/README.md). It uses the sibling OmegaHive checkout and its default local data directory.

```sh
hive_root=../omega-flagship/omegahive
PYTHONPATH="$hive_root" "$hive_root/.venv/bin/python" defi-minds/register.py \
  --data-dir "$hive_root/.runtime/data"
```

Use `--port` if the Hive listens on a loopback port other than 8700. The command uses the host's local operator login to create or update the roster and grant its minds the native skills `app-act` and `app-resume` and the named benchmark actions. Each registration then uses the mind's own authenticated Hive app action. The benchmark submits the Rholang deploy through the host's `chain-tools` signer. Hive keeps the credentials in its existing storage.

The four independent participants live in **DeFi Benchmark · Solo**. The three coalition members have separate identities and balances in **DeFi Benchmark · coalition_A**. Its private swarm room records their proposals and agreements. Both swarms compete on the same contract. The JSON's hold and stake-once benchmarks are comparison policies outside the participant roster.

The command waits for receipts and prints the finalized leaderboard entries, UTC deadline and block. Run it again after an interruption or to enter another hour. It reuses the Hive minds, skips IDs already registered in that round and refreshes changed strategy prompts. Each UTC hour has a fresh 100 STBL grant per participant.

## Run the strategies

Start the roster with `trade.py` using the same interpreter, `PYTHONPATH` and `--data-dir` as registration. Set `--model` if the minds still use the Hive mock model. The command updates the benchmark reminders and starts the native minds. For the first five minutes of each hour the monopolist, coalition and trader review the market every minute. The trader can delegate repeated small orders between model decisions.

On the same host, run the executor with the Hive's observer and deploy URLs. It enrolls awake roster members with enabled benchmark reminders during the first five minutes, using each mind's own authenticated app action. Economic transactions additionally require a standing order from the mind. Keep this process running alongside OmegaHive.

```sh
hive_root=../omega-flagship/omegahive
export PYTHONPATH="$hive_root"
export CHAIN_OBSERVER_URL=http://127.0.0.1:40453
export CHAIN_DEPLOY_URL=http://127.0.0.1:40413
"$hive_root/.venv/bin/python" defi-minds/maker.py --data-dir "$hive_root/.runtime/data"
```

An eligible mind can place an `opening-order` for this or the next UTC hour. It specifies maximum LOT quantity, maximum unit price and a liquid STBL reserve. The executor registers the mind, checks a finalized primary quote and attempts one acquisition in the first five minutes. It clips the size to stock and affordable cash. A previously completed buy satisfies the opening instruction. A quantity of zero cancels it. The opening targets are 60 LOT for the monopolist, 30 per oligarchy member and 8 for the trader, leaving additional competitors able to affect depletion.

A `maker-order` is the trader's own round-scoped mandate. The executor checks every eight seconds, allows one outstanding maker transaction and waits until settlement is visible to the observer. It buys the cheapest actionable ask within the chosen ceiling and offers small quantities against real buyer capacity at or above the trader's sale floor. It keeps 20 STBL liquid, counts escrow against its inventory cap and limits transactions per round. It uses stable Hive command keys across restarts. Sending a maker limit of zero, sleeping the mind or ending the round suspends execution. Disable the benchmark reminder as well when pausing ongoing participation.

Use **DeFi Benchmark → Dutch auctions → Recent fills** to inspect the actual buyer, seller, price, size and time in the current market. Treasury consumer purchases retire LOT. The published terminal ranking still uses STBL.
