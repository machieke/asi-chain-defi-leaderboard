"""OmegaHive program contract 0.1. Finalized chain state is the scoring ledger."""
import asyncio
import hashlib
import os
import re

from chain_observer import ChainError, ObserverClient
from omega_chain_deploy import Deployer, decode

HOUR = 3600
PAGE_SIZE = 20
STAKE_FIELDS = {"asset": "STBL or LOT", "amount": "Positive token amount"}
ACTIONS = {
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
}


def describe():
    actions = []
    for key, (label, fields) in ACTIONS.items():
        reading = key in ("standings", "receipt")
        params = fields if reading else {"round": "UTC hour number from standings", **fields}
        actions.append({"id": key, "label": label, "applies_to": [], "params": params,
                        "agents": True, **({"effect": "read"} if reading else {})})
    return {"contract": "0.1", "actions": actions,
            "kinds": [{"id": "round", "label": "Benchmark round", "role": "other"},
                      {"id": "mind", "label": "Mind standing", "role": "assessment"},
                      {"id": "auction", "label": "Dutch auction", "role": "other"}],
            "relations": []}


def contract_uri():
    value = os.getenv("DEFI_BENCHMARK_URI", "")
    if not re.fullmatch(r"rho:id:[a-z0-9]{54}", value):
        raise ValueError("Configure DEFI_BENCHMARK_URI with the installed contract URI.")
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
    uri = contract_uri()
    observer = ObserverClient.from_env()
    block = observer.finalized_block()
    ts = int(block["timestamp"]) // 1000
    current = ts // HOUR
    hour = current if requested is None else integer(requested, "Round", 0, current)
    term = f'''new out, lookup(`rho:registry:lookup`), ch in {{
      lookup!(`{uri}`, *ch) | for (api <- ch) {{ api!("snapshot", {hour}, *out) }}
    }}'''
    result = observer.query(term, block_hash=block["blockHash"])
    if len(result["expr"]) != 1:
        raise ValueError("The benchmark contract is unavailable at the finalized block.")
    return hour, current, ts, block, decode(result["expr"][0])


def standings(requested=None, page=1):
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
                        "gain": units(score - 100000), "lots": p["lots"], "staked_lots": p["lockedLots"]}))
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
                         "remaining": a["quantity"], "price": units(price), "start_price": units(a["high"]),
                         "floor": units(a["low"]), "end": a["end"],
                         "open": bool(a["quantity"] and now < a["end"])})
    pages = max(1, (max(len(players), len(auctions)) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(integer(page, "Page"), pages)
    part = slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE)
    return {"round": hour, "current": current, "end": end, "as_of": ts, "block": block["blockNumber"],
            "hash": block["blockHash"], "count": len(players), "auction_count": len(auctions),
            "page": page, "pages": pages, "players": [p for _, _, p in players[part]], "auctions": auctions[part]}


def view(ctx, focus, stage):
    match = re.fullmatch(r"round:(current|[0-9]+):([0-9]+)", focus or "")
    requested = match[1] if match and match[1] != "current" else None
    try:
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
    if op == "cancel":
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
    uri = contract_uri()
    hour = integer(params.get("round"), "Round", 0, 999999999)
    key = hashlib.sha256(command["idempotency_key"].encode()).hexdigest()
    args = action_args(op, params, actor)
    term = f'''new out(`rho:system:deployId`), auth(`rho:system:deployerId`), lookup(`rho:registry:lookup`), ch in {{
      lookup!(`{uri}`, *ch) | for (api <- ch) {{
        api!("execute", *auth, {hour}, "{actor['id']}", "{key}", "{op}", {args}, *out)
      }}
    }}'''
    writer = Deployer()
    signed = writer.prepare(term)
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
    if action not in ACTIONS:
        raise ValueError("Unknown benchmark action.")
    allowed = set(ACTIONS[action][1]) | {"base_revision"}
    if action not in ("receipt", "standings"):
        allowed.add("round")
    if params.keys() - allowed:
        raise ValueError("Unexpected benchmark parameters.")
    if action == "standings":
        return {"status": "done", "detail": standings(params.get("round"), params.get("page", 1))}
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
