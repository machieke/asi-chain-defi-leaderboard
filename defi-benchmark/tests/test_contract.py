"""Execute the production contract in an isolated Rholang evaluation.

DEFI_TEST_API points at a disposable F1R3node /api with exploratory deploys.
Only the block clock and registry wiring are replaced, inside this evaluation.
"""
import json
import os
from pathlib import Path
import unittest
import urllib.error
import urllib.request

from omega_chain_deploy import decode

SOURCE = Path(__file__).resolve().parents[1] / "contracts/benchmark.rho"


@unittest.skipUnless(os.getenv("DEFI_TEST_API"), "requires a F1R3node exploratory endpoint")
class ContractTest(unittest.TestCase):
    def test_two_minds_and_hour_boundary(self):
        steps = [
            (0, "register", '"Alice"', "a", "r", "ok"),
            (0, "register", '"Alice"', "a", "r2", "Already registered"),
            (0, "register", '"Bob"', "b", "r", "ok"),
            (0, "stake", '("STBL", 50000n)', "a", "s", "ok"),
            (0, "stake", '("STBL", 50000n)', "a", "s", "ok"),  # replay
            (0, "stake", '("STBL", 60000n)', "a", "x", "Insufficient balance"),
            (0, "buy", '("0", 3n, 2999n)', "a", "limit", "Price exceeds balance or maximum payment"),
            (0, "buy", '("0", 3n, 3000n)', "a", "buy", "ok"),
            (0, "sell", '(2n, 8000n, 2000n, 1800)', "a", "sell", "ok"),
            (0, "stake", '("LOT", 1n)', "a", "stake-lot", "ok"),
            (900, "buy", '("1", 1n, 5000n)', "b", "bid", "ok"),
            (1800, "buy", '("1", 1n, 5000n)', "b", "expired",
             "Auction closed, insufficient inventory or self purchase"),
            (1800, "cancel", '"1"', "a", "cancel", "ok"),
            (1800, "claim", 'Nil', "a", "claim", "ok"),
            (1800, "unstake", '("STBL", 50000n)', "a", "u", "ok"),
            (1800, "buy", '("0", 4n, 3200n)', "a", "refill", "ok"),
            (1800, "sell", '(4n, 1200n, 1100n, 300)', "a", "consumer", "ok"),
            (1800, "sell", '(4n, 1200n, 1100n, 300)', "a", "consumer", "ok"),
            (1800, "fill-demand", '"2"', "b", "full", "No executable consumer bid"),
            (1860, "fill-demand", '"2"', "b", "next-minute", "ok"),
            (3600, "claim", "Nil", "a", "late", "Round closed or not yet open"),
            (3600, "register", '"Alice"', "a", "new", "ok"),
        ]
        old, fresh = self.execute(steps)
        a, b = old["players"]["a"], old["players"]["b"]
        self.assertEqual((a["cash"], a["staked"], a["lots"], a["lockedLots"]), (107060, 0, 1, 1))
        self.assertEqual((b["cash"], b["lots"]), (95000, 1))
        self.assertEqual(old["demand"], {"minute": 31, "used": 2, "budget": 199240, "retired": 4})
        self.assertEqual((a["buys"], a["sales"], b["buys"], old["fillCount"]), (2, 3, 1, 5))
        self.assertEqual((old["auctions"]["0"]["quantity"], old["auctions"]["2"]["sold"]), (153, 4))
        self.assertEqual(fresh["auctions"]["0"]["quantity"], 160)
        self.assertEqual((fresh["fillCount"], fresh["demand"]["budget"]), (0, 204000))
        self.assertEqual(fresh["players"]["a"]["cash"], 100000)
        self.assertEqual(len(fresh["players"]), 1)

    def test_opening_scarcity_and_multiple_independent_trader_fills(self):
        sizes = {"monopoly": 60, "oligarch1": 28, "oligarch2": 28, "oligarch3": 28,
                 "trader": 8, "farmer": 8, "sniper": 0}
        steps = [(0, "register", json.dumps(mind), mind, "register", "ok") for mind in sizes]
        for second, (mind, size) in enumerate(sizes.items(), 1):
            if size:
                steps.append((second, "buy", f'("0", {size}n, {size * 1000}n)', mind, "open", "ok"))
        steps.append((20, "sell", '(12n, 1320n, 1320n, 1800)', "monopoly", "offer", "ok"))
        for tick in range(6):
            when = 60 + tick * 120
            steps.extend([(when, "sell", '(2n, 1650n, 1650n, 60)', "trader", f"sell{tick}", "ok"),
                          (when + 5, "buy", '("1", 2n, 2640n)', "trader", f"buy{tick}", "ok")])
        book, _ = self.execute(steps)
        self.assertEqual(book["auctions"]["0"]["quantity"], 0)
        self.assertEqual(book["auctions"]["1"]["sold"], 12)
        self.assertEqual(book["demand"]["retired"], 12)
        self.assertEqual((book["players"]["trader"]["buys"], book["players"]["trader"]["sales"]), (7, 6))
        self.assertEqual(book["players"]["monopoly"]["sales"], 6)
        lot_total = sum(p["lots"] + p["lockedLots"] for p in book["players"].values())
        self.assertEqual(lot_total + book["demand"]["retired"], 160)

    def execute(self, steps):
        body = ('new old, fresh in { api!("snapshot", 0, *old) | api!("snapshot", 1, *fresh) | '
                'for (@a <- old; @b <- fresh) { out!((a,b)) } }')
        for ts, op, args, mind, key, expected in reversed(steps):
            hour = 1 if key == "new" else 0
            body = (f'new ack in {{ ticks!({ts*1000}) | '
                    f'api!("execute", *owner, {hour}, "{mind}", "{key}", "{op}", {args}, *ack) | '
                    f'for (@v <- ack) {{ if (v == {json.dumps(expected)}) {{ {body} }} '
                    f'else {{ out!(("{key}",v)) }} }} }}')
        body = ('new impostor, res in { api!("execute", *impostor, 0, "z", "r", "register", "Z", *res) | '
                'for (@"Hive signer required" <- res) {' + body + '} }')
        src = SOURCE.read_text().replace('clock(`rho:block:data`)', 'clock, ticks')
        start = src.index('  insert!(bundle+')
        src = src[:start] + '  contract clock(ret) = {for (@t <- ticks){ret!(0,t,"")}} | ' + body + '\n}'
        term = 'new out in {' + src + '}'
        req = urllib.request.Request(os.environ["DEFI_TEST_API"] + '/explore-deploy',
                                     data=json.dumps({"term": term}).encode(),
                                     headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as res:
                values = [decode(v) for v in json.load(res)["expr"]]
        except urllib.error.HTTPError as exc:
            self.fail(exc.read().decode()[:2000])
        self.assertEqual(len(values), 1)
        old, fresh = values[0]
        self.assertIsInstance(old, dict, values)
        return old, fresh
