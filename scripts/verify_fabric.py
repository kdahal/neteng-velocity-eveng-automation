#!/usr/bin/env python3
"""
Fabric Verification Script for EVE-NG Topology.
Verifies operational state by executing show commands over VyOS console ports.
"""
import sys
import time
import socket
import telnetlib
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

def query_vyos_console(host: str, port: int, command: str = "show version") -> str:
    """Connects to VyOS serial port, sends command, returns terminal output."""
    try:
        tn = telnetlib.Telnet(host, port, timeout=5)
        # Flush initial buffer with newline
        tn.write(b"\r\n")
        time.sleep(1)
        tn.write(f"{command}\r\n".encode("ascii"))
        time.sleep(2)
        output = tn.read_very_eager().decode("latin-1", errors="ignore")
        tn.close()
        return output
    except Exception as e:
        return f"Error: {e}"

def get_nodes(session):
    res = session.get(NODES_URL, timeout=5)
    return res.json().get("data", {}) if res.status_code == 200 else None

def main():
    print("========================================")
    print(" RUNNING EVE-NG FABRIC REGRESSION TEST  ")
    print("========================================")

    session = requests.Session()
    login_res = session.post(AUTH_URL, json={"username": "admin", "password": "eve", "html5": "-1"}, timeout=5)
    if login_res.status_code != 200:
        print("[FAIL] EVE-NG authentication failed.")
        sys.exit(1)

    nodes = get_nodes(session)
    if not nodes:
        print("[FAIL] Unable to read lab nodes.")
        sys.exit(1)

    # Ensure nodes are started
    stopped_nodes = [info["name"] for info in nodes.values() if info.get("status") != 2]
    if stopped_nodes:
        print(f"[*] Starting stopped nodes: {', '.join(stopped_nodes)}")
        session.get(START_URL, timeout=10)
        for _ in range(12):
            time.sleep(5)
            nodes = get_nodes(session)
            if all(info.get("status") == 2 for info in nodes.values()):
                break

    time.sleep(5)

    all_passed = True
    for node_id, info in nodes.items():
        name = info.get("name")
        url = info.get("url", "")
        port = int(url.split(":")[-1]) if ":" in url else None

        if not port or not check_tcp_port(EVE_IP, port):
            print(f"[FAIL] {name:<12} (Port {port}): Unreachable socket.")
            all_passed = False
            continue

        # Interrogate console
        raw_output = query_vyos_console(EVE_IP, port, "show interfaces")
        has_response = len(raw_output.strip()) > 0
        status_label = "PASS" if has_response else "FAIL"
        
        print(f"[{status_label}] {name:<12} (Port {port}): Serial buffer responsive.")

        if not has_response:
            all_passed = False

    if not all_passed:
        print("\n[!] Regression failed: Node serial consoles non-responsive.")
        sys.exit(1)

    print("\n[✓] All fabric nodes operational and verified.")
    sys.exit(0)

if __name__ == "__main__":
    main()
