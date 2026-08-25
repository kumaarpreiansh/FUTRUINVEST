# K. Database Schema

PostgreSQL. Money as `NUMERIC(18,4)`, never floating point. Every table
timestamped; nothing investor-facing is hard-deleted while a record-keeping
obligation stands.

## Design rules

1. **Versioned, not mutated.** Profiles, rules and recommendations are
   append-only with version numbers. A history you can edit is not a history.
2. **Snapshots, not references.** An audit row embeds the data as it was, so it
   replays correctly.
3. **Money is `NUMERIC`.** Floats are permitted only for statistics.
4. **Every regulatory or tax value carries its `rule_id` and ruleset version.**

## Identity and profile

```sql
CREATE TABLE investor (
    investor_id      UUID PRIMARY KEY,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    status           TEXT NOT NULL,          -- ACTIVE | DORMANT | CLOSED
    kyc_status       TEXT NOT NULL,          -- NOT_STARTED | IN_PROGRESS | VALIDATED | REJECTED
    kyc_validated_at TIMESTAMPTZ,
    kyc_kra          TEXT,
    pan_hash         BYTEA NOT NULL,         -- hashed; raw PAN in the vault only
    risk_category    TEXT,                   -- AML risk categorisation
    closed_at        TIMESTAMPTZ
);

-- Append-only. A change writes a new row; the old one is never updated.
CREATE TABLE investor_profile (
    profile_id       UUID PRIMARY KEY,
    investor_id      UUID NOT NULL REFERENCES investor,
    version          INTEGER NOT NULL,
    fingerprint      TEXT NOT NULL,          -- SHA-256 of the profile content
    tier1            JSONB NOT NULL,
    tier2            JSONB,
    tier3            JSONB,
    completeness     JSONB NOT NULL,         -- % per tier
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by       TEXT NOT NULL,          -- INVESTOR | ADVISER | SYSTEM
    supersedes       UUID REFERENCES investor_profile,
    UNIQUE (investor_id, version)
);

CREATE TABLE consent_artefact (
    consent_id       UUID PRIMARY KEY,
    investor_id      UUID NOT NULL REFERENCES investor,
    purpose          TEXT NOT NULL,          -- DPDP purpose
    scope            JSONB NOT NULL,
    granted_at       TIMESTAMPTZ NOT NULL,
    withdrawn_at     TIMESTAMPTZ,
    method           TEXT NOT NULL,
    evidence_ref     TEXT NOT NULL,          -- immutable store pointer
    policy_version   TEXT NOT NULL
);
```

## Goals and risk

