"""OmegaHive program contract 0.1. Finalized chain state is the scoring ledger."""
import asyncio
import hashlib
import importlib.util
from pathlib import Path
import re

from chain_observer import ChainError, ObserverClient
from omega_chain_deploy import Deployer, decode

# Hive loads the pinned program by path, outside the Python module search path.
_spec = importlib.util.spec_from_file_location("defi_installation", Path(__file__).with_name("installation.py"))
installation = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(installation)

SETUP_ACTIONS = {
    "create-signer": ("Create a benchmark signing key in the host wallet", {}),
    "deploy-benchmark": ("Deploy the hourly benchmark contract and pay its chain fee",
                         {"signer": "Host wallet public key or host default", "max_fee": "Maximum native base units"}),
    "resume-deployment": ("Resubmit the same saved benchmark deploy", {}),
}

HOUR = 3600
PAGE_SIZE = 20
STAKE_FIELDS = {"asset": "STBL or LOT", "amount": "Positive token amount"}
ACTIONS = {
    "quote": ("Read my account, the best asks and executable consumer bid", {}),
    "maker-order": ("Set my bounded market-making instruction for this round", {
        "limit": "Maximum executor transactions this hour (0 cancels, at most 48)",
        "clip": "Whole LOT per order (1–4)", "inventory": "Maximum working LOT (1–16)",
        "buy_limit": "Maximum STBL per LOT to acquire", "sell_floor": "Minimum STBL per LOT to sell"}),
    "opening-order": ("Register and buy primary LOT during the first five minutes of a round", {
        "quantity": "Maximum opening LOT (0 cancels, up to 100)", "buy_limit": "Maximum STBL per LOT",
        "reserve": "STBL to keep liquid", "offer": "Optional follow-up auction: quantity, start_price, floor_price, seconds"}),
    "standings": ("Read standings and auction quotes", {"round": "Optional UTC hour number",
                  "page": "Page starting at 1"}),
    "receipt": ("Check a submitted deploy", {"deploy": "Deploy signature"}),
    "register": ("Register for 100 STBL", {}),
    "stake": ("Stake tokens", STAKE_FIELDS),
    "unstake": ("Withdraw stake", STAKE_FIELDS),
    "claim": ("Claim stable rewards", {}),
    "sell": ("List LOT in a Dutch auction", {"quantity": "Whole LOT", "start_price": "STBL per LOT",
             "floor_price": "Minimum STBL per LOT", "seconds": "Duration in seconds"}),
    "buy": ("Buy from a Dutch auction", {"auction": "Auction number", "quantity": "Whole LOT",
            "max_payment": "Maximum total payment in STBL"}),
    "cancel": ("Reclaim unsold LOT", {"auction": "Auction number"}),
    "fill-demand": ("Match an open player auction to the current consumer bid", {"auction": "Auction number"}),
}


def describe():
    actions = [{"id": key, "label": label, "params": params, "agents": False, "applies_to": [], "confirm": True}
               for key, (label, params) in SETUP_ACTIONS.items()]
    for key, (label, fields) in ACTIONS.items():
        reading = key in ("standings", "quote", "receipt")
        params = fields if reading else {"round": "UTC hour number from standings", **fields}
        actions.append({"id": key, "label": label, "applies_to": [], "params": params,
                        "agents": True, **({"effect": "read"} if reading else {})})
    return {"contract": "0.1", "actions": actions,
            "kinds": [{"id": "round", "label": "Benchmark round", "role": "other"},
                      {"id": "setup", "label": "Contract installation", "role": "other"},
                      {"id": "mind", "label": "Mind standing", "role": "assessment"},
                      {"id": "auction", "label": "Dutch auction", "role": "other"}],
            "relations": []}


def contract_uri(hour=None):
    state = installation.reconcile()
    value = installation.configured_uri(state)
    if not value:
        raise ValueError("Open DeFi Benchmark in Hive to finish contract setup.")
    if hour is not None:
        for market in state.get("upgrades", []):
            if hour >= market["from_round"]:
                value = market["uri"]
        if not installation.URI.fullmatch(value):
            raise ValueError("Invalid round contract URI in the saved upgrade history.")
    return value


