"""Install the JSON roster in a local OmegaHive and register it for the chain's current hour."""

import argparse
import fcntl
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from hive.supervisor.workspace import current_token


ROOT = Path(__file__).resolve().parent
PROGRAM = "defi-benchmark"
ACTIONS = ("standings", "quote", "receipt", "register", "stake", "unstake", "claim", "buy", "sell", "cancel", "fill-demand")


class LocalOnly(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None  # Never forward a Hive bearer or login to another endpoint.


class Hive:
    def __init__(self, port, data_dir, *, operator_login=True):
        self.base = f"http://127.0.0.1:{port}/api/"
        self.http = build_opener(ProxyHandler({}), LocalOnly())
        self.data = data_dir
        self.operator = None
        if operator_login:
            password = (data_dir / "admin-password").read_text().strip()
            self.operator = self.request("auth/login", body={"password": password})["token"]

    def request(self, path, *, token=None, body=None, method=None):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = "Bearer " + token
        data = json.dumps(body).encode() if body is not None else None
        req = Request(self.base + path, data=data, method=method, headers=headers)
        try:
            with self.http.open(req, timeout=45) as response:
                return json.load(response)
        except HTTPError as error:
            raise RuntimeError(f"{req.get_method()} /api/{path}: HTTP {error.code}") from error

    def admin(self, path, body=None, method=None):
        return self.request(path, token=self.operator, body=body, method=method)

    def act(self, mind, action, params, key=None):
        return self.request(f"agent/programs/{PROGRAM}/act", token=mind.token, body={
            "action": action, "items": [], "params": params,
            "idempotency_key": key or str(uuid.uuid4()),
        })


@dataclass
class Mind:
    id: str
    name: str
    token: str = field(repr=False)


def roster():
    spec = json.loads((ROOT / "initial-minds.json").read_text())
    roles = {r["id"]: r for r in spec["agents"]}
    contract = (ROOT / "benchmark.md").read_text().strip()
    for group in spec["example_roster"]:
        role = roles[group["agent_id"]]
        local = (ROOT / role["id"] / "strategy.md").read_text().strip()
        size = group["instances"]
        names = [role["name"] if size == 1 else f"{role['name']} {i}" for i in range(1, size + 1)]
        coalition = group.get("coalition_id")
        for index, name in enumerate(names, 1):
            marker = f"DeFi benchmark roster / {role['id']} / {index}"
            assignment = "Play independently. Compete only in the supplied benchmark."
            if coalition:
                assignment = (f"Your assigned coalition is {coalition}. Members: {', '.join(names)}. "
                              f"Agreement mode: {spec['coalition_rules']['default_mode']}. "
                              "Use only this coalition's private swarm room for coordination.")
            persona = "\n\n".join((marker, f"You are {name}. {assignment}", role["strategy_prompt"],
                                  "Strategy hypothesis: " + role["hypothesis"],
                                  "Main failure mode: " + role["main_failure_mode"],
                                  spec["common_prompt"], contract, local))
            yield coalition, name, marker, persona


def unique(rows, name):
    matches = [r for r in rows if r["name"] == name]
    if len(matches) > 1:
        raise RuntimeError(f"Ambiguous Hive name: {name}")
    return matches[0] if matches else None


def provision(hive):
    groups = {}
    rows = hive.admin("swarms")
    agents = hive.admin("agents")
    rules = {(r["scope"], r["skill"]) for r in hive.admin("policy")}
    minds = []
    for coalition, name, marker, persona in roster():
        if coalition not in groups:
            title = f"DeFi Benchmark · {coalition or 'Solo'}"
            description = f"asi-chain-defi-leaderboard/defi-minds roster ({coalition or 'solo participants'})"
            swarm = unique(rows, title)
            if swarm and swarm.get("description") != description:
                raise RuntimeError(f"Existing swarm {title} belongs to another setup")
            if not swarm:
                swarm = hive.admin("swarms", {"name": title, "description": description, "visibility": "private"})
            groups[coalition] = swarm["id"]
        swarm_id = groups[coalition]
        members = [a for a in agents if a["swarm_id"] == swarm_id]
        claimed = [a for a in members if (a.get("persona") or "").split("\n", 1)[0] == marker]
        if len(claimed) > 1 or (claimed and claimed[0]["name"] != name):
            raise RuntimeError(f"The roster identity for {name} has been duplicated or renamed in the Hive")
        mind = unique(members, name)
        if mind:
            if mind["kind"] != "omega" or mind["persona"].split("\n", 1)[0] != marker:
                raise RuntimeError(f"Existing mind {name} belongs to another setup")
            if mind["persona"] != persona:
                hive.admin("agents/" + mind["id"], {"persona": persona}, method="PATCH")
        else:
            mind = hive.admin("agents", {"kind": "omega", "name": name, "swarm_id": swarm_id, "persona": persona})
        agent_id = mind["id"]
        scope = "agent:" + agent_id
        # Native Omega checks the skill before Hive checks the named app action.
        skills = ["app-act", "app-resume", *(f"program:{PROGRAM}:{action}" for action in ACTIONS)]
        if marker.split(" / ")[1] in ("monopolist", "oligarchy_member", "high_frequency_trader"):
            skills.append(f"program:{PROGRAM}:opening-order")
        if marker == "DeFi benchmark roster / high_frequency_trader / 1":
            skills.append(f"program:{PROGRAM}:maker-order")
        for skill in skills:
            if (scope, skill) not in rules:
                hive.admin("policy", {"scope": scope, "skill": skill, "mode": "allow",
                                      "note": "DeFi benchmark roster participant"})
        # Use the same guarded credential reader as the native supervisor. Keep no second copy.
        minds.append(Mind(agent_id, name, current_token(hive.data / "agents" / agent_id)))
    return minds


def expect(result, status, action):
    if result.get("status") != status:
        raise RuntimeError(f"{action}: {result.get('status')} {result.get('message', '')}")
    return result


def standings(hive, mind, params=None):
    return expect(hive.act(mind, "standings", params or {}), "done", "standings")["detail"]


def players(hive, reader, board):
    rows = list(board["players"])
    for page in range(2, board["pages"] + 1):
        rows.extend(standings(hive, reader, {"round": board["round"], "page": page})["players"])
    return {row["id"]: row for row in rows}


def register(hive, mind, hour):
    result = hive.act(mind, "register", {"round": hour}, f"roster-register-{hour}-{mind.id}")
    deploy = expect(result, "started", f"register {mind.name}")["receipt"]["id"]
    print(f"{mind.name}: submitted {deploy}", flush=True)
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        receipt = hive.act(mind, "receipt", {"deploy": deploy})
        if receipt.get("status") != "started":
            expect(receipt, "done", f"register {mind.name} {deploy}")
            if receipt["receipt"]["state"] != "committed" or receipt["message"] != "ok":
                raise RuntimeError(f"{mind.name}: unexpected registration receipt for {deploy}")
            return
        time.sleep(2)
    raise RuntimeError(f"{mind.name}: {deploy} is still pending; rerun to resume")


def enter_round(hive, minds, initial):
    reader = minds[0]
    hour = initial["round"]
    existing = players(hive, reader, initial)
    for mind in minds:
        if mind.id not in existing:
            try:
                register(hive, mind, hour)
            except RuntimeError:
                # An awake mind may have entered independently while the roster was being processed.
                board = standings(hive, reader, {"round": hour})
                if mind.id not in players(hive, reader, board):
                    raise
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        board = standings(hive, reader)
        if board["round"] != hour:
            return False
        confirmed = players(hive, reader, board)
        if all(m.id in confirmed for m in minds):
            end = datetime.fromtimestamp(board["end"], UTC).isoformat()
            print(f"Round {hour} ends {end}. Finalized block {board['block']}, {board['hash']}")
            for mind in minds:
                row = confirmed[mind.id]
                print(f"{mind.name}  {mind.id}  rank {row['rank']}  {row['cash']} liquid  {row['score']} STBL score")
            return True
        time.sleep(2)
    raise RuntimeError(f"Finalized standings for round {hour} do not yet contain all roster IDs; rerun to resume")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=os.environ.get("HIVE_DATA_DIR"),
                        help="data directory of the running local OmegaHive (or set HIVE_DATA_DIR)")
    parser.add_argument("--port", type=int, default=8700)
    args = parser.parse_args()
    if args.data_dir is None:
        parser.error("supply --data-dir or HIVE_DATA_DIR")
    with (args.data_dir / "defi-minds.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        hive = Hive(args.port, args.data_dir)
        minds = provision(hive)
        while True:
            initial = standings(hive, minds[0])
            try:
                if enter_round(hive, minds, initial):
                    return
            except RuntimeError:
                if standings(hive, minds[0])["round"] == initial["round"]:
                    raise
            print("The finalized chain entered a new hour. Registering the roster together.", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError) as exc:
        sys.exit(str(exc))