```sql
CREATE TABLE goal (
    goal_id              UUID PRIMARY KEY,
    investor_id          UUID NOT NULL REFERENCES investor,
    name                 TEXT NOT NULL,
    goal_type            TEXT NOT NULL,
    target_amount_today  NUMERIC(18,4) NOT NULL,
    target_date          DATE NOT NULL,
    current_corpus       NUMERIC(18,4) NOT NULL DEFAULT 0,
    monthly_contribution NUMERIC(18,4) NOT NULL DEFAULT 0,
    annual_step_up_pct   NUMERIC(6,3) NOT NULL DEFAULT 0,
    inflation_pct        NUMERIC(6,3),
    priority             TEXT NOT NULL,
    flexible_amount      BOOLEAN NOT NULL DEFAULT FALSE,
    flexible_date        BOOLEAN NOT NULL DEFAULT FALSE,
    status               TEXT NOT NULL,      -- ACTIVE | ACHIEVED | ABANDONED | PAUSED
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE goal_projection (
    projection_id        UUID PRIMARY KEY,
    goal_id              UUID NOT NULL REFERENCES goal,
    computed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    scenario             TEXT NOT NULL,      -- conservative | base | optimistic
    assumptions          JSONB NOT NULL,     -- the full CMA set used
    ruleset_version      TEXT NOT NULL,
    inflation_adj_target NUMERIC(18,4) NOT NULL,
    expected_corpus      NUMERIC(18,4) NOT NULL,
    corpus_p10           NUMERIC(18,4) NOT NULL,
    corpus_p50           NUMERIC(18,4) NOT NULL,
    corpus_p90           NUMERIC(18,4) NOT NULL,
    probability          NUMERIC(6,5) NOT NULL,
    required_return_pct  NUMERIC(6,3),
    required_monthly     NUMERIC(18,4)
);
CREATE INDEX ON goal_projection (goal_id, computed_at DESC);

CREATE TABLE risk_assessment (
    assessment_id    UUID PRIMARY KEY,
    investor_id      UUID NOT NULL REFERENCES investor,
    profile_version  INTEGER NOT NULL,
    capacity         JSONB NOT NULL,         -- score, band, factors
    tolerance        JSONB NOT NULL,
    requirement      JSONB NOT NULL,
    binding_band     TEXT NOT NULL,
    binding_reason   TEXT NOT NULL,
    max_equity_pct   INTEGER NOT NULL,
    conflicts        JSONB NOT NULL,
    assessed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Scheme master and data lineage

```sql
CREATE TABLE scheme (
    scheme_code      TEXT PRIMARY KEY,       -- AMFI code
    isin             TEXT,
    scheme_name      TEXT NOT NULL,
    amc              TEXT NOT NULL,
    plan             TEXT NOT NULL,          -- DIRECT | REGULAR
    option           TEXT NOT NULL,
    sebi_category    TEXT NOT NULL,
    asset_class      TEXT NOT NULL,
    tax_class        TEXT,                   -- LEGAL REVIEW: classification is a legal question
    management_style TEXT NOT NULL,
    benchmark_code   TEXT,
    riskometer       TEXT,
    launch_date      DATE,
    status           TEXT NOT NULL           -- ACTIVE | MERGED | WOUND_UP
);