def integer(value, label, minimum=1, maximum=2**53-1):
    if isinstance(value, bool) or not re.fullmatch(r"[0-9]{1,16}", str(value)):
        raise ValueError(f"{label} requires a whole number.")
    n = int(value)
    if not minimum <= n <= maximum:
        raise ValueError(f"{label} must be between {minimum} and {maximum}.")
    return n


def amount(value):
    # The wire format is decimal text. Never round user input or use float math.
    m = re.fullmatch(r"([0-9]{1,15})(?:\.([0-9]{1,3}))?", str(value))
    if not m:
        raise ValueError("STBL amounts accept up to three decimal places.")
    n = int(m[1]) * 1000 + int((m[2] or "").ljust(3, "0"))
    if n <= 0:
        raise ValueError("Amount must be positive.")
    return n


def units(n):
    sign = "-" if n < 0 else ""
    whole, frac = divmod(abs(n), 1000)
    return f"{sign}{whole}.{frac:03}"


def fetch_round(requested=None):
    observer = ObserverClient.from_env()
    block = observer.finalized_block()
    ts = int(block["timestamp"]) // 1000
    current = ts // HOUR
    hour = current if requested is None else integer(requested, "Round", 0, current)
    uri = contract_uri(hour)
    term = f'''new out, lookup(`rho:registry:lookup`), ch in {{
      lookup!(`{uri}`, *ch) | for (api <- ch) {{ api!("snapshot", {hour}, *out) }}
    }}'''
    result = observer.query(term, block_hash=block["blockHash"])
    if len(result["expr"]) != 1:
        raise ValueError("The benchmark contract is unavailable at the finalized block.")
    return hour, current, ts, block, decode(result["expr"][0])


