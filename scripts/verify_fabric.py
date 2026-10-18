#!/usr/bin/env python3
"""
Fabric Verification Script for EVE-NG Topology.
Executes automated connectivity checks against provisioned nodes.
"""
import sys
import subprocess
import socket

# Define your target EVE-NG node management/loopback IPs
NODES = {
    "leaf-01": "192.168.122.10",
    "leaf-02": "192.168.122.11",
    "spine-01": "192.168.122.20",
}

def check_ping(host: str) -> bool:
    """Check ICMP reachability."""
    res = subprocess.run(
        ["ping", "-c", "2", "-W", "2", host],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    return res.returncode == 0

def check_tcp_port(host: str, port: int = 22, timeout: float = 3.0) -> bool:
    """Verify that SSH management daemon is listening."""
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
    print(" RUNNING FABRIC CONNECTIVITY REGRESSION ")
    print("========================================")
    
    all_passed = True
    for name, ip in NODES.items():
        ping_ok = check_ping(ip)
        ssh_ok = check_tcp_port(ip, port=22)
        
        status = "PASS" if (ping_ok and ssh_ok) else "FAIL"
        print(f"[{status}] Node: {name:<10} IP: {ip:<15} Ping: {'OK' if ping_ok else 'TIMEOUT'} SSH: {'LISTENING' if ssh_ok else 'DOWN'}")
        
        if not (ping_ok and ssh_ok):
            all_passed = False

    if not all_passed:
        print("\n[!] Fabric verification failed. Node(s) unreachable.")
        sys.exit(1)
        
    print("\n[✓] All fabric nodes operational and reachable.")
    sys.exit(0)

if __name__ == "__main__":
    main()
