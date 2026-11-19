import asyncio
from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import program

setup = program.installation
PUBLIC = "04" + "ab" * 64
URI = "rho:id:" + "x" * 54
PARAMS = {"signer": PUBLIC, "max_fee": "5000000"}


class InstallationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.enterContext(patch.dict(os.environ, {"HIVE_DATA_DIR": temp.name, "DEFI_BENCHMARK_URI": "",
            "CHAIN_DEPLOY_PUBLIC_KEY": "", "CHAIN_DEPLOY_KEY_FILE": "", "CHAIN_NETWORK_ID": "testnet",
            "CHAIN_SHARD_ID": "root"}))
        self.writer = self.enterContext(patch.object(setup, "Deployer")).return_value
        self.writer.prepare.return_value = {"deployer": PUBLIC, "signature": "ab" * 70,
                                            "data": {"phloLimit": 5000000, "phloPrice": "1"}}
        self.account = self.enterContext(patch.object(setup, "account", return_value={
            "address": "test-address", "balance": "6000000"}))
        self.wallet = self.enterContext(patch.object(setup, "wallet")).return_value
        self.wallet.entries.return_value = [(PUBLIC, "TestWallet")]
        self.admin = SimpleNamespace(actor={"kind": "person", "local_operator": True, "route": "operator"},
                                     viewer={"kind": "operator", "local_operator": True, "route": "operator"})

    def act(self, action, params=None, ctx=None):
        return asyncio.run(program.act(ctx or self.admin, action, [], params or {}))

    def test_permissions_and_fee_gate(self):
        self.assertTrue(all(a['confirm'] and not a['agents'] for a in program.describe()['actions']
                            if a['id'] in program.SETUP_ACTIONS))
        for actor in ({"kind": "mind", "role": "admin"}, {"kind": "person", "role": "member"},
                      {"kind": "person", "role": "admin", "local_operator": False}, None):
            for action in program.SETUP_ACTIONS:
                self.assertEqual(self.act(action, ctx=SimpleNamespace(actor=actor))["status"], "refused")
        self.assertFalse(setup.directory().exists())
        self.wallet.entries.return_value = []
        self.assertEqual(self.act("create-signer")["status"], "done")
        self.wallet.generate.assert_called_once()
        self.wallet.entries.return_value = [(PUBLIC, "TestWallet")]
        self.act("create-signer")
        self.wallet.generate.assert_called_once()
        for params in ({**PARAMS, "signer": "unlisted"}, {**PARAMS, "max_fee": "1"}, {**PARAMS, "term": "Nil"}):
            self.assertEqual(self.act("deploy-benchmark", params)["status"], "refused")
        self.account.return_value["balance"] = "0"
        self.assertIn("Fund", self.act("deploy-benchmark", PARAMS)["message"])
        self.writer.submit.assert_not_called()

    def test_one_install_per_hive_across_retries_and_restarts(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: setup.install(PARAMS), range(4)))
        self.assertTrue(all(r["receipt"]["state"] == "pending" for r in results))
        self.writer.submit.assert_called_once()
        self.writer.prepare.assert_called_once()
        self.writer.receipt.return_value = {"state": "pending"}
        self.assertEqual(program.view(self.admin, None, None)["items"][0]["setup"]["phase"], "pending")
        self.writer.receipt.return_value = {"state": "committed", "value": URI}
        self.assertEqual(program.contract_uri(), URI)
        self.assertEqual(setup.read()["signer"], PUBLIC)
        self.assertNotIn("signed", setup.read())
        self.assertEqual(self.act("deploy-benchmark", PARAMS)["status"], "done")
        self.writer.submit.assert_called_once()
        with patch.dict(os.environ, {"CHAIN_SHARD_ID": "other"}), self.assertRaises(ValueError):
            program.contract_uri()

    def test_lost_submit_response_replays_identical_bytes(self):
        self.writer.submit.side_effect = [OSError("offline"), None]
        self.assertEqual(self.act("deploy-benchmark", PARAMS)["receipt"]["state"], "unknown")
        self.assertEqual(setup.read()["phase"], "prepared")
        self.assertEqual(self.act("resume-deployment")["receipt"]["state"], "pending")
        self.writer.prepare.assert_called_once()
        self.assertEqual(*self.writer.submit.call_args_list)
        self.writer.receipt.return_value = {"state": "refused", "message": "Expired"}
        self.assertEqual(setup.reconcile()["phase"], "failed")
        self.writer.submit.side_effect = None
        self.assertEqual(self.act("deploy-benchmark", PARAMS)["status"], "started")
        self.assertEqual(self.writer.prepare.call_count, 2)

    def test_configured_uri_and_nonoperator_views_do_not_touch_the_wallet(self):
        graph = program.view(SimpleNamespace(viewer={"kind": "person", "role": "member"}), None, None)
        self.assertFalse(graph["items"][0]["setup"]["operator"])
        self.wallet.entries.assert_not_called()
        with patch.dict(os.environ, {"DEFI_BENCHMARK_URI": URI}):
            self.assertEqual(program.contract_uri(), URI)
            self.assertEqual(self.act("deploy-benchmark", PARAMS)["status"], "done")
        self.assertFalse(self.writer.prepare.called)

    def test_named_and_unnamed_signers_use_asi_wallet_hex_addresses(self):
        from coincurve import PrivateKey
        public = PrivateKey.from_int(1).public_key.format(False).hex()
        address = "0x7e5f4552091a69125d5dfcb7b8c2659029395bdf"
        self.wallet.entries.return_value = [(PUBLIC, ""), (public, "TestWallet")]
        self.writer.writer_status.return_value = {"minPhloPrice": "1"}
        panel = setup.panel(self.admin, {}, public)
        self.assertEqual(panel["selected"], public)
        self.assertEqual(panel["keys"], [PUBLIC, public])
        self.assertEqual(panel["signers"][public], {"name": "TestWallet", "address": address})
        self.assertEqual(panel["signers"][PUBLIC]["name"], "")
        with patch.dict(os.environ, {"CHAIN_DEPLOY_PUBLIC_KEY": public.upper()}):
            self.assertEqual(setup.panel(self.admin, {})["signers"], {public: panel["signers"][public]})
        with patch.dict(os.environ, {"CHAIN_DEPLOY_KEY_FILE": "/configured/signer"}):
            self.assertEqual(setup.panel(self.admin, {})["signers"], {})

    def test_source_hash_and_uncertain_receipts_block_replacement(self):
        with patch.object(setup, "CONTRACT_SHA256", "0" * 64):
            self.assertEqual(self.act("deploy-benchmark", PARAMS)["status"], "refused")
        self.act("deploy-benchmark", PARAMS)
        self.writer.receipt.return_value = {"state": "committed", "value": "ok"}
        with self.assertRaisesRegex(ValueError, "registry URI"):
            setup.reconcile()
        self.assertEqual(setup.read()["phase"], "pending")
        self.writer.submit.assert_called_once()
