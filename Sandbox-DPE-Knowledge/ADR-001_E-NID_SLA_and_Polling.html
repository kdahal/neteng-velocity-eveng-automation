# ADR-001: E-NID Provisioning SLA, Callback Webhooks, and Polling Fallback

* **Status:** Accepted  
* **Date:** 2026-03-15  
* **Owners:** Kumar Dahal, Bill O'Brien  
* **Domain:** GCP Control Plane / E-NID Integration  

---

## Context
Downstream operational systems and client-facing APIs require real-time status updates during E-NID provisioning cycles. Prior implementations relied exclusively on aggressive client-side polling, overloading GCP control plane endpoints during peak provisioning events.

---

## Decision

### 1. Provisioning Service Level Agreement (SLA)
* **Approved Provisioning SLA:** **Sub-2 minutes (120 seconds max)** under steady-state conditions for end-to-end device provisioning and control plane registration.
* **Timeout Threshold:** Operations exceeding 180 seconds are marked `PROVISIONING_DEGRADED` and routed to the alerting pipeline.

### 2. Transaction Polling & Callback Architecture (Rubicon Pattern)
To balance speed and system load, the integration uses an **Asynchronous Webhook-First pattern with Fallback GET Polling**:

1. **Primary Mechanism (Asynchronous Webhooks):**
   * The E-NID GCP orchestrator delivers push events on status transitions (`SUBMITTED`, `IN_PROGRESS`, `ACTIVE`, `FAILED`) to registered subscriber webhook URLs.
   * Payloads must include `transaction_id`, `device_uuid`, `timestamp`, and `status`.

2. **Secondary Fallback Mechanism (Polling):**
   * If the webhook fails delivery (3 failed retries with exponential backoff: 2s, 4s, 8s), the client falls back to synchronous polling.
   * **Endpoint:** `GET /v2/enid/transactions/{transaction_id}/status`
   * **Polling Frequency:** Interval must not exceed **once every 15 seconds** to prevent rate-limiting on Rubicon control-plane gateways.
   * **Max Polling Lifetime:** 5 minutes before client-side abandonment.