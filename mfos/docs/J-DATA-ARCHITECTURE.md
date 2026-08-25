# J. Data Architecture

Implementation: `funds.py`.

## The failure this is built to prevent

One vendor feed, no cross-check, a stale expense ratio, and a confident
recommendation built on it. This is the quiet, common failure in Indian fintech
data, and it is invisible until someone reconciles a statement.

So: multiple sources, a published hierarchy, recorded disagreements, and a
quality score that can stop the pipeline.

## Source hierarchy

Lower rank wins a disagreement.

| Rank | Source | Used for |
|---|---|---|
| 1 | SEBI | Regulations, circulars, categorisation |
| 2 | Government of India / Income Tax Dept | Tax rules, notifications |
| 3 | AMFI | NAV, AUM, category lists, market-cap classification |
| 4 | AMC SID / addendum | Scheme attributes, material changes |
| 5 | AMC factsheet | Holdings, sector splits, manager, turnover |
| 6 | RTA (CAMS, KFin) | Investor holdings, transactions, lots |
| 7 | Exchange (BSE StAR MF, NSE MFSS) | Order and settlement data |
| 8 | Validated vendor | Fill-ins, cross-checks |
| 9 | Derived | Computed by us from a higher source |
| 10 | User-supplied | Profile, declarations |

The two consequences that matter: a vendor never overrides AMFI on NAV, and
nothing derived ever overrides a primary source.

## Observations, not values

A field is not a value. It is a list of observations, each with a source and a
timestamp, never overwritten in place:

```python
scheme.observe("nav", 245.30, "AMFI", timestamp)
scheme.observe("nav", 245.10, "RTA",  timestamp)
```

`Field.resolve()` returns the highest-ranked, most recent observation.
`Field.disagreement()` reports material numeric divergence beyond the field's
tolerance. Both are stored in the audit snapshot, so a later reader can see not
only what value was used but what the alternatives were.

## Data quality scoring

0–100 per scheme, with reasons attached. Below `min_data_quality_score` (70) a
scheme is not scored, not recommended and not displayed as known.

| Dimension | Penalty |
|---|---|
| Missing critical field (NAV, expense, category, risk-o-meter, benchmark) | −18 each |
| Missing important field (AUM, top-10, manager, tenure, holdings) | −5 each |
| Stale beyond the per-field limit | up to −25, scaled by how stale |
| Low-rank source on a critical field | −6 |
| Single source on NAV | −8 |
| Cross-source disagreement | up to −20, scaled by spread |
| History below 36 months | −10 |
| Holdings not summing to 100 ± 5 | −10 |
| Implausible NAV | −40 |

Staleness limits are per field class, because they have genuinely different
cadences: NAV 30 hours, expense ratio 800 hours, holdings 1,200 hours (monthly
disclosure plus slack).

## This is "data quality overrides AI", implemented

`funds.require_usable()` raises `DataQualityFailure`. The pipeline halts at
stage 7. The investor sees:

> I don't have sufficient verified data on Kestrel Flexi Cap Direct to make a
> recommendation about it.

Which is a better outcome than a confident recommendation built on a
four-week-old expense ratio.

## Data domains

| Domain | Source of truth | Refresh | Retention |
|---|---|---|---|
| Scheme master | AMFI + AMC SID | Daily | Indefinite |
| NAV history | AMFI | Daily | Indefinite |
| Benchmark history | Index providers | Daily | Indefinite |
| Portfolio holdings | AMC factsheet | Monthly | 10 years |
| Expense ratio | AMC | Monthly + on change | Indefinite |
| Category classification | AMFI half-yearly list | Half-yearly | Indefinite |
| Investor profile | Investor | On change | Per DPDP + record-keeping |
| Investor holdings | RTA | Daily | Per record-keeping |
| Transactions | RTA / exchange | Real time | Per record-keeping |
| Regulatory rules | SEBI, AMFI, GoI | On publication | Indefinite, versioned |
| Tax rules | Income Tax Dept | On Finance Act | Indefinite, versioned |
| Recommendations | This platform | On creation | Indefinite, append-only |

Market-cap classification deserves a note: AMFI publishes the half-yearly list
that defines large, mid and small cap. The platform **ingests that list** rather
than deriving the ranking itself, because deriving it would produce a
classification that disagrees with the one the industry actually uses.

## Lineage

Every value carries: field, value, source, timestamp, URL, count of independent
sources, and any disagreement. `snapshot_schemes()` freezes all of it into the
audit record at decision time.

The distinction that matters: the audit record contains a **snapshot**, not a
reference. A recommendation defended against today's NAV is not defended at all.

## Reconciliation

| Check | Cadence | On failure |
|---|---|---|
| NAV: AMFI vs RTA | Daily | Flag, use AMFI, alert data ops |
| Holdings sum to 100% | On ingest | Reject the document, retain the previous |
| Units: platform vs RTA | Daily | Freeze the account, escalate |
| Expense ratio vs SID | On change | Flag for review before use |
| Category vs AMFI list | Half-yearly | Re-group peers, recompute scores |

## Synthetic data

`synthetic.py` generates the demo and test universe. Every scheme it produces is
fictional, the module says so at the top, and
`test_synthetic_data_is_labelled` asserts the docstring still says so.

It exists for two reasons: the pipeline can be run and tested end to end without
a live data licence, and a reader can see the exact shape of the data the real
feeds must supply.

## Privacy by design

- **Minimisation.** Tier-2 and Tier-3 fields are collected only when a specific
  decision needs them, with the reason shown.
- **Purpose limitation.** Each field records why it was collected.
- **Encryption.** At rest and in transit; financial fields additionally
  field-level encrypted.
- **Access.** Role-based, least privilege, every access logged.
- **Retention.** Per domain above; record-keeping obligations outlive the
  investor relationship and that is disclosed at collection.
- **Erasure.** Investor data deletable on request except where record-keeping
  obligations require retention — which is stated up front, not discovered at
  the point of request.
- **AI data use.** Investor data is never used to train a third-party model.
  Recorded as a platform constraint, not a preference.
