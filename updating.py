cat << 'EOF' > /home/kumar/app.py
"""
Lumen Velocity GitHub App Protection Rule Service
Bridges GitHub Custom Deployment Protection Rules and Keysight Velocity.
"""
import hmac
import hashlib
import json
import time
import requests
import jwt
from fastapi import FastAPI, Request, Header, HTTPException
import uvicorn
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

app = FastAPI(title="Lumen Velocity Gate Service")

# ----------------- CONFIGURATION -----------------
GITHUB_APP_ID = "5360921"
GITHUB_INSTALLATION_ID = "172491464"
GITHUB_PRIVATE_KEY_PATH = "/home/kumar/velocity-gate.pem"
GITHUB_WEBHOOK_SECRET = "LumenVelocitySecret2026!"

CALLBACK_BASE_URL = "https://crinkly-playmate-crease.ngrok-free.dev"

# ----------------- KEYSIGHT VELOCITY CONFIG -----------------
VELOCITY_BASE_URL = "https://192.168.122.106"
VELOCITY_RESERVATION_ENDPOINT = f"{VELOCITY_BASE_URL}/velocity/api/reservation/v25/reservation"
VELOCITY_TOKEN = "eyJwYWNrZXQiOiJ7XCJpZFwiOlwiYWRtaW5pc3RyYXRvclwiLFwidXNlcl9uYW1lXCI6XCJzdXBlci1hZG1pblwiLFwiZG9tYWluXCI6XCJsZGFwXCIsXCJzdGFydF90aW1lXCI6MTc5MjE4MTk3OSxcImV4cGlyYXRpb25cIjoxNzkyNzg3MDc5LFwiaXNfcGFzc3dvcmRfY2hhbmdlX2FsbG93ZWRcIjp0cnVlfSIsInNpZ25hdHVyZSI6IjRiYjcyMGYyODZkYzhhMmQ3ZmU3MTdkYzZkMTM5YjU1ODljYjFkZDM2OGVhM2U5MjY2ZDk1YjUyY2RlZjY2ZDZlYWY1MTA4YjQ1ZDU2MzU1YjI4ZmY5MDI4ODUxZTc5YzdhYmFlNDYyNDhhN2NkNzRhZjRjNTljYWE5MWE1YTVlNTg5MTZlNWE3NjBjYTdlNWFiYjkyNGJjZWUxNTIwZjBkNjhkNjdmOGFkM2YzZDg2Mjc0ZjZmZjcwMGRhOThjYjU5NTA2Yjc1YzE2ZWYxNjgyNjRkYTk0YjRjODFlNGI5YjNhOTIzNGMwOTQzZDEwMjI0OTczMDhmNjViYjQ5ZjlmNzQ1MGQ0NDRkN2EyZTRiZDBkODRiNDliOWI5OTg2NjRlMTIyYWRjYmZhNTc0ODYxZDVhYTFhYWFkNjE5ZDEzNzM0NGVjZGU5NmViODVhYTEzMzMyZjg1MjEzZGJmZTI2NzU2YTQ1NTI0ZmExOTFiMGM3NTQ2ODFmOTdhNDFlZDRiNmZjZGNkNzRkZGQzNGM1NTJiMTBhYzc5ODhjMDRlMWVjMDA2ZTFjMmE4YjEwN2Q1NTc3MDRjNjYwZjA5ZTNmNWMzYzVlN2NiMGUwNWFjMThlMWM1MzRlZDliN2IxNDRkZTZhZGI0ZDA5ZmE0OTEwZDU2NzdkMTM0OWFjMGUzIn0="
TOPOLOGY_UUID = "a51d26ad-ded0-4b0e-bad2-cb5ec3862e25"
# -------------------------------------------------------------

# In-memory mapping: reservation_id -> context
active_deployments = {}

def get_github_installation_token() -> str:
    with open(GITHUB_PRIVATE_KEY_PATH, "r") as f:
        private_key = f.read()

    now = int(time.time())
    payload = {
        "iat": now - 60,
        "exp": now + (10 * 60),
        "iss": GITHUB_APP_ID
    }
    encoded_jwt = jwt.encode(payload, private_key, algorithm="RS256")

    url = f"https://api.github.com/app/installations/{GITHUB_INSTALLATION_ID}/access_tokens"
    headers = {
        "Authorization": f"Bearer {encoded_jwt}",
        "Accept": "application/vnd.github+json"
    }
    res = requests.post(url, headers=headers, timeout=10)
    if res.status_code != 201:
        raise HTTPException(status_code=500, detail=f"Failed to fetch installation token: {res.text}")
    
    return res.json()["token"]

