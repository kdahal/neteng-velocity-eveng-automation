#!/usr/bin/env python3
"""
Dynamic Fabric Verification & Config-Driven Provisioning Script.
Discovers the active ephemeral Vel_POC_*.unl lab provisioned by Velocity,
injects version-controlled startup configs from configs/*.cfg, verifies
operational & configuration state over serial console, and holds open for demo inspection.
"""
import os
import sys
import time
import socket
import telnetlib
import urllib.parse
from pathlib import Path
import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

EVE_IP = "192.168.122.19"
AUTH_URL = f"http://{EVE_IP}/api/auth/login"
FOLDERS_URL = f"http://{EVE_IP}/api/folders/"
CONFIG_DIR = Path(__file__).resolve().parent.parent / "configs"

VELOCITY_HOST = os.environ.get("VELOCITY_HOST", "https://192.168.122.106")
VELOCITY_PRIORITY = os.environ.get("VELOCITY_PRIORITY", "MEDIUM")
DEMO_HOLD_SECONDS = int(os.environ.get("DEMO_HOLD_SECONDS", "30"))


def check_velocity_api() -> bool:
    try:
        health = requests.get(f"{VELOCITY_HOST}/health-status", verify=False, timeout=5)
        if health.status_code != 200 or health.json().get("healthWarning") is not None:
            print(f"[FAIL] Velocity Core health check failed: {health.text}")
            return False
        print(f"[PASS] Velocity Core ({VELOCITY_HOST}) healthy | Priority: {VELOCITY_PRIORITY}")
        return True
    except Exception as e:
        print(f"[FAIL] Velocity Core unreachable: {e}")
        return False