-- Every observation of every field. Never updated, only appended.
CREATE TABLE scheme_observation (
    observation_id   BIGSERIAL PRIMARY KEY,
    scheme_code      TEXT NOT NULL REFERENCES scheme,
    field_name       TEXT NOT NULL,
    value            JSONB NOT NULL,
    source           TEXT NOT NULL,
    source_rank      SMALLINT NOT NULL,
    source_url       TEXT,
    as_of            TIMESTAMPTZ NOT NULL,
    ingested_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON scheme_observation (scheme_code, field_name, as_of DESC);

CREATE TABLE scheme_nav (
    scheme_code      TEXT NOT NULL REFERENCES scheme,
    nav_date         DATE NOT NULL,
    nav              NUMERIC(18,6) NOT NULL,
    source           TEXT NOT NULL,
    PRIMARY KEY (scheme_code, nav_date)
);

CREATE TABLE scheme_data_quality (
    scheme_code      TEXT NOT NULL REFERENCES scheme,
    computed_at      TIMESTAMPTZ NOT NULL,
    score            NUMERIC(5,2) NOT NULL,
    usable           BOOLEAN NOT NULL,
    reasons          JSONB NOT NULL,
    PRIMARY KEY (scheme_code, computed_at)
);

CREATE TABLE scheme_event (
    event_id         UUID PRIMARY KEY,
    scheme_code      TEXT NOT NULL REFERENCES scheme,
    event_type       TEXT NOT NULL,          -- MANAGER_CHANGE | MERGER | ATTRIBUTE_CHANGE | ...
    material         BOOLEAN NOT NULL,
    detail           JSONB NOT NULL,
    source           TEXT NOT NULL,
    effective_date   DATE NOT NULL,
    detected_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Portfolio and lots

```sql
CREATE TABLE holding (
    holding_id       UUID PRIMARY KEY,
    investor_id      UUID NOT NULL REFERENCES investor,
    scheme_code      TEXT NOT NULL REFERENCES scheme,
    goal_id          UUID REFERENCES goal,
    units            NUMERIC(18,6) NOT NULL,
    folio            TEXT,
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Tax lives at lot level. Portfolios do not have holding periods.
CREATE TABLE holding_lot (
    lot_id           UUID PRIMARY KEY,
    holding_id       UUID NOT NULL REFERENCES holding,
    units            NUMERIC(18,6) NOT NULL,
    cost_per_unit    NUMERIC(18,6) NOT NULL,
    purchase_date    DATE NOT NULL,
    source           TEXT NOT NULL,          -- RTA | INVESTOR_DECLARED
    redeemed_units   NUMERIC(18,6) NOT NULL DEFAULT 0
);
CREATE INDEX ON holding_lot (holding_id, purchase_date);

CREATE TABLE sip (
    sip_id           UUID PRIMARY KEY,
    investor_id      UUID NOT NULL REFERENCES investor,
    goal_id          UUID REFERENCES goal,
    scheme_code      TEXT NOT NULL REFERENCES scheme,
    amount           NUMERIC(18,4) NOT NULL,
    day_of_month     SMALLINT NOT NULL,
    step_up_pct      NUMERIC(6,3) NOT NULL DEFAULT 0,
    state            TEXT NOT NULL,
    started_on       DATE,
    paused_until     DATE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sip_event (
    event_id         BIGSERIAL PRIMARY KEY,
    sip_id           UUID NOT NULL REFERENCES sip,
    action           TEXT NOT NULL,          -- MODIFY | PAUSE | RESTART | STOP | STEP_UP | INSTALMENT | MISSED
    detail           JSONB NOT NULL,
    occurred_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

## Rules, versioned

```sql
CREATE TABLE ruleset_version (
    ruleset_id       TEXT NOT NULL,          -- tax.IN | compliance.IN | scoring.fund | ...
    version          TEXT NOT NULL,
    content          JSONB NOT NULL,         -- the whole YAML, parsed
    content_hash     TEXT NOT NULL,
    published_on     DATE NOT NULL,
    published_by     TEXT NOT NULL,
    approved_by      TEXT,                   -- required for tax and compliance
    active_from      TIMESTAMPTZ NOT NULL,
    active_to        TIMESTAMPTZ,
    PRIMARY KEY (ruleset_id, version)
);

CREATE TABLE regulatory_rule (
    rule_id             TEXT NOT NULL,
    ruleset_version     TEXT NOT NULL,
    title               TEXT NOT NULL,
    domain              TEXT NOT NULL,
    source              TEXT NOT NULL,
    source_url          TEXT,
    effective_from      DATE,
    effective_to        DATE,
    enforcement         TEXT NOT NULL,
    control_id          TEXT,
    verification_status TEXT NOT NULL,
    compliance_status   TEXT NOT NULL,
    legal_review_required BOOLEAN NOT NULL,
    system_impact       JSONB,
    PRIMARY KEY (rule_id, ruleset_version)
);
```

## Signals, recommendations, audit

```sql
CREATE TABLE signal_evaluation (
    evaluation_id    UUID PRIMARY KEY,
    investor_id      UUID NOT NULL REFERENCES investor,
    scheme_code      TEXT REFERENCES scheme,
    goal_id          UUID REFERENCES goal,
    state            TEXT NOT NULL,
    score            NUMERIC(6,2) NOT NULL,
    triggers_fired   JSONB NOT NULL,
    dampeners        JSONB NOT NULL,
    material_events  JSONB NOT NULL,
    suppressed_reason TEXT,
    ruleset_version  TEXT NOT NULL,
    evaluated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON signal_evaluation (investor_id, scheme_code, evaluated_at DESC);

-- Append-only. A response writes a new row that supersedes by content hash.
CREATE TABLE recommendation_audit (
    audit_id             BIGSERIAL PRIMARY KEY,
    recommendation_id    UUID NOT NULL,
    investor_id          UUID NOT NULL REFERENCES investor,
    created_at           TIMESTAMPTZ NOT NULL,
    profile_version      INTEGER,
    profile_fingerprint  TEXT,
    portfolio_snapshot   JSONB NOT NULL,
    market_data_snapshot JSONB NOT NULL,
    fund_data_snapshot   JSONB NOT NULL,     -- includes full provenance
    ruleset_versions     JSONB NOT NULL,
    regulatory_rules_used JSONB NOT NULL,
    tax_rules_used       JSONB NOT NULL,
    models_used          JSONB NOT NULL,
    stage_results        JSONB NOT NULL,     -- all 16 stages
    triggers_fired       JSONB NOT NULL,
    recommendation       JSONB NOT NULL,
    confidence           JSONB NOT NULL,
    explanation          TEXT NOT NULL,
    disclosures_shown    JSONB NOT NULL,
    conflicts_disclosed  JSONB NOT NULL,
    human_review         JSONB,
    investor_response    TEXT,
    investor_responded_at TIMESTAMPTZ,
    final_action         JSONB,
    outcome_review       JSONB,
    content_hash         TEXT NOT NULL,
    supersedes           TEXT                -- content_hash of the row it replaces
);
CREATE INDEX ON recommendation_audit (recommendation_id, audit_id);
CREATE INDEX ON recommendation_audit (investor_id, created_at DESC);

CREATE TABLE alert_dispatch (
    dispatch_id      UUID PRIMARY KEY,
    investor_id      UUID NOT NULL REFERENCES investor,
    alert_id         TEXT NOT NULL,
    severity         TEXT NOT NULL,
    scheme_code      TEXT REFERENCES scheme,
    dedupe_key       TEXT NOT NULL,
    payload          JSONB NOT NULL,
    status           TEXT NOT NULL,          -- SENT | SUPPRESSED | DEFERRED_* | QUEUED_*
    status_reason    TEXT NOT NULL,
    channels         JSONB NOT NULL,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ON alert_dispatch (investor_id, created_at DESC);
```

## Execution — present, and closed

```sql
CREATE TABLE transaction_consent (
    consent_id       UUID PRIMARY KEY,
    investor_id      UUID NOT NULL REFERENCES investor,
    recommendation_id UUID,
    scheme_code      TEXT NOT NULL REFERENCES scheme,
    direction        TEXT NOT NULL,          -- BUY | REDEEM | SWITCH
    amount           NUMERIC(18,4) NOT NULL,
    granted_at       TIMESTAMPTZ NOT NULL,
    expires_at       TIMESTAMPTZ NOT NULL,
    method           TEXT NOT NULL,
    tax_estimate_shown BOOLEAN NOT NULL,
    conflicts_shown  BOOLEAN NOT NULL,
    disclosures_shown JSONB NOT NULL,
    used_at          TIMESTAMPTZ,            -- single use
    evidence_ref     TEXT NOT NULL
);

CREATE TABLE transaction_order (
    order_id         UUID PRIMARY KEY,
    consent_id       UUID NOT NULL REFERENCES transaction_consent,
    investor_id      UUID NOT NULL REFERENCES investor,
    scheme_code      TEXT NOT NULL REFERENCES scheme,
    direction        TEXT NOT NULL,
    amount           NUMERIC(18,4),
    units            NUMERIC(18,6),
    idempotency_key  TEXT NOT NULL UNIQUE,
    status           TEXT NOT NULL,
    rails            TEXT NOT NULL,          -- BSE_STAR | NSE_MFSS | RTA_DIRECT
    placed_at        TIMESTAMPTZ,
    confirmed_at     TIMESTAMPTZ,
    tax_estimate     JSONB NOT NULL,
    audit_ref        UUID NOT NULL
);
```

`transaction_order` exists so the shape is settled and reviewable. Nothing writes
to it on this build: `execution_enabled` is false, `auto_execution_enabled` is
permanently false, and consent is never granted.

## Retention

| Data | Retention | Basis |
|---|---|---|
| Recommendation audit | Indefinite | Record-keeping; replayability |
| Transactions and consents | Per statutory record-keeping | Regulatory |
| Profile versions | Relationship + statutory period | Suitability defence |
| NAV and scheme history | Indefinite | Reference data |
| Alert dispatches | 3 years | Operational |
| Behavioural interventions | 3 years | Governance |
| Marketing consents | Until withdrawn + 1 year | DPDP |

Erasure requests are honoured except where a record-keeping obligation stands,
and that exception is disclosed at collection rather than discovered at the point
of request.