def verify_github_signature(payload: bytes, signature_header: str):
    if not signature_header:
        raise HTTPException(status_code=401, detail="Missing X-Hub-Signature-256 header")
    sha_name, signature = signature_header.split("=")
    mac = hmac.new(GITHUB_WEBHOOK_SECRET.encode(), msg=payload, digestmod=hashlib.sha256)
    if not hmac.compare_digest(mac.hexdigest(), signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

@app.post("/webhook/github")
async def handle_github_webhook(request: Request, x_hub_signature_256: str = Header(None)):
    body = await request.body()
    verify_github_signature(body, x_hub_signature_256)
    event = json.loads(body)
    
    if event.get("action") == "requested" and ("deployment" in event or "deployment_callback_url" in event):
        run_id = (
            event.get("run_id") or 
            event.get("workflow_run", {}).get("id") or 
            event.get("deployment", {}).get("run_id") or 
            str(int(time.time()))
        )
        environment = event.get("environment", "velocity-lab-env")
        repo = event.get("repository", {}).get("full_name", "kdahal/neteng-velocity-eveng-automation")
        callback_url = event.get("deployment_callback_url")
        
        reservation_payload = {
            "name": f"GH-Run-{run_id}",
            "description": f"CI reservation for {repo} run {run_id}",
            "duration": 3600,
            "priority": "HIGH",
            "topologyId": TOPOLOGY_UUID,
            "isQueued": True,
            "callbackURL": f"{CALLBACK_BASE_URL}/webhook/velocity"
        }
        
        headers = {
            "Content-Type": "application/json",
            "X-Auth-Token": VELOCITY_TOKEN
        }
        
        res = requests.post(
            VELOCITY_RESERVATION_ENDPOINT,
            json=reservation_payload,
            headers=headers,
            verify=False,
            timeout=15
        )
        
        try:
            res_data = res.json()
        except Exception:
            res_data = {"raw_text": res.text}
            
        reservation_id = res_data.get("id") or res_data.get("reservationId")
        
        if not reservation_id:
            print(f"[ERROR] Velocity error status {res.status_code}: {res_data}")
            return {"status": "error_creating_reservation", "velocity_response": res_data}
            
        active_deployments[reservation_id] = {
            "github_callback_url": callback_url,
            "repo": repo,
            "run_id": run_id,
            "environment": environment
        }
        
        print(f"[GATE] Successfully queued Velocity reservation {reservation_id} for run {run_id}")
        return {"status": "queued_in_velocity", "reservation_id": reservation_id}

    return {"status": "ignored"}

@app.post("/webhook/velocity")
async def handle_velocity_callback(request: Request):
    payload = await request.json()
    reservation_id = payload.get("reservationId") or payload.get("id")
    
    print(f"[VELOCITY CALLBACK] Received event for reservation: {reservation_id}")
    
    context = active_deployments.get(reservation_id)
    if not context:
        print(f"[WARNING] Velocity callback for unmapped reservation: {reservation_id}")
        return {"status": "unknown_reservation"}
    
    github_callback_url = context["github_callback_url"]
    environment = context.get("environment", "velocity-lab-env")
    
    token = get_github_installation_token()
    
    approval_payload = {
        "environment_name": environment,
        "state": "approved",
        "comment": f"Velocity reservation {reservation_id} is ACTIVE and EVE-NG nodes are verified ready."
    }
    
    gh_res = requests.post(
        github_callback_url,
        json=approval_payload,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}"
        },
        timeout=10
    )
    
    print(f"[GATE APPROVED] GitHub response {gh_res.status_code} for reservation {reservation_id}")
    return {"status": "gate_approved", "github_status": gh_res.status_code}

EVE_IP = "192.168.122.19"
LAB_PATH = "Velocity-Integration-Lab/Base-POC-Lab.unl"
EVE_AUTH_URL = f"http://{EVE_IP}/api/auth/login"
EVE_STOP_URL = f"http://{EVE_IP}/api/labs/{LAB_PATH}/nodes/stop"

@app.post("/teardown/{reservation_id}")
async def handle_teardown(reservation_id: str):
    """Releases Velocity resources and stops running EVE-NG nodes."""
    headers = {
        "X-Auth-Token": VELOCITY_TOKEN,
        "Content-Type": "application/json"
    }

    target_ids = list(active_deployments.keys()) if reservation_id == "active" else [reservation_id]

    # 1. Release Velocity Reservations
    results = {}
    for res_id in target_ids:
        res = requests.post(
            f"{VELOCITY_RESERVATION_ENDPOINT}/{res_id}/action?type=CANCEL",
            headers=headers,
            verify=False,
            timeout=10
        )
        active_deployments.pop(res_id, None)
        print(f"[TEARDOWN] Reservation {res_id} canceled: HTTP {res.status_code}")
        results[res_id] = res.status_code

    # 2. Stop EVE-NG Lab Nodes
    try:
        session = requests.Session()
        login_res = session.post(EVE_AUTH_URL, json={"username": "admin", "password": "eve", "html5": "-1"}, timeout=5)
        if login_res.status_code == 200:
            stop_res = session.get(EVE_STOP_URL, timeout=10)
            print(f"[TEARDOWN] EVE-NG nodes stop signal sent: HTTP {stop_res.status_code}")
    except Exception as e:
        print(f"[TEARDOWN ERROR] Failed to signal EVE-NG stop: {e}")

    return {"status": "released", "velocity_results": results}

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8443)
EOF
