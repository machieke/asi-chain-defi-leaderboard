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
