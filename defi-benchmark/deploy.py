"""Install the benchmark once, or recover its registry URI from a deploy ID."""
import argparse
import os
from pathlib import Path
import time

from omega_chain_deploy import Deployer
from chain_observer import ObserverClient
import installation


def next_round_upgrade():
    if os.environ.get("DEFI_BENCHMARK_URI"):
        raise SystemExit("Choose the Hive-managed installation for an upgrade (unset DEFI_BENCHMARK_URI).")
    active = installation.read()
    if active.get("phase") != "ready":
        raise SystemExit("Install the benchmark in the Hive first.")
    source = installation.CONTRACT_SHA256
    if active.get("source") == source or any(m["source"] == source for m in active.get("upgrades", [])):
        print("This contract release is already installed.")
        return
    stage = installation.directory() / "releases" / source
    prior = installation.reconcile(stage)
    if prior.get("phase") != "ready":
        signer = active["signer"] if active["wallet"] else "host"
        fee = installation.PHLO_LIMIT * int(Deployer().writer_status()["minPhloPrice"])
        result = installation.install({"signer": signer, "max_fee": str(fee)}, root=stage,
                                      replay=prior.get("phase") == "prepared")
        if result["status"] not in ("started", "done"):
            raise SystemExit(result["message"])
        print("Installation deploy", installation.read(stage)["id"], flush=True)
    deadline = time.monotonic() + 180
    while True:
        new = installation.reconcile(stage)
        if new["phase"] == "ready":
            break
        if new["phase"] == "failed" or time.monotonic() >= deadline:
            raise SystemExit(f"Deployment {new['id']} is {new['phase']}. Rerun --next-round to resume.")
        time.sleep(2)
    if new["signer"] != active["signer"]:
        raise SystemExit("The new release must use the existing competition signer.")
    with installation.exclusive():
        state = installation.read()
        if state.get("uri") != active["uri"] or state.get("signer") != new["signer"]:
            raise SystemExit("The active competition changed during deployment.")
        markets = state.setdefault("upgrades", [])
        if any(m["source"] == source for m in markets):
            return
        block = ObserverClient.from_env().finalized_block()
        hour = max(int(block["timestamp"]) // 1000, int(time.time())) // 3600 + 1
        if markets and markets[-1]["from_round"] >= hour:
            raise SystemExit(f"Another release is already scheduled for round {hour}.")
        markets.append({"from_round": hour, "uri": new["uri"], "id": new["id"], "source": source})
        installation.save(state)
    print(f"Round {hour} and later use {new['uri']}. Previous rounds keep their original contract.")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--resume", metavar="DEPLOY_ID", help="follow an existing installation")
    mode.add_argument("--next-round", action="store_true", help="upgrade a Hive-managed competition at the next UTC hour")
    args = parser.parse_args()
    if args.next_round:
        return next_round_upgrade()
    client = Deployer()
    deploy_id = args.resume
    if not deploy_id:
        signed = client.prepare((Path(__file__).parent / "contracts/benchmark.rho").read_text())
        deploy_id = signed["signature"]
        print(f"Installation deploy {deploy_id}", flush=True)
        client.submit(signed)
    deadline = time.monotonic() + 180
    while time.monotonic() < deadline:
        receipt = client.receipt(deploy_id)
        if receipt["state"] == "committed":
            uri = receipt["value"]
            if not isinstance(uri, str) or not uri.startswith("rho:id:"):
                raise SystemExit("Deploy did not return a registry URI")
            print(f"export DEFI_BENCHMARK_URI={uri}")
            return
        if receipt["state"] != "pending":
            raise SystemExit(receipt["message"])
        time.sleep(2)
    raise SystemExit(f"Still pending. Resume with deploy.py --resume {deploy_id}")


if __name__ == "__main__":
    main()
