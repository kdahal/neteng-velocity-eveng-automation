#!/usr/bin/env python3
"""
Fabric Verification Script for EVE-NG Topology.
Ensures nodes are booted, verifies state via EVE-NG Pro API,
and checks serial console socket reachability.
"""
import sys
import time
import socket
import requests

EVE_IP = "192.168.122.19"
LAB_PATH = "Velocity-Integration-Lab/Base-POC-Lab.unl"
AUTH_URL = f"http://{EVE_IP}/api/auth/login"
NODES_URL = f"http://{EVE_IP}/api/labs/{LAB_PATH}/nodes"
START_URL = f"http://{EVE_IP}/api/labs/{LAB_PATH}/nodes/start"

def check_tcp_port(host: str, port: int, timeout: float = 2.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        sock.close()
        return True
    except (socket.timeout, ConnectionRefusedError, OSError):
        return False

def get_nodes(session):
    res = session.get(NODES_URL, timeout=5)
    if res.status_code != 200:
        return None
    return res.json().get("data", {})

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

    nodes = get_nodes(session)
    if not nodes:
        print("[FAIL] Failed to retrieve nodes or lab is empty.")
        sys.exit(1)

    # Check if any nodes are stopped (status != 2)
    stopped_nodes = [info["name"] for info in nodes.values() if info.get("status") != 2]
    if stopped_nodes:
        print(f"[*] Detected stopped nodes: {', '.join(stopped_nodes)}. Sending start signal...")
        start_res = session.get(START_URL, timeout=10)
        print(f"[*] EVE-NG start command returned: {start_res.status_code}")

        # Poll up to 60 seconds for all nodes to report status == 2
        print("[*] Waiting for nodes to reach RUNNING state...")
        for attempt in range(12):
            time.sleep(5)
            nodes = get_nodes(session)
            all_running = all(info.get("status") == 2 for info in nodes.values())
            if all_running:
                print(f"[✓] All nodes transitioned to RUNNING after {(attempt + 1) * 5}s.")
                break
        else:
            print("[WARNING] Timed out waiting for all nodes to report running state. Proceeding with checks...")

    # Allow serial buffers and telnet daemons a short grace period to bind
    time.sleep(5)

    all_passed = True
    print(f"\nEvaluating {len(nodes)} node(s) in {LAB_PATH}:\n")

    for node_id, info in nodes.items():
        name = info.get("name", f"node-{node_id}")
        status = info.get("status", 0)  # 2 = running
        url = info.get("url", "")

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
        print("\n[!] Regression failed: Nodes failed to start or console sockets closed.")
        sys.exit(1)

    print("\n[✓] All fabric nodes running with responsive console ports.")
    sys.exit(0)

if __name__ == "__main__":
    main()
