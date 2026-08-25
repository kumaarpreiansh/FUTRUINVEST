# M. Security Architecture

## What is being protected

In descending order of harm if breached:

1. **The ability to move money.** Compromised execution is the catastrophic case,
   which is why execution is gated three ways and disabled on this build.
2. **Investor financial profiles.** Income, expenses, dependants, liabilities,
   goals. More sensitive than portfolio value, and more useful to an attacker.
3. **PAN and KYC data.** Identity theft primitives.
4. **Portfolio holdings.** Wealth signal; targeting data.
5. **The rule repositories.** An attacker who edits a compliance rule disables a
   control everywhere at once.
6. **The audit trail.** Its value is entirely in its integrity.

## Threat model

| Threat | Mitigation |
|---|---|
| Credential stuffing | MFA mandatory, device binding, anomaly detection on login |
| Session hijack | Short-lived tokens, device-bound refresh, re-auth for any consent |
| Insider access to profiles | Least privilege, JIT elevation with approval, every access logged |
| Insider tampering with rules | Rule changes need two-person approval and a signed ruleset version |
| Malicious or wrong rule change | Ruleset versions are content-hashed and immutable once active |
| Audit tampering | Append-only, content-hashed, chained, WORM-replicated |
| Vendor data poisoning | Multi-source hierarchy; disagreement detection; quality gate |
| Model prompt injection | Fact pack is structured data, not free text; numeric verification on output |
| Unauthorised execution | Per-transaction consent, single-use, expiring, bound to scheme/direction/amount |
| PII in logs | Structured errors, opaque IDs, log scrubbing in the pipeline |
| Supply chain | Pinned dependencies, lockfiles, SBOM, signed builds |

## Prompt injection specifically

A published scheme document could in principle contain text designed to
manipulate a model that reads it. Three properties make that ineffective here:

1. The model never sees raw documents. It sees a `FactPack` of typed values with
   sources, assembled by deterministic code.
2. The model cannot produce numbers, so a manipulated output cannot change a
   figure.
3. The model cannot decide anything. Injection could at most alter prose, which
   then fails the language scan or the numeric check and triggers fallback.

The general principle: **an LLM that cannot act cannot be made to act.**

## Identity and access

**Investors:** MFA required; biometric where the device supports it; device
binding with re-verification on new devices; short-lived access tokens;
re-authentication for consent regardless of session age.

**Staff:** SSO with hardware keys. No standing production data access.
Just-in-time elevation, time-boxed, approved by a second person, fully logged.
Production database access requires a ticket reference.

**Services:** mTLS between services, short-lived workload identities, no shared
secrets, no long-lived API keys.

## Cryptography

| Data | At rest | In transit | Notes |
|---|---|---|---|
| PAN, KYC documents | Field-level encryption, separate KMS key | TLS 1.3 | Vault; raw PAN only in identity service |
| Financial profile | Field-level encryption | TLS 1.3 | Key rotation every 90 days |
| Portfolio, holdings | Database-level encryption | TLS 1.3 | |
| Audit trail | Encrypted + content-hash chain | TLS 1.3 | WORM replica |
| Rule repositories | Signed, content-hashed | TLS 1.3 | Signature verified at load |
| Backups | Encrypted, separate key | — | Restore tested quarterly |

Keys in a managed KMS with HSM backing. No key material in code, config, images
or environment variables.

## The execution boundary

The most security-sensitive path, so it carries the most controls:

1. KYC VALIDATED
2. AML screening passed
3. Recommendation exists and is not stale
4. Tax estimate shown and acknowledged
5. Conflict disclosures shown and acknowledged
6. Consent created, bound to scheme + direction + amount, with an expiry
7. Re-authentication at consent time
8. Consent single-use, marked used atomically with the order
9. Idempotency key on the order
10. Transaction limits: per-transaction, daily, and velocity
11. Anomaly detection: unusual amount, unusual scheme, unusual hour, new device
12. Order placed only through regulated rails
13. Confirmation reconciled against the RTA
14. Every step audited

On this build the path stops at step 1 for want of KYC, and at the platform gate
regardless. The controls are built and tested against a disabled path, which is
the right order: enabling execution should be a configuration change against
controls that already exist, not a project that builds them.

## Audit integrity

Append-only. Content-hashed. Superseding rows reference the hash of what they
replace; `verify_chain()` proves every link resolves.

Replicated to write-once storage with an independent retention policy, so an
attacker with full database access still cannot rewrite history unnoticed.

No update or delete endpoint exists on the audit service. Not disabled —
absent.

## Detection and response

| Signal | Response |
|---|---|
| Login anomaly | Step-up auth, notify investor |
| Consent from a new device | Re-auth, cooling-off, notify |
| Bulk profile reads by staff | Alert, auto-revoke elevation |
| Rule change outside change window | Block deployment, page compliance |
| Audit chain break | Sev-1, freeze writes, forensic capture |
| Vendor feed anomaly | Quarantine the feed, fall back to hierarchy |
| Model producing ungrounded numbers | Auto-fallback, alert AI governance |

Incident response: named on-call, documented severity ladder, breach
notification prepared against the DPDP framework and applicable SEBI
cyber-incident reporting obligations. **The breach notification runbook is a
LEGAL REVIEW item** — the reporting timelines and recipients must be confirmed
against the operative requirements for the chosen operating model.

## Assurance

| Activity | Frequency |
|---|---|
| Dependency scanning, SBOM | Every build |
| SAST | Every build |
| DAST | Weekly |
| Penetration test | Annual + on major change |
| Red team | Annual |
| Access review | Quarterly |
| DR restore test | Quarterly |
| Tabletop incident exercise | Half-yearly |

## Resilience

RPO 15 minutes, RTO 4 hours for the platform; RTO 1 hour for anything on the
execution path once it is enabled.

Cross-region replication, quarterly restore tests against a real restore rather
than a backup-completed metric, and a documented degraded mode: planning,
analytics and education continue when fund-data or the model gateway is
unavailable.
