"""Install the benchmark once, or recover its registry URI from a deploy ID."""
import argparse
from pathlib import Path
import time

from omega_chain_deploy import Deployer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--resume", metavar="DEPLOY_ID", help="follow an existing installation")
    args = parser.parse_args()
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
