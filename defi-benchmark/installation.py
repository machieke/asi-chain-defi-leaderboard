"""One durable installation per Hive. Only confirmed operator actions submit deploys."""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

from chain_observer import ChainError, ObserverClient
from omega_chain_deploy import Deployer, decode

CONTRACT = Path(__file__).parent / "contracts" / "benchmark.rho"
# Hive's program digest covers Python and the UI lock. Bind the deployed Rholang to that release too.
CONTRACT_SHA256 = "a463e96432f793ab4530efb74b7e6ca5a850ee067b3df3ab27ad8742f18059ce"
PUBLIC = re.compile(r"04[0-9a-f]{128}")
URI = re.compile(r"rho:id:[a-z0-9]{54}")
PHLO_LIMIT = 5_000_000


def directory():
    return Path(os.getenv("HIVE_DATA_DIR") or Path.home() / ".omegadots/hive") / "defi-benchmark"


@contextmanager
def exclusive(root=None):
    root = root or directory()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(root, os.O_DIRECTORY | os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def identity():
    return {"network": os.getenv("CHAIN_NETWORK_ID", "testnet"), "shard": os.getenv("CHAIN_SHARD_ID", "root")}


def read(root=None):
    try:
        state = json.loads(((root or directory()) / "installation.json").read_bytes())
    except FileNotFoundError:
        return {}
    if (not isinstance(state, dict) or state.get("chain") != identity() or
            state.get("phase") not in ("prepared", "pending", "failed", "ready")):
        raise ValueError("The saved installation is invalid or belongs to another chain. Check the Hive data directory.")
    return state


def save(state, root=None):
    root = root or directory()
    with tempfile.NamedTemporaryFile(dir=root, prefix=".installation-", delete=False) as f:
        try:
            f.write(json.dumps(state, separators=(",", ":")).encode())
            f.flush()
            os.fsync(f.fileno())
            os.replace(f.name, root / "installation.json")
            fd = os.open(root, os.O_DIRECTORY | os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            Path(f.name).unlink(missing_ok=True)


def configured_uri(state):
    override = os.getenv("DEFI_BENCHMARK_URI", "")
    if override and state and override != state.get("uri"):
        raise ValueError("The host URI conflicts with this Hive's saved installation.")
    uri = override or state.get("uri", "")
    if uri and not URI.fullmatch(uri):
        raise ValueError("The benchmark registry URI is invalid.")
    return uri


def wallet():
    try:
        from omega_chain_wallet import Wallet
    except ImportError:
        raise ValueError("Install omega-chain-wallet in the Hive host environment to manage signing keys.") from None
    return Wallet.from_env()


def signer_entries():
    file = os.getenv("CHAIN_DEPLOY_KEY_FILE")
    public = os.getenv("CHAIN_DEPLOY_PUBLIC_KEY")
    if file and public:
        raise ValueError("The Hive host has two default signers configured. Select one in its environment.")
    if file:
        return {"host": ""}
    host_wallet = wallet()
    # Wallets from before signer naming still have valid public keys.
    entries = dict(host_wallet.entries()) if hasattr(host_wallet, "entries") else dict.fromkeys(host_wallet.public_keys(), "")
    if public:
        key = public.lower()
        return {key: entries.get(key, "")}
    return entries


def wallet_address(public):
    from Crypto.Hash import keccak

    if not PUBLIC.fullmatch(public):
        raise ValueError("Invalid wallet public key.")
    # ASI Chain Wallet's deriveEthAddress hashes the uncompressed X and Y coordinates.
    digest = keccak.new(digest_bits=256, data=bytes.fromhex(public[2:])).digest()
    return "0x" + digest[-20:].hex()


def account(public):
    if not PUBLIC.fullmatch(public):
        raise ValueError("Invalid wallet public key.")
    # F1R3node's native balance lookup, evaluated only in a finalized read-only snapshot.
    term = f'''new out, addr(`rho:vault:address`), a, lookup(`rho:registry:lookup`), s, v, b in {{
      addr!("fromPublicKey", "{public}".hexToBytes(), *a) | lookup!(`rho:vault:system`, *s) |
      for (@address <- a; @(_, system) <- s) {{
        @system!("findOrCreate", address, *v) | for (@(true, vault) <- v) {{
          @vault!("balance", *b) | for (@balance <- b) {{ out!((address, balance)) }}
        }}
      }}
    }}'''
    expr = ObserverClient.from_env().query(term)["expr"]
    if len(expr) == 1:
        result = decode(expr[0])
        if (isinstance(result, list) and len(result) == 2 and isinstance(result[0], str)
                and re.fullmatch(r"1111[1-9A-HJ-NP-Za-km-z]+", result[0]) and type(result[1]) is int):
            return {"address": result[0], "balance": str(result[1])}
    raise ValueError("The finalized observer could not read this signer's native balance.")


def reconcile(root=None):
    state = read(root)
    if configured_uri(state) or state.get("phase") not in ("pending", "prepared"):
        return state
    writer = Deployer()
    writer.writer_status()
    receipt = writer.receipt(state["id"])
    phase = receipt["state"]
    if phase == "pending":
        return state
    if phase == "committed" and isinstance(receipt.get("value"), str) and URI.fullmatch(receipt["value"]):
        update = {"phase": "ready", "uri": receipt["value"]}
    elif phase == "refused":
        update = {"phase": "failed", "message": receipt.get("message", "The deploy was refused.")}
    else:
        # A finalized but unreadable result might already have created a contract.
        raise ValueError(f"Verify installation receipt {state['id']}. The node did not return a registry URI.")
    with exclusive(root):
        latest = read(root)
        if latest.get("id") == state["id"] and latest["phase"] in ("pending", "prepared"):
            latest.update(update)
            latest.pop("signed", None)
            save(latest, root)
        return latest


def operator(identity, *, viewing=False):
    kind = "operator" if viewing else "person"
    return (isinstance(identity, dict) and identity.get("kind") == kind and
            identity.get("local_operator") is True and identity.get("route") == "operator")


def panel(ctx, state, requested=None):
    phase = state.get("phase", "new")
    info = {**identity(), "phase": phase, "operator": operator(getattr(ctx, "viewer", None), viewing=True),
            "id": state.get("id"), "message": state.get("message", "")}
    if not info["operator"] or phase in ("pending", "prepared"):
        return info
    entries = signer_entries()
    selected = requested if requested in entries else next(iter(entries), "")
    info.update(keys=list(entries), selected=selected, signers={
        key: {"name": name, "address": wallet_address(key)} for key, name in entries.items() if key != "host"})
    if selected:
        if selected != "host":
            info.update(account(selected))
        info["max_fee"] = str(PHLO_LIMIT * int(Deployer().writer_status()["minPhloPrice"]))
    return info


def create_signer():
    with exclusive():
        state = read()
        if configured_uri(state) or state.get("phase") in ("prepared", "pending"):
            raise ValueError("This Hive already has a benchmark installation.")
        if not signer_entries():
            host_wallet = wallet()
            host_wallet.initialize()
            host_wallet.generate()
    return {"status": "done", "message": "The host signing wallet is ready."}


def install(params, *, replay=False, root=None):
    with exclusive(root):
        state = read(root)
        if configured_uri(state):
            return {"status": "done", "message": "This benchmark is already configured."}
        phase = state.get("phase")
        writer = Deployer()
        if phase in ("pending", "prepared"):
            if not replay or phase != "prepared":
                return {"status": "started", "message": "Already submitted or awaiting recovery. Check progress.",
                        "receipt": {"id": state["id"], "state": "pending" if phase == "pending" else "unknown"}}
            # Re-send exactly the journaled transaction after a crash or lost reply.
            writer.writer_status()
            signed = state["signed"]
        else:
            if replay:
                raise ValueError("There is no signed deployment to resume.")
            selected = params.get("signer")
            if selected not in signer_entries():
                raise ValueError("Select a signer from the host wallet.")
            source = CONTRACT.read_bytes()
            if hashlib.sha256(source).hexdigest() != CONTRACT_SHA256:
                raise ValueError("The bundled Rholang contract differs from the reviewed release.")
            signed = writer.prepare(source.decode(), wallet_public_key=None if selected == "host" else selected)
            public = signed["deployer"]
            funds = account(public)
            fee = signed["data"]["phloLimit"] * int(signed["data"]["phloPrice"])
            if str(fee) != params.get("max_fee"):
                raise ValueError("The maximum fee changed. Refresh before confirming deployment.")
            if int(funds["balance"]) < fee:
                raise ValueError(f"Fund {funds['address']} with at least {fee} native base units in total, then refresh.")
            state = {"phase": "prepared", "chain": identity(), "id": signed["signature"], "signed": signed,
                     "signer": public, "wallet": selected != "host", "source": CONTRACT_SHA256}
            save(state, root)
        try:
            writer.submit(signed)
        except (ChainError, OSError) as exc:
            if not replay and isinstance(exc, ChainError) and exc.http_status in (400, 403, 422):
                state.update(phase="failed", message=str(exc))
                state.pop("signed", None)
                save(state, root)
                return {"status": "refused", "message": str(exc), "receipt": {"id": state["id"], "state": "refused"}}
            # Even a refused repeat can be a duplicate of a delivered deploy. Keep its identity.
            return {"status": "started", "message": f"Check the saved deploy before retrying submission. {exc}",
                    "receipt": {"id": state["id"], "state": "unknown"}}
        state["phase"] = "pending"
        state.pop("signed", None)
        save(state, root)
        return {"status": "started", "message": "Deployment submitted. Waiting for finalization.",
                "receipt": {"id": state["id"], "state": "pending"}}