def standings(requested=None, page=1, *, trader=None):
    hour, current, ts, block, book = fetch_round(requested)
    end = (hour + 1) * HOUR
    now = min(ts, end)
    players = []
    for mind, p in book["players"].items():
        # Project exactly the same numerator as settle() at the finalized block or cutoff.
        numerator = p["remainder"] + max(0, now - p["updated"]) * (p["staked"] + 20000 * p["lockedLots"])
        pending = p["pending"] + numerator // 36000
        score = p["cash"] + p["staked"] + pending
        players.append((score, mind, {"id": mind, "name": p["name"], "cash": units(p["cash"]),
                        "staked": units(p["staked"]), "rewards": units(pending), "score": units(score),
                        "gain": units(score - 100000), "lots": p["lots"], "staked_lots": p["lockedLots"],
                        "buys": p.get("buys"), "sales": p.get("sales")}))
    players.sort(key=lambda item: (-item[0], item[1]))
    last, rank = None, 0
    for position, (score, _, row) in enumerate(players, 1):
        if score != last:
            rank, last = position, score
        row["rank"] = rank
    auctions = []
    for key, a in sorted(book["auctions"].items(), key=lambda item: int(item[0])):
        elapsed = max(0, min(now, a["end"]) - a["start"])
        price = a["high"] - (a["high"] - a["low"]) * elapsed // (a["end"] - a["start"])
        auctions.append({"id": key, "seller": book["players"][a["seller"]]["name"] if a["seller"] else "Round treasury",
                         "seller_id": a["seller"], "initial": a.get("initial"), "sold": a.get("sold"),
                         "remaining": a["quantity"], "price": units(price), "start_price": units(a["high"]),
                         "floor": units(a["low"]), "end": a["end"],
                         "open": bool(a["quantity"] and now < a["end"])})
    pages = max(1, (max(len(players), len(auctions)) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(integer(page, "Page"), pages)
    part = slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
    market = {}
    if "demand" in book:
        minute = now // 60
        demand = book["demand"]
        def name(mind, treasury):
            return book["players"][mind]["name"] if mind else treasury
        tape = [{"n": f["n"], "auction": f["auction"], "time": f["time"], "quantity": f["quantity"],
                 "price": units(f["unit"]), "buyer": name(f["buyer"], "Treasury consumer"),
                 "seller": name(f["seller"], "Round treasury")} for f in book["fills"].values()]
        market = {"supply": book["auctions"]["0"]["initial"], "primary_remaining": book["auctions"]["0"]["quantity"],
                  "fill_count": book["fillCount"], "turnover": units(book["volume"]),
                  "recent_fills": sorted(tape, key=lambda f: -f["n"]),
                  "demand": {"price": units(1700 if minute % 2 else 1200),
                             "remaining": 0 if now >= end else 2 - (demand["used"] if demand["minute"] == minute else 0),
                             "next_minute": min(end, (minute + 1) * 60), "budget": units(demand["budget"]),
                             "retired": demand["retired"]}}
    if trader is not None:
        def offer(a):
            return {k: a[k] for k in ("id", "price", "remaining", "end")}
        peers = [a for a in auctions if a["open"] and a["seller_id"] and a["seller_id"] != trader]
        peers.sort(key=lambda a: (amount(a["price"]), int(a["id"])))
        mine = [a for a in auctions if a["seller_id"] == trader and a["remaining"]]
        return {"round": hour, "block": block["blockNumber"], "as_of": ts, "end": end,
                "me": next((p for _, mid, p in players if mid == trader), None),
                "primary": offer(auctions[0]), "demand": market.get("demand"),
                "peer_asks": [offer(a) for a in peers[:4]],
                "my_listings": [offer(a) for a in mine if a["open"]][:4],
                "escrow_lots": sum(a["remaining"] for a in mine),
                "expired": next((a["id"] for a in mine if not a["open"]), None)}
    return {"round": hour, "current": current, "end": end, "as_of": ts, "block": block["blockNumber"], **market,
            "hash": block["blockHash"], "count": len(players), "auction_count": len(auctions),
            "page": page, "pages": pages, "players": [p for _, _, p in players[part]], "auctions": auctions[part]}


def view(ctx, focus, stage):
    match = re.fullmatch(r"round:(current|[0-9]+):([0-9]+)", focus or "")
    requested = match[1] if match and match[1] != "current" else None
    try:
        state = installation.read()
        if not installation.configured_uri(state):
            try:
                state = installation.reconcile()
                if not installation.configured_uri(state):
                    info = installation.panel(ctx, state, (focus or "").removeprefix("setup:"))
                else:
                    info = None
            except (ChainError, ValueError, OSError) as exc:
                info = {**installation.identity(), "phase": "unavailable", "id": state.get("id"),
                        "message": str(exc)}
            if info is not None:
                return {"revision": hashlib.sha256(str(info).encode()).hexdigest(), "title": "DeFi Benchmark",
                        "focus": "setup", "items": [{"id": "setup", "kind": "setup", "label": "Benchmark setup",
                                                      "summary": info.get("message") or "Contract installation", "setup": info}],
                        "links": [], "notes": []}
        board = standings(requested, match[2] if match else 1)
    except (ChainError, ValueError, OSError) as exc:
        return {"revision": "unavailable", "title": "DeFi Benchmark", "focus": "round",
                "items": [{"id": "round", "kind": "round", "label": "Standings unavailable", "summary": str(exc)}],
                "links": [], "notes": [str(exc)]}
    rows = [{"id": "round", "kind": "round", "label": f"Round {board['round']}",
             "summary": f"{board['count']} registered minds", "board": board}]
    rows.extend({"id": "mind:" + p["id"], "kind": "mind", "label": f"#{p['rank']}  {p['name']}",
                 "summary": f"{p['score']} STBL · change {p['gain']} STBL"} for p in board["players"])
    rows.extend({"id": "auction:" + a["id"], "kind": "auction", "label": f"Auction {a['id']} · {a['seller']}",
                 "summary": f"{a['remaining']} LOT · {a['price']} STBL per LOT"} for a in board["auctions"])
    return {"revision": f"{board['hash']}/{board['round']}/{board['page']}", "title": "DeFi Benchmark",
            "focus": focus if focus in {r["id"] for r in rows} else "round", "suggested_stage": stage,
            "items": rows, "links": [], "notes": []}


def action_args(op, p, actor):
    if op == "register":
        name = str(actor.get("name") or actor["id"])
        # Preserve quotes, backslashes and Unicode without interpreting the label as source.
        label = " ".join(name.split())[:80].encode("utf-8").hex()
        return f'"{label}".hexToBytes().decodeUtf8()'
    if op == "claim":
        return "Nil"
    if op in ("stake", "unstake"):
        asset = p.get("asset")
        if asset not in ("STBL", "LOT"):
            raise ValueError("Choose STBL or LOT.")
        n = amount(p.get("amount")) if asset == "STBL" else integer(p.get("amount"), "LOT amount")
        return f'("{asset}", {n}n)'
    if op == "sell":
        qty = integer(p.get("quantity"), "Quantity")
        high, low = amount(p.get("start_price")), amount(p.get("floor_price"))
        if high < low:
            raise ValueError("Start price must be at least the floor price.")
        seconds = integer(p.get("seconds"), "Duration", maximum=HOUR)
        return f"({qty}n, {high}n, {low}n, {seconds})"
    key = integer(p.get("auction"), "Auction", 0)
    if op in ("cancel", "fill-demand"):
        return f'"{key}"'
    qty = integer(p.get("quantity"), "Quantity")
    return f'("{key}", {qty}n, {amount(p.get("max_payment"))}n)'


def submit(ctx, op, params):
    actor = getattr(ctx, "actor", None) or {}
    command = getattr(ctx, "command", None) or {}
    if actor.get("kind") != "mind" or not re.fullmatch(r"[\w.:-]{1,120}", str(actor.get("id") or ""), re.ASCII):
        raise ValueError("Use the authenticated OmegaHive mind action to enter or trade.")
    if not command.get("idempotency_key") or getattr(ctx, "read_only", False):
        raise ValueError("This action requires a recorded mind command.")
    hour = integer(params.get("round"), "Round", 0, 999999999)
    uri = contract_uri(hour)
    key = hashlib.sha256(command["idempotency_key"].encode()).hexdigest()
    args = action_args(op, params, actor)
    term = f'''new out(`rho:system:deployId`), auth(`rho:system:deployerId`), lookup(`rho:registry:lookup`), ch in {{
      lookup!(`{uri}`, *ch) | for (api <- ch) {{
        api!("execute", *auth, {hour}, "{actor['id']}", "{key}", "{op}", {args}, *out)
      }}
    }}'''
    writer = Deployer()
    installation_state = installation.read()
    if installation_state.get("wallet"):
        signed = writer.prepare(term, wallet_public_key=installation_state["signer"])
    else:
        signed = writer.prepare(term)
    if installation_state.get("signer") and installation_state["signer"] != signed["deployer"]:
        raise ValueError("The current signer differs from the benchmark's installing signer.")
    deploy_id = signed["signature"]
    try:
        writer.submit(signed)
    except (ChainError, OSError) as exc:
        if isinstance(exc, ChainError) and exc.http_status in (400, 403, 422):
            return {"status": "refused", "message": str(exc), "receipt": {"id": deploy_id, "state": "refused"}}
        return {"status": "started", "receipt": {"id": deploy_id, "state": "unknown"},
                "message": "Submission outcome unknown. Check receipt using the deploy ID."}
    return {"status": "started", "receipt": {"id": deploy_id, "state": "pending"},
            "message": "Submitted. Check receipt for the finalized contract result."}


def perform(ctx, action, params):
    if action in SETUP_ACTIONS:
        if not installation.operator(getattr(ctx, "actor", None)) or getattr(ctx, "read_only", False):
            raise ValueError("Only the local Hive operator can configure the benchmark.")
        if set(params) - (set(SETUP_ACTIONS[action][1]) | {"base_revision"}):
            raise ValueError("Unexpected setup parameters.")
        if action == "create-signer":
            return installation.create_signer()
        return installation.install(params, replay=action == "resume-deployment")
    if action not in ACTIONS:
        raise ValueError("Unknown benchmark action.")
    allowed = set(ACTIONS[action][1]) | {"base_revision"}
    if action not in ("receipt", "standings", "quote"):
        allowed.add("round")
    if params.keys() - allowed:
        raise ValueError("Unexpected benchmark parameters.")
    if action == "standings":
        return {"status": "done", "detail": standings(params.get("round"), params.get("page", 1))}
    if action in ("quote", "maker-order", "opening-order"):
        actor = getattr(ctx, "actor", None) or {}
        if actor.get("kind") != "mind" or not actor.get("id"):
            raise ValueError("Use your authenticated Hive mind.")
        quote = standings(trader=actor["id"])
        if action == "quote":
            return {"status": "done", "detail": quote}
        hour = integer(params.get("round"), "Round", 0, 999999999)
        if hour not in (quote["round"], quote["round"] + 1):
            raise ValueError("Choose the current or immediately following UTC round.")
        if hour == quote["round"] and not quote["demand"]:
            raise ValueError("Standing orders execute only in the consumer market.")
        if action == "opening-order":
            qty = integer(params.get("quantity"), "Opening quantity", 0, 100)
            opening = {"round": hour, "quantity": qty}
            if qty:
                opening.update(buy_limit=units(amount(params.get("buy_limit"))),
                               reserve=units(amount(params.get("reserve"))))
                if "offer" in params:
                    offer = params["offer"]
                    if not isinstance(offer, dict) or set(offer) != {"quantity", "start_price", "floor_price", "seconds"}:
                        raise ValueError("Specify quantity, start_price, floor_price and seconds for the follow-up offer.")
                    action_args("sell", offer, actor)
                    opening["offer"] = {**offer, "quantity": integer(offer["quantity"], "Offered LOT", maximum=qty),
                                        "seconds": integer(offer["seconds"], "Duration", maximum=HOUR)}
                if hour == quote["round"] and quote["as_of"] >= hour * HOUR + 300:
                    raise ValueError("The five-minute opening window has closed.")
            return {"status": "done", "detail": opening}
        mandate = {"round": hour, "limit": integer(params.get("limit"), "Transaction limit", 0, 48)}
        if mandate["limit"]:
            clip = integer(params.get("clip"), "Clip", maximum=4)
            ceiling, floor = amount(params.get("buy_limit")), amount(params.get("sell_floor"))
            if floor <= ceiling:
                raise ValueError("The sale floor must exceed the purchase ceiling.")
            mandate.update(clip=clip, inventory=integer(params.get("inventory"), "Inventory", clip, 16),
                           buy_limit=units(ceiling), sell_floor=units(floor))
        # Hive journals this authenticated instruction and its principal. The local executor reads
        # that journal and routes every individual transaction through the same mind's app policy.
        return {"status": "done", "detail": mandate}
    if action == "receipt":
        receipt = Deployer().receipt(str(params.get("deploy", "")))
        state = receipt["state"]
        if state == "committed" and receipt.get("value") != "ok":
            state = "refused"
        return {"status": {"committed": "done", "refused": "refused"}.get(state, "started"),
                "receipt": {"id": receipt["id"], "state": state},
                "message": str(receipt.get("value", receipt.get("message", state)))}
    return submit(ctx, action, params)


async def act(ctx, action, items, params):
    try:
        return await asyncio.to_thread(perform, ctx, action, params)
    except (ValueError, ChainError, OSError) as exc:
        return {"status": "refused" if isinstance(exc, ValueError) else "error", "message": str(exc)}
