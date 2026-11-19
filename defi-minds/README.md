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

Use `--port` if the Hive listens on a loopback port other than 8700. The command uses the host's local operator login to create or update the roster and grant its minds the nine benchmark actions. Each registration then uses the mind's own authenticated Hive app action. The benchmark submits the Rholang deploy through the host's `chain-tools` signer. Hive keeps the credentials in its existing storage.

The four independent participants live in **DeFi Benchmark · Solo**. The three coalition members have separate identities and balances in **DeFi Benchmark · coalition_A**. Its private swarm room records their proposals and agreements. Both swarms compete on the same contract. The JSON's hold and stake-once benchmarks are comparison policies outside the participant roster.

The command waits for receipts and prints the finalized leaderboard entries, UTC deadline and block. Run it again after an interruption or to enter another hour. It reuses the Hive minds, skips IDs already registered in that round and refreshes changed strategy prompts. Each UTC hour has a fresh 100 STBL grant per participant.

Registration leaves each mind's run state under your control. To run a strategy, start its mind in the Hive and ask it to play the current DeFi Benchmark round. The strategy uses the enabled `app-act` actions for the duration of that round.
