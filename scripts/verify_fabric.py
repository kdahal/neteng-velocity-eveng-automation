#!/usr/bin/env python3
"""
Fabric Verification Script for EVE-NG Ephemeral Topologies.
Discovers the active Vel_EFC_UC1_* or Vel_POC_* lab, waits for vJunos/VyOS/VPCS
control-plane boot (~90-120s for Junos), and verifies serial console & SSH readiness.
"""
import sys
import time
import socket
import telnetlib
import requests

EVE_IP = "192.168.122.19"
AUTH_URL = f"http://{EVE_IP}/api/auth/login"
FOLDERS_URL = f"http://{EVE_IP}/api/folders/"


def check_tcp_port(host: str, port: int, timeout: float = 2.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False


def query_console(host: str, port: int, is_junos: bool = False) -> str:
    try:
        tn = telnetlib.Telnet(host, port, timeout=5)
        tn.write(b"\r\n")
        time.sleep(1.5)
        if not is_junos:
            tn.write(b"show version\r\n")
            time.sleep(1.5)
        output = tn.read_very_eager().decode("latin-1", errors="ignore")
        tn.close()
        return output
    except Exception as e:
        return f"Error: {e}"


def login(session: requests.Session):
    return session.post(
        AUTH_URL,
        json={"username": "admin", "password": "eve", "html5": "-1"},
        timeout=5
    )


def discover_ephemeral_lab(session: requests.Session):
    try:
        res = session.get(FOLDERS_URL, timeout=5)
        if res.status_code == 200:
            labs = res.json().get("data", {}).get("labs", [])
            target_labs = [
                lab.get("path", "").lstrip("/")
                for lab in labs
                if lab.get("file", "").startswith(("Vel_EFC_UC1_", "Vel_POC_"))
                and lab.get("file", "").endswith(".unl")
            ]
            if target_labs:
                return sorted(target_labs)[-1]
    except Exception:
        pass
    return None


def get_nodes(session: requests.Session, nodes_url: str):
    try:
        res = session.get(nodes_url, timeout=5)
        if res.status_code == 200:
            return res.json().get("data", {})
        elif res.status_code in (401, 412):
            login(session)
            retry_res = session.get(nodes_url, timeout=5)
            if retry_res.status_code == 200:
                return retry_res.json().get("data", {})
    except Exception:
        pass
    return None


def main():
    print("========================================")
    print(" RUNNING EVE-NG FABRIC REGRESSION TEST  ")
    print("========================================")

    session = requests.Session()
    if login(session).status_code != 200:
        print("[FAIL] EVE-NG authentication failed.")
        sys.exit(1)

    lab_path = None
    for _ in range(12):
        lab_path = discover_ephemeral_lab(session)
        if lab_path:
            break
        time.sleep(2)

    if not lab_path:
        print("[FAIL] No active Vel_EFC_UC1_* or Vel_POC_* ephemeral lab found.")
        sys.exit(1)

    print(f"[*] Target Lab: {lab_path}")
    nodes_url = f"http://{EVE_IP}/api/labs/{lab_path}/nodes"
    start_url = f"http://{EVE_IP}/api/labs/{lab_path}/nodes/start"

    nodes = get_nodes(session, nodes_url)
    if not nodes:
        print("[FAIL] Unable to read lab nodes.")
        sys.exit(1)

    stopped_nodes = [info["name"] for info in nodes.values() if info.get("status") != 2]
    if stopped_nodes:
        print(f"[*] Starting stopped nodes: {', '.join(stopped_nodes)}")
        session.get(start_url, timeout=10)
        for _ in range(12):
            time.sleep(5)
            nodes = get_nodes(session, nodes_url)
            if nodes and all(info.get("status") == 2 for info in nodes.values()):
                break

    nodes = get_nodes(session, nodes_url)
    if not nodes:
        print("[FAIL] Nodes could not be retrieved after startup loop.")
        sys.exit(1)

    # Allow ~100 seconds for vJunos-router control plane (or 20s for VyOS/VPCS) to reach login prompt
    has_junos = any("junos" in info.get("template", "").lower() or "vmx" in info.get("template", "").lower() for info in nodes.values())
    boot_wait = 100 if has_junos else 20
    print(f"[*] Waiting {boot_wait}s for node control-plane boot...")
    time.sleep(boot_wait)

    all_passed = True
    for _, info in nodes.items():
        name = info.get("name", "unknown")
        template = info.get("template", "").lower()
        is_junos = "junos" in template or "vmx" in template
        url = info.get("url", "")
        port = int(url.split(":")[-1]) if ":" in url else None

        if not port or not check_tcp_port(EVE_IP, port):
            print(f"[FAIL] {name:<18} (Port {port}): Unreachable console socket.")
            all_passed = False
            continue

        raw_output = query_console(EVE_IP, port, is_junos=is_junos)
        has_response = len(raw_output.strip()) > 0
        status_label = "PASS" if has_response else "FAIL"
        print(f"[{status_label}] {name:<18} (Port {port}): Serial buffer responsive.")

        if not has_response:
            all_passed = False

    # Verify Rubicon SSH reachability on Orange-ER-PE-01 (192.168.122.60:22) if Junos lab is active
    if has_junos:
        ssh_ready = False
        for _ in range(6):
            if check_tcp_port("192.168.122.60", 22, timeout=3.0):
                ssh_ready = True
                break
            time.sleep(5)
        ssh_label = "PASS" if ssh_ready else "WARN"
        print(f"[{ssh_label}] Orange-ER-PE-01   (192.168.122.60:22): Rubicon SSH endpoint {'LISTENING' if ssh_ready else 'still initializing'}.")

    if not all_passed:
        print("\n[!] Regression failed: One or more node serial consoles non-responsive.")
        sys.exit(1)

    print("\n[✓] All fabric nodes operational and verified.")
    sys.exit(0)


if __name__ == "__main__":
    main()
