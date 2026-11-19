import sys
import unittest
import json
import sqlite3
from pathlib import Path
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from maker import next_order, opening_buy, next_key, run_mind


class MakerTest(unittest.TestCase):
    def setUp(self):
        self.order = dict(clip=2, inventory=8, buy_limit="1.420", sell_floor="1.450")
        self.book = dict(as_of=20, end=3600, me=dict(lots=0, staked_lots=0, cash="100.000"),
                         demand=dict(price="1.700", remaining=2), escrow_lots=0, my_listings=[],
                         expired=None, primary=dict(id="0", price="1.000", remaining=160, end=3600),
                         peer_asks=[dict(id="2", price="0.950", remaining=1, end=200)])

    def test_price_priority_cash_reserve_and_inventory(self):
        self.assertEqual(next_order(self.book, self.order), ("buy", dict(auction="2", quantity=1, max_payment="0.950")))
        self.book["me"]["cash"] = "20.900"
        self.assertIsNone(next_order(self.book, self.order))
        self.book["me"].update(cash="95.000", lots=7)
        self.book.update(demand=dict(price="1.200", remaining=2), escrow_lots=1)
        self.assertIsNone(next_order(self.book, self.order))

    def test_small_market_sale_existing_listing_and_cutoff(self):
        self.book["me"]["lots"] = 8
        action, params = next_order(self.book, self.order)
        self.assertEqual((action, params["quantity"], params["start_price"]), ("sell", 2, "1.700"))
        self.book["my_listings"] = [dict(id="3", price="1.550")]
        self.assertEqual(next_order(self.book, self.order), ("fill-demand", {"auction": "3"}))
        self.book["demand"]["remaining"] = 0
        self.assertIsNone(next_order(self.book, self.order))

        self.book.update(expired="3")
        self.assertEqual(next_order(self.book, self.order), ("cancel", {"auction": "3"}))
        self.book["as_of"] = 3590
        self.assertIsNone(next_order(self.book, self.order))

    def test_opening_size_and_cutoff(self):
        instruction = dict(round=0, quantity=60, buy_limit="1.000", reserve="20.000")
        self.book["me"].update(buys=0, cash="45.000")
        self.assertEqual(opening_buy(self.book, instruction)[1]["quantity"], 25)
        self.book["primary"]["remaining"] = 3
        self.assertEqual(opening_buy(self.book, instruction)[1]["quantity"], 3)
        self.book["me"]["buys"] = 1
        self.assertIsNone(opening_buy(self.book, instruction))
        self.book["me"]["buys"] = 0
        self.book["as_of"] = 300
        self.assertIsNone(opening_buy(self.book, instruction))

    def test_scheduled_seat_without_a_standing_order_registers_once_funded(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE program_actions(id,principal,program,action,status,result,created_at,idempotency_key)")
        db.execute("CREATE TABLE wakeups(agent_id,enabled,text)")
        db.execute("INSERT INTO wakeups VALUES ('farmer',0,'[DeFi benchmark market review] check quote')")
        hive = Mock(data=Path("/unused"))
        node = Mock()
        agent = {"id": "farmer", "name": "Farmer"}
        quote = {"me": None, "round": 1, "block": 12, "demand": None}
        hive.act.return_value = {"status": "done", "detail": quote}
        with patch("maker.current_token", return_value="test"):
            run_mind(db, hive, node, agent, 1, opening=True)
            hive.act.assert_not_called()
            db.execute("UPDATE wakeups SET enabled=1")
            run_mind(db, hive, node, agent, 1, opening=False)
            hive.act.assert_not_called()
            run_mind(db, hive, node, agent, 1, opening=True)
            self.assertEqual(hive.act.call_args.args[1], "quote")
            quote["demand"] = {"remaining": 2}
            run_mind(db, hive, node, agent, 1, opening=True)
            self.assertEqual(hive.act.call_args.args[1:], ("register", {"round": 1}, "executor-registration-1-farmer-1"))
            quote["me"] = {"buys": 0}
            run_mind(db, hive, node, agent, 1, opening=True)
        actions = [call.args[1] for call in hive.act.call_args_list]
        self.assertEqual(actions, ["quote", "quote", "register", "quote"])

    def test_pending_and_observer_lag_do_not_issue_a_second_order(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE program_actions(principal,program,idempotency_key,status,result,created_at)")
        node = Mock()
        args = (db, node, "agent:test", "executor-1-")
        self.assertEqual(next_key(*args, 3, 10), "executor-1-1")
        result = json.dumps({"status": "started", "receipt": {"id": "one"}})
        db.execute("INSERT INTO program_actions VALUES ('agent:test','defi-benchmark','executor-1-1','done',?,1)", (result,))
        node.receipt.return_value = {"state": "pending"}
        self.assertIsNone(next_key(*args, 3, 12))
        node.receipt.return_value = {"state": "committed", "value": "ok"}
        node.request.return_value = {"blockNumber": 13}
        self.assertIsNone(next_key(*args, 3, 12))
        self.assertEqual(next_key(*args, 3, 13), "executor-1-2")
        self.assertIsNone(next_key(*args, 1, 13))
