#!/usr/bin/env python3
"""
Fabric Verification Script for EVE-NG Topology.
Validates node state via EVE-NG Pro REST API and checks TCP console ports.
"""
import sys
import socket
import requests

EVE_IP = "192.168.122.19"
LAB_PATH = "Velocity-Integration-Lab/Base-POC-Lab.unl"
AUTH_URL = f"http://{EVE_IP}/api/auth/login"
NODES_URL = f"http://{EVE_IP}/api/labs/{LAB_PATH}/nodes"

def check_tcp_port(host: str, port: int, timeout: float = 3.0) -> bool:
    """Verifies TCP socket connectivity to the node console port."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def main():
    print("========================================")
    print(" RUNNING EVE-NG FABRIC REGRESSION TEST  ")
    print("========================================")

    session = requests.Session()
    login_payload = {"username": "admin", "password": "eve", "html5": "-1"}
    
    try:
        login_res = session.post(AUTH_URL, json=login_payload, timeout=5)
        if login_res.status_code != 200 or login_res.json().get("status") != "success":
            print(f"[FAIL] Unable to authenticate with EVE-NG API at {EVE_IP}")
            sys.exit(1)
    except Exception as e:
        print(f"[FAIL] Connection error to EVE-NG: {e}")
        sys.exit(1)

    nodes_res = session.get(NODES_URL, timeout=5)
    if nodes_res.status_code != 200:
        print(f"[FAIL] Failed to fetch nodes for lab {LAB_PATH}")
        sys.exit(1)

    nodes_data = nodes_res.json().get("data", {})
    if not nodes_data:
        print("[FAIL] No nodes found in the target topology.")
        sys.exit(1)

    all_passed = True
    print(f"Discovered {len(nodes_data)} node(s) in {LAB_PATH}:\n")

    for node_id, info in nodes_data.items():
        name = info.get("name", f"node-{node_id}")
        status = info.get("status", 0)  # 2 = running, 0 = stopped
        url = info.get("url", "")
        
        # Extract port from telnet://192.168.122.19:<port>
        port = None
        if ":" in url:
            try:
                port = int(url.split(":")[-1])
            except ValueError:
                port = None

        is_running = (status == 2)
        console_ok = check_tcp_port(EVE_IP, port) if port else False

        pass_status = is_running and console_ok
        status_label = "PASS" if pass_status else "FAIL"
        print(f"[{status_label}] Node: {name:<12} (ID: {node_id}) | Running: {'YES' if is_running else 'NO'} | Console: {EVE_IP}:{port or 'N/A'} {'UP' if console_ok else 'DOWN'}")

        if not pass_status:
            all_passed = False

    if not all_passed:
        print("\n[!] Regression failed: Nodes are not running or console ports unreachable.")
        sys.exit(1)

    print("\n[✓] All fabric nodes running with responsive console ports.")
    sys.exit(0)

if __name__ == "__main__":
    main()
