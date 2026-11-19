"""Start the native benchmark roster and install its market review schedules."""
import argparse
import json
import os
from pathlib import Path

from register import Hive, provision, roster

PREFIX = "[DeFi benchmark market review]"
OPENING = "0,1,2,3,4,5,7,10,15,20,25,30,35,40,45,50,55,59 * * * *"
TASKS = {
    "monopolist": "First arm opening-order: quantity 60, buy_limit 1.000, reserve 15.000. Include the optional "
                  "offer {quantity:16, start_price:1.500, floor_price:1.250, seconds:480} to list some stock "
                  "immediately after acquisition for the outside trader. "
                  "On the first funded consumer-market quote, acquire up to 60 affordable LOT from auction 0 "
                  "at <=1.000 each. Confirm the fill, consider taking remaining supply within your 85 LOT target, "
                  "then offer 4–8 LOT in a Dutch listing so outside traders can buy. Keep a cash reserve.",
    "oligarchy_member": "First arm your own opening-order: quantity 30, buy_limit 1.000, reserve 20.000. "
                        "On the first funded consumer-market quote buy 25–30 available LOT from auction 0 "
                        "at <=1.000 each. Individual opening acquisitions can proceed immediately. After confirmation, "
                        "coordinate your resale floor and small outside listings with the other two members in your room.",
    "high_frequency_trader": "Authorize a bounded execution policy with maker-order. Also arm an opening-order "
                             "for 8 LOT at buy_limit 1.000 and reserve 20.000. Example maker-order params: limit 48, clip 2, "
                             "inventory 8, buy_limit 1.420, sell_floor 1.450. Choose your thresholds. The local executor "
                             "then places individual real buys, partial sales and bid matches under your authenticated "
                             "identity. Let it work, review your buy/sale counts, and adjust spreads as peer offers "
                             "change. Do not duplicate its trades manually. A limit of zero stops the order.",
    "farmer": "Compare the opening LOT price with remaining staking rewards and direct STBL yield. Execute the "
              "higher-value position and confirm the transaction. Review exits against actual demand.",
    "sniper": "Track the best independent ask against remaining consumer capacity and LOT yield. Act when your "
              "conservative trigger is met. Reserve purchasing power for subsequent opportunities.",
}


def reminder(role):
    return (PREFIX + ' Execute app-act {"program":"defi-benchmark","action":"quote","params":{}} now. '
            'Use the returned round and me (null means register). Confirm outstanding receipts before another order. '
            'If you use standing orders, arm them now via app-act: choose round + 1 when demand is null or the '
            'opening has passed; use the current round during its first five minutes. The executor registers and '
            'buys within your own limits. An unchanged standing order only needs to be armed once. '
            'Use live quotes for discretionary activity in the current round. Your strategy: '
            + TASKS[role] + ' Use the actual finalized sizes and prices. Report filled LOT and STBL; refresh after '
            'partial fills or competing buyers. A listing by itself is not a sale.')


def opening_brief(role, board):
    hour = board["round"] if board.get("demand") and board["as_of"] < board["end"] - 3300 else board["round"] + 1
    quantities = {"monopolist": 60, "oligarchy_member": 30, "high_frequency_trader": 8}
    if role not in quantities:
        return ""
    opening = {"round": hour, "quantity": quantities[role], "buy_limit": "1.000", "reserve": "20.000"}
    if role == "monopolist":
        opening["offer"] = {"quantity": 16, "start_price": "1.500", "floor_price": "1.250", "seconds": 480}
    instructions = [("opening-order", opening)]
    if role == "high_frequency_trader":
        instructions.append(("maker-order", {"round": hour, "limit": 48, "clip": 2, "inventory": 8,
                                             "buy_limit": "1.420", "sell_floor": "1.450"}))
    commands = ["app-act " + json.dumps({"program": "defi-benchmark", "action": action, "params": params})
                for action, params in instructions]
    return ("\nYour standing strategy is ready to submit now. Execute the following commands in your own Hive "
            "turn and check each result. These are bounded instructions for the indicated round.\n" + "\n".join(commands))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=os.environ.get("HIVE_DATA_DIR"))
    parser.add_argument("--port", type=int, default=8700)
    parser.add_argument("--model", help="set this inference model for the seven participating minds")
    args = parser.parse_args()
    if args.data_dir is None:
        parser.error("supply --data-dir or HIVE_DATA_DIR")
    hive = Hive(args.port, args.data_dir)
    minds = provision(hive)
    agents = {a["id"]: a for a in hive.admin("agents")}
    roles = {name: marker.split(" / ")[1] for _, name, marker, _ in roster()}
    if any((args.model or agents[m.id]["model"]).startswith("mock/") for m in minds):
        parser.error("select a real inference model with --model or in OmegaHive")
    for mind in minds:
        agent, role = agents[mind.id], roles[mind.name]
        path = "agents/" + mind.id
        task = reminder(role)
        cron = OPENING if role in ("monopolist", "oligarchy_member", "high_frequency_trader") else "*/5 * * * *"
        if args.model and args.model != agent["model"]:
            hive.admin(path, {"model": args.model}, method="PATCH")
        wakes = [w for w in hive.admin(path + "/wakeups") if w["text"].startswith(PREFIX)]
        if len(wakes) > 1:
            raise RuntimeError(f"{mind.name} has multiple benchmark reminders")
        settings = {"cron": cron, "tz": "UTC", "text": task}
        changed = not wakes or any(wakes[0].get(k) != v for k, v in settings.items()) or not wakes[0]["enabled"]
        if changed and wakes:
            hive.admin("wakeups/" + wakes[0]["id"], {**settings, "enabled": True}, method="PATCH")
        elif changed:
            hive.admin(path + "/wakeups", settings)
        starting = agent["status"] not in ("awake", "starting")
        if starting:
            hive.admin(path + "/start", {})
        if starting or changed:
            view = hive.admin("programs/defi-benchmark/view", {"swarm_id": agent["swarm_id"]})
            board = next(i["board"] for i in view["items"] if i.get("board"))
            hive.admin(path + "/messages", {"text": task + f" The latest finalized round is {board['round']}."
                                            + opening_brief(role, board)})
        print(f"{mind.name}: {cron} UTC")


if __name__ == "__main__":
    main()
