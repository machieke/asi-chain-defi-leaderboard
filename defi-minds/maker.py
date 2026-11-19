"""Execute the native trader's admitted, round-scoped maker order on a local Hive."""
import argparse
from datetime import UTC, datetime
from decimal import Decimal
import fcntl
import json
import os
from pathlib import Path
import sqlite3
import sys
import time

from omega_chain_deploy import Deployer
from chain_observer import ChainError, ObserverClient
from register import Hive, Mind, current_token, roster

RESERVE = Decimal("20")
TICK_SECONDS = 8


def next_order(quote, order):
    """One independent decision on the finalized book. All prices are STBL per LOT."""
    account, bid = quote["me"], quote["demand"]
    seconds = quote["end"] - quote["as_of"]
    if not account or not bid or seconds < 20:
        return None
    if quote["expired"] is not None:
        return "cancel", {"auction": quote["expired"]}
    floor = Decimal(order["sell_floor"])
    if quote["my_listings"]:
        for ask in quote["my_listings"]:
            if bid["remaining"] and floor <= Decimal(ask["price"]) <= Decimal(bid["price"]):
                return "fill-demand", {"auction": ask["id"]}
        return None
    if account["lots"] and bid["remaining"] and Decimal(bid["price"]) >= floor:
        size = min(account["lots"], bid["remaining"], order["clip"])
        return "sell", {"quantity": size, "start_price": bid["price"], "floor_price": str(floor),
                        "seconds": min(150, seconds - 12)}
    room = order["inventory"] - account["lots"] - account["staked_lots"] - quote["escrow_lots"]
    cash = Decimal(account["cash"]) - RESERVE
    if room <= 0 or cash <= 0 or seconds < 75:
        return None
    asks = sorted([quote["primary"], *quote["peer_asks"]], key=lambda a: Decimal(a["price"]))
    for ask in asks:
        price = Decimal(ask["price"])
        if not ask["remaining"] or price > Decimal(order["buy_limit"]) or ask["end"] <= quote["as_of"] + 5:
            continue
        size = min(room, order["clip"], ask["remaining"], int(cash / price))
        if size:
            return "buy", {"auction": ask["id"], "quantity": size, "max_payment": str(price * size)}
    return None


def active_order(db, principal, action, hour):
    return db.execute("SELECT id,result FROM program_actions WHERE principal=? AND program='defi-benchmark' "
                      "AND action=? AND status='done' AND json_extract(result,'$.status')='done' "
                      "AND json_extract(result,'$.detail.round')=? ORDER BY created_at DESC, rowid DESC LIMIT 1",
                      (principal, action, hour)).fetchone()


def next_key(db, node, principal, prefix, limit, observed_block):
    history = db.execute("SELECT status,result FROM program_actions WHERE principal=? AND program='defi-benchmark' "
                         "AND idempotency_key LIKE ? ORDER BY created_at DESC, rowid DESC",
                         (principal, prefix + "%")).fetchall()
    if len(history) >= limit:
        return None
    if history:
        last = history[0]
        if last["status"] != "done" or not last["result"]:
            return None
        submitted = json.loads(last["result"])
        deploy = submitted.get("receipt", {}).get("id")
        if deploy and submitted["status"] == "started":
            receipt = node.receipt(deploy)
            if receipt["state"] not in ("refused", "committed"):
                return None
            if receipt["state"] == "committed" and int(node.request("deploy/" + deploy)["blockNumber"]) > observed_block:
                return None
        elif submitted["status"] != "refused":
            return None
    return prefix + str(len(history) + 1)


def opening_buy(quote, order):
    account, primary = quote["me"], quote["primary"]
    if account["buys"] or quote["as_of"] >= order["round"] * 3600 + 300:
        return None
    cash = Decimal(account["cash"]) - Decimal(order["reserve"])
    price = Decimal(primary["price"])
    if price > Decimal(order["buy_limit"]) or cash <= 0:
        return None
    size = min(order["quantity"], primary["remaining"], int(cash / price))
    if size:
        return "buy", {"auction": "0", "quantity": size, "max_payment": str(price * size)}