def check_tcp_port(host: str, port: int, timeout: float = 2.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def query_vyos_console(host: str, port: int, command: str = "show interfaces") -> str:
    try:
        tn = telnetlib.Telnet(host, port, timeout=5)
        tn.write(b"\r\n")
        time.sleep(1)
        tn.write(f"{command}\r\n".encode("ascii"))
        time.sleep(2)
        output = tn.read_very_eager().decode("latin-1", errors="ignore")
        tn.close()
        return output
    except Exception as e:
        return f"Error: {e}"


def login(session: requests.Session):
    return session.post(
        AUTH_URL,
        json={"username": "admin", "password": "eve", "html5": "-1"},
        timeout=5,
    )


def discover_ephemeral_lab(session: requests.Session) -> str:
    res = session.get(FOLDERS_URL, timeout=5)
    if res.status_code == 200:
        labs = res.json().get("data", {}).get("labs", [])
        poc_labs = sorted([l["file"] for l in labs if l["file"].startswith("Vel_POC_")])
        if poc_labs:
            return poc_labs[-1]
    return ""


def get_nodes(session: requests.Session, base_lab_url: str):
    try:
        res = session.get(f"{base_lab_url}/nodes", timeout=5)
        if res.status_code == 200:
            return res.json().get("data", {})
        elif res.status_code in (401, 412):
            login(session)
            retry_res = session.get(f"{base_lab_url}/nodes", timeout=5)
            if retry_res.status_code == 200:
                return retry_res.json().get("data", {})
    except Exception:
        pass
    return None


def inject_startup_configs(session: requests.Session, base_lab_url: str, nodes: dict) -> bool:
    if not CONFIG_DIR.exists():
        print(f"[INFO] No config directory found at {CONFIG_DIR}; skipping config injection.")
        return True

    injected_any = False
    for node_id, info in nodes.items():
        name = info.get("name", "")
        cfg_file = CONFIG_DIR / f"{name}.cfg"
        if not cfg_file.exists():
            continue

        if info.get("status") == 2:
            print(f"[*] Stopping {name} (ID {node_id}) to apply Git startup config...")
            session.get(f"{base_lab_url}/nodes/{node_id}/stop", timeout=10)
            time.sleep(2)

        config_text = cfg_file.read_text(encoding="utf-8")
        cfg_res = session.put(
            f"{base_lab_url}/configs/{node_id}",
            json={"data": config_text},
            timeout=10,
        )
        node_res = session.put(
            f"{base_lab_url}/nodes/{node_id}",
            json={"config": 1},
            timeout=10,
        )

        if cfg_res.status_code == 200 and node_res.status_code == 200:
            print(f"[PASS] Injected startup config from {cfg_file.name} -> {name} (ID {node_id})")
            injected_any = True
        else:
            print(f"[FAIL] Config injection failed for {name}: cfg={cfg_res.status_code}, node={node_res.status_code}")
            return False

    if not injected_any:
        print("[INFO] No matching node config files found in configs/; booting with existing state.")
    return True


def main():
    print("========================================")
    print(" RUNNING EVE-NG FABRIC REGRESSION TEST  ")
    print("========================================")

    if not check_velocity_api():
        sys.exit(1)

    session = requests.Session()
    if login(session).status_code != 200:
        print("[FAIL] EVE-NG authentication failed.")
        sys.exit(1)

    lab_file = discover_ephemeral_lab(session)
    if not lab_file:
        print("[FAIL] No active ephemeral Vel_POC_*.unl lab found in EVE-NG.")
        sys.exit(1)

    print(f"[*] Target Lab: {lab_file}")
    encoded_lab = urllib.parse.quote(lab_file)
    base_lab_url = f"http://{EVE_IP}/api/labs/{encoded_lab}"

    nodes = get_nodes(session, base_lab_url)
    if not nodes:
        print("[FAIL] Unable to read lab nodes.")
        sys.exit(1)

    if not inject_startup_configs(session, base_lab_url, nodes):
        sys.exit(1)

    nodes = get_nodes(session, base_lab_url)
    stopped_nodes = [info["name"] for info in nodes.values() if info.get("status") != 2]
    if stopped_nodes:
        print(f"[*] Starting nodes with injected configs: {', '.join(stopped_nodes)}")
        session.get(f"{base_lab_url}/nodes/start", timeout=15)
        for _ in range(12):
            time.sleep(5)
            nodes = get_nodes(session, base_lab_url)
            if nodes and all(info.get("status") == 2 for info in nodes.values()):
                break

    # Wait for VyOS boot & startup-config commit
    print("[*] Waiting 15s for VyOS startup-config initialization...")
    time.sleep(15)

    nodes = get_nodes(session, base_lab_url)
    if not nodes:
        print("[FAIL] Nodes could not be retrieved after startup loop.")
        sys.exit(1)

    all_passed = True
    for node_id, info in nodes.items():
        name = info.get("name")
        url = info.get("url", "")
        port = int(url.split(":")[-1]) if ":" in url else None

        if not port or not check_tcp_port(EVE_IP, port):
            print(f"[FAIL] {name:<12} (Port {port}): Unreachable socket.")
            all_passed = False
            continue

        raw_output = query_vyos_console(EVE_IP, port, "show interfaces")
        has_response = len(raw_output.strip()) > 0
        cfg_file = CONFIG_DIR / f"{name}.cfg"

        if cfg_file.exists():
            config_applied = "10.255.0." in raw_output
            status_label = "PASS" if (has_response and config_applied) else "FAIL"
            detail = (
                "Serial responsive + Git startup config (10.255.0.x) verified."
                if (has_response and config_applied)
                else "Serial responsive, but injected loopback IP missing (VyOS still booting)."
            )
            if not (has_response and config_applied):
                all_passed = False
        else:
            status_label = "PASS" if has_response else "FAIL"
            detail = "Serial buffer responsive."
            if not has_response:
                all_passed = False

        print(f"[{status_label}] {name:<12} (Port {port}): {detail}")

    if not all_passed:
        print("\n[!] Regression failed: Node verification or startup config check failed.")
        sys.exit(1)

    print("\n[✓] All fabric nodes operational and startup configs verified.")

    if DEMO_HOLD_SECONDS > 0:
        print(f"[*] Holding active sandbox open for {DEMO_HOLD_SECONDS}s for live demo inspection...")
        time.sleep(DEMO_HOLD_SECONDS)

    sys.exit(0)


if __name__ == "__main__":
    main()
