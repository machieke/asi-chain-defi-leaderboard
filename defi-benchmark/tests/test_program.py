import asyncio
import hashlib
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

APP = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(APP))
import program


class ProgramTests(unittest.TestCase):
    def test_round_cutover_preserves_old_books_and_selects_the_new_contract(self):
        old, new = ("rho:id:" + c * 54 for c in ("a", "b"))
        state = {"uri": old, "upgrades": [{"from_round": 101, "uri": new}]}
        with patch.object(program.installation, "reconcile", return_value=state):
            self.assertEqual(program.contract_uri(100), old)
            self.assertEqual(program.contract_uri(101), new)
            self.assertEqual(program.contract_uri(200), new)

    def test_standing_instructions_are_round_bound_and_require_positive_spread(self):
        ctx = SimpleNamespace(actor={"kind": "mind", "id": "t"})
        params = {"round": 101, "clip": 2, "inventory": 8, "limit": 32,
                  "buy_limit": "1.42", "sell_floor": "1.45"}
        with patch.object(program, "standings", return_value={"round": 100, "demand": None}) as read:
            result = program.perform(ctx, "maker-order", params)
            self.assertEqual(result["detail"]["buy_limit"], "1.420")
            read.assert_called_with(trader="t")
            for bad in ({"round": 100}, {"round": 102}, {"limit": 49}, {"inventory": 1}, {"sell_floor": "1.4"}):
                with self.subTest(bad=bad), self.assertRaises(ValueError):
                    program.perform(ctx, "maker-order", {**params, **bad})
            self.assertEqual(program.perform(ctx, "opening-order", {"round": 101, "quantity": 0})["detail"],
                             {"round": 101, "quantity": 0})

    def test_final_scoring_and_ties(self):
        player = {"name": "Example", "cash": 50000, "staked": 50000, "pending": 200,
                  "remainder": 0, "lockedLots": 1, "lots": 500, "updated": 0}
        book = {"players": {"b": player, "a": player}, "auctions": {}}
        # Two hours later, principal plus exactly one hour of accrued STBL.
        with patch.object(program, "fetch_round", return_value=(0, 2, 7200, {"blockNumber": 1, "blockHash": "abc"}, book)):
            rows = program.standings(0)["players"]
        self.assertEqual([(p["id"], p["rank"], p["score"], p["gain"]) for p in rows],
                         [("a", 1, "107.200", "7.200"), ("b", 1, "107.200", "7.200")])

    def test_identity_and_parameters_checked_before_signing(self):
        ctx = SimpleNamespace(actor={"kind": "person", "id": "m1"}, command={"idempotency_key": "one"})
        with patch.object(program, "Deployer") as signer:
            for params in ({"round": 1}, {"round": 1, "mind": "someone-else"}):
                result = asyncio.run(program.act(ctx, "register", [], params))
                self.assertEqual(result["status"], "refused")
            signer.assert_not_called()
        for value in ("0.0001", -1, "1e3", "1n", True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                program.amount(value)
        self.assertEqual(program.amount("0.001"), 1)

    def test_hive_ui_lock_covers_the_served_bytes(self):
        folder = APP / "ui"
        actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                  for p in folder.iterdir() if p.name != "bundle.lock.json"}
        self.assertEqual(actual, json.loads((folder / "bundle.lock.json").read_text())["files"])