def run_mind(db, hive, node, agent, hour, opening):
    principal = "agent:" + agent["id"]
    orders = {}
    for kind in ("opening-order", "maker-order"):
        if kind == "opening-order" and not opening:
            continue
        row = active_order(db, principal, kind, hour)
        if row:
            order = json.loads(row["result"])["detail"]
            if order.get("quantity", order.get("limit")):
                orders[kind] = (row["id"], order)
    enroll = opening and db.execute("SELECT 1 FROM wakeups WHERE agent_id=? AND enabled=1 "
                                    "AND text LIKE '[DeFi benchmark market review]%' LIMIT 1", (agent["id"],)).fetchone()
    if not orders and not enroll:
        return
    mind = Mind(agent["id"], agent["name"], current_token(hive.data / "agents" / agent["id"]))
    response = hive.act(mind, "quote", {})
    if response.get("status") != "done":
        return
    quote = response["detail"]
    if quote["round"] != hour or not quote["demand"]:
        return
    if not quote["me"] and enroll:
        key = next_key(db, node, principal, f"executor-registration-{hour}-{mind.id}-", 3, int(quote["block"]))
        if key:
            result = hive.act(mind, "register", {"round": hour}, key)
            print(datetime.now(UTC).isoformat(), mind.name, key, "register", result.get("status"), flush=True)
        return
    for kind, (record_id, order) in orders.items():
        intent = kind
        if not quote["me"]:
            if kind == "opening-order" and quote["as_of"] >= hour * 3600 + 300:
                continue
            choice, intent, limit = ("register", {}), "registration", 3
        elif kind == "opening-order":
            choice, limit = opening_buy(quote, order), 3
            offer = order.get("offer")
            if (not choice and offer and quote["me"]["buys"] and quote["me"]["lots"]
                    and quote["as_of"] < hour * 3600 + 300):
                intent, limit = "opening-offer", 1
                choice = "sell", {**offer, "quantity": min(offer["quantity"], quote["me"]["lots"]),
                                  "seconds": min(offer["seconds"], quote["end"] - quote["as_of"] - 12)}
        else:
            choice, limit = next_order(quote, order), order["limit"]
        if not choice:
            continue
        prefix = f"executor-{intent}-{hour}-{mind.id}-"
        key = next_key(db, node, principal, prefix, limit, int(quote["block"]))
        if not key:
            if intent == "opening-offer":
                continue
            return
        # An instruction can be cancelled or replaced during observation and finality checks.
        if active_order(db, principal, kind, hour)["id"] != record_id:
            return
        if not db.execute("SELECT 1 FROM agents WHERE id=? AND status='awake' AND deleted=0", (mind.id,)).fetchone():
            return
        action, params = choice
        result = hive.act(mind, action, {"round": hour, **params}, key)
        print(datetime.now(UTC).isoformat(), mind.name, key, action, params, result.get("status"),
              result.get("receipt", {}).get("id", ""), flush=True)
        return


def tick(db, hive, node, observer, seats):
    block = observer.finalized_block()
    hour, offset = divmod(int(block["timestamp"]), 3_600_000)
    rows = db.execute("SELECT a.id,a.name,s.name AS swarm,s.description,"
                      "substr(a.persona,1,instr(a.persona,char(10))-1) AS marker "
                      "FROM agents a JOIN swarms s ON s.id=a.swarm_id WHERE a.deleted=0 AND a.kind='omega' "
                      "AND a.status='awake' AND a.owner=s.owner AND s.owner='user:operator'").fetchall()
    for agent in rows:
        if (agent["swarm"], agent["description"], agent["name"], agent["marker"]) not in seats:
            continue
        try:
            run_mind(db, hive, node, agent, hour, offset < 300_000)
        except (RuntimeError, OSError, sqlite3.Error, ValueError, ChainError) as exc:
            print(agent["name"], str(exc), file=sys.stderr, flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=os.environ.get("HIVE_DATA_DIR"))
    parser.add_argument("--port", type=int, default=8700)
    args = parser.parse_args()
    if args.data_dir is None:
        parser.error("supply --data-dir or HIVE_DATA_DIR")
    with (args.data_dir / "defi-maker.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        db = sqlite3.connect((args.data_dir / "hive.db").resolve().as_uri() + "?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        hive = Hive(args.port, args.data_dir, operator_login=False)
        node = Deployer()
        observer = ObserverClient.from_env()
        seats = {(f"DeFi Benchmark · {coalition or 'Solo'}",
                  f"asi-chain-defi-leaderboard/defi-minds roster ({coalition or 'solo participants'})",
                  name, marker) for coalition, name, marker, _ in roster()}
        while True:
            try:
                tick(db, hive, node, observer, seats)
            except (RuntimeError, OSError, sqlite3.Error, ValueError, ChainError) as exc:
                print("Maker tick:", str(exc), file=sys.stderr, flush=True)
            time.sleep(TICK_SECONDS)


if __name__ == "__main__":
    main()
