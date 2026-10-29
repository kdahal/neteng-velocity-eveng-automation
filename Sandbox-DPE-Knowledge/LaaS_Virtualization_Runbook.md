# Runbook: Lab-as-a-Service (LaaS) Virtualization & EVE-NG Pro Topologies

* **Status:** Active  
* **Last Updated:** 2026-04-02  
* **Maintainers:** Nick Brown, Jason Nault  
* **Infrastructure:** On-prem Bare Metal & Cloud-hosted EVE-NG Pro Clusters  

---

## 1. Node Capacity & Dynamic Allocation Rules
* **Standard Test Topology:** Dynamically allocates between **50 and 75 virtual nodes** per tenant allocation block.
* **Allocation Caps:** Topologies requesting >75 nodes require architectural approval from Nick Brown or Jason Nault.
* **Resource Scaling:** Dynamic CPU/RAM overcommit is limited to **2.5:1** on physical hypervisors.

---

## 2. Automated Teardown Webhook Workflow
To release lab hypervisor capacity automatically, all active topologies follow a strict **3-tier automated teardown workflow**:
[ Inactivity / Lease Expiry ] ──► Tier 1: Warn (Slack/Teams Webhook)
│
▼
[ +30 Mins Grace Period ]    ──► Tier 2: State Snapshot & Config Dump
│
▼
[ Immediate Post-Dump ]      ──► Tier 3: Hard Destroy Webhook

1. **Tier 1 — Pre-Expiry Inactivity Notification:**
   * At 30 minutes prior to scheduled topology lease expiry (or after 4 hours of zero interface traffic), a webhook emits an alert to the user's Teams/Slack channel with a 1-click renewal link.
2. **Tier 2 — Configuration Capture & Snapshot:**
   * If unacknowledged, a teardown job runs `config-pull-webhook` to persist runtime configs and diffs into the target Git branch.
3. **Tier 3 — Hypervisor Resource Reclaim:**
   * An orchestrated `teardown-webhook` issues an ACPI shutdown, purges volatile disk images, releases reserved management IP blocks back to IPAM, and decrements the active node pool count.