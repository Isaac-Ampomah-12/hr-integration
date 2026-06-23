# GlobalTech Corp — Multi-Source HR Data Integration Pipeline

**SK-01 Capstone Project**

---

## Table of Contents

1. [Overview](#overview)
2. [Business Context](#business-context)
3. [Quick Start](#quick-start)
4. [Project Structure](#project-structure)
5. [Input Sources](#input-sources)
6. [Pipeline Architecture](#pipeline-architecture)
7. [Output Files](#output-files)
8. [Golden Dataset Schema](#golden-dataset-schema)
9. [How to Run](#how-to-run)
10. [Known Limitations](#known-limitations)
11. [Assumptions](#assumptions)
12. [Change Log](#change-log)

---

## Overview

This pipeline consolidates employee data from four heterogeneous HR source systems into a single, clean, deduplicated **golden dataset** ready for workforce analytics, payroll processing, and compliance reporting.

It ingests ~49,000 raw records across CSV, JSON, XLSX, and XML formats; applies systematic cleaning, currency normalization, and entity resolution; validates 15 data-quality rules; produces a 6-chart EDA report; and exports the golden dataset as a partitioned Parquet file with associated audit artifacts.

---

## Business Context

GlobalTech Corp (15,000 employees) recently acquired AcquiredCo (3,200 employees). Each company ran independent HR, payroll, and benefits systems with incompatible schemas, naming conventions, ID formats, and data quality standards.

Before the merged workforce can be managed as a single entity — for payroll, benefits enrollment, org-chart reporting, or regulatory filings — the data must be:

- **Unified** under a common schema with consistent employee IDs (GT-/AC- namespaced)
- **Cleaned** to remove formatting artefacts, normalize names and departments, and standardize currencies
- **Deduplicated** to eliminate cross-source duplicates and flag probable same-person matches for HR review
- **Validated** against a set of business rules before any downstream system consumes the data
- **Documented** so that future engineers and auditors understand the provenance of every record

---

## Quick Start

```bash
# Install dependencies
pip install pandas openpyxl rapidfuzz pyarrow matplotlib seaborn

# Run the full pipeline
python pipeline.py

# Run with gate disabled (dev / debugging)
python pipeline.py --no-gate

# Run individual deliverables
python ingest.py        # Deliverable 1
python clean.py         # Deliverable 2
python deduplicate.py   # Deliverable 3
python validate.py      # Deliverable 4
python visualize.py     # Deliverable 5
python export.py        # Deliverable 6
```

All outputs are written to `Data/processed/`.

---

## Project Structure

```
hr-integration/
├── config.py           Central configuration (paths, constants, logging)
├── ingest.py           Deliverable 1 — multi-source ingestion
├── clean.py            Deliverable 2 — cleaning & standardization
├── deduplicate.py      Deliverable 3 — three-pass deduplication
├── validate.py         Deliverable 4 — data quality validation
├── visualize.py        Deliverable 5 — EDA visualization report
├── export.py           Deliverable 6 — golden dataset export
├── pipeline.py         End-to-end orchestrator
├── pipeline.log        Run log (appended each execution)
├── README.md           This file
└── Data/
    ├── raw/            Source files (read-only inputs)
    │   ├── globaltech_hris.csv
    │   ├── acquiredco_api.json
    │   ├── payroll_data.xlsx
    │   └── benefits_enrollment.xml
    └── processed/      Pipeline outputs
        ├── golden_employees/           Partitioned Parquet
        │   ├── company_origin=GlobalTech/
        │   └── company_origin=AcquiredCo/
        ├── golden_employees_schema.csv
        ├── ghost_employees.csv
        ├── probable_matches_review.csv
        ├── validation_report.csv
        ├── validation_report.html
        └── eda_report.png
```

---

## Input Sources

| # | Source | File | Format | Expected Rows | Description |
|---|--------|------|--------|---------------|-------------|
| 1 | GlobalTech HRIS | `Data/raw/globaltech_hris.csv` | CSV (UTF-8) | 15,000 | Primary employee master from GlobalTech's HRIS. Fields: employee_id (int), first_name, last_name, email, department (code or full name), job_title, hire_date, country, employment_type, employment_status, manager_id |
| 2 | AcquiredCo HRIS | `Data/raw/acquiredco_api.json` | JSON (BambooHR-style paginated) | 3,200 | Employee records from AcquiredCo's cloud HR system. Paginated at 100 records/page. Fields: employee_identifier (`ACQ_NNNNN`), first_name, last_name, email, department, job_title, start_date, location, employment_status, emp_type (FT/PT/CONTRACTOR), reports_to |
| 3 | Payroll | `Data/raw/payroll_data.xlsx` | Excel (sheet: Payroll) | 19,000 | Payroll extract with duplicate entries per employee. Fields: employee_id, source (GlobalTech/AcquiredCo), base_salary (mixed numeric/formatted), currency (USD/EUR/GBP), pay_frequency (Annual/Monthly/Bi-Weekly), bonus_target_pct, effective_date |
| 4 | Benefits | `Data/raw/benefits_enrollment.xml` | XML | 12,000 | Benefits enrollment records (multiple plans per GlobalTech employee). Fields: employee_id, plan_type, coverage_level, enrollment_date, premium_employee, premium_employer |

### Standard Post-Ingest Schema

All employee sources are aligned to the following 12-column schema before cleaning:

| Column | Type | Notes |
|--------|------|-------|
| employee_id | str | Raw ID before namespacing |
| source_system | str | GlobalTech_HRIS or AcquiredCo_HRIS |
| first_name | str | |
| last_name | str | |
| email | str | |
| department | str | |
| job_title | str | |
| hire_date | str | Multiple formats; parsed in clean step |
| country | str | |
| employment_type | str | |
| employment_status | str | |
| manager_id | str | |

---

## Pipeline Architecture

```
Raw Files
    │
    ▼
[ingest.py] ──────────────────────────── dead_letter.csv (parse errors)
    │
    │  employees (18,200)  payroll (19,000)  benefits (12,000)
    ▼
[clean.py]
    │  • Name normalization (Unicode NFC, title-case, Mc/Mac)
    │  • Employee ID namespacing  GT-XXXXXX / AC-XXXXXX
    │  • Currency conversion to USD + annual normalization
    │  • Department taxonomy mapping (18 canonical names)
    │  • Date standardization (3 formats → datetime64)
    │  • Payroll dedup: keep most-recent per (employee_id, source)
    │
    ▼
[deduplicate.py]
    │  • Pass 1: exact ID match — source priority HRIS > Payroll > Benefits
    │  •         intra-source ID collisions quarantined (199 AC-XXXXXX)
    │  • Pass 2: cross-company email match (0 in current data)
    │  • Pass 3: fuzzy name + ±30-day hire date (rapidfuzz ≥ 88)
    │  •         → probable_matches review file (113 pairs)
    │  • Ghost detection: payroll IDs not in HRIS (0 in current data)
    │  • Provenance: source_systems + dedup_method columns added
    │
    ▼
[validate.py] ───────────────────────── validation_report.csv / .html
    │  15 checks across 7 categories
    │  Pipeline gate: halt if > 2 checks fail
    │
    ▼
[visualize.py] ──────────────────────── eda_report.png (300 DPI)
    │  6 charts: dept headcount, country headcount,
    │  salary dist, tenure dist, benefits enrollment, DQ summary
    │
    ▼
[export.py] ─────────────────────────── golden_employees/ (Parquet)
                                         golden_employees_schema.csv
                                         ghost_employees.csv
                                         probable_matches_review.csv
```

---

## Output Files

| File | Format | Rows | Description |
|------|--------|------|-------------|
| `golden_employees/` | Parquet (Snappy) | 18,001 | Partitioned by `company_origin`. The authoritative employee master. Read with `pd.read_parquet("Data/processed/golden_employees")` |
| `golden_employees_schema.csv` | CSV | 19 | Column-level data dictionary: name, type, description, example, nullable |
| `ghost_employees.csv` | CSV | 0 (current) | Payroll records with no HRIS counterpart — fraud/compliance risk |
| `probable_matches_review.csv` | CSV | 113 | Fuzzy-matched cross-company pairs for HR confirmation before any merge |
| `validation_report.csv` | CSV | 15 | One row per quality check: total, passed, failed, pass_rate, status |
| `validation_report.html` | HTML | — | Styled version of the validation report with pipeline gate result |
| `eda_report.png` | PNG 300 DPI | — | 6-chart workforce analytics dashboard (6554 × 8031 px) |
| `pipeline.log` | Plain text | — | Timestamped run log appended on each execution |

### Reading the golden dataset

```python
import pandas as pd

# Full dataset
df = pd.read_parquet("Data/processed/golden_employees")

# GlobalTech only
gt = pd.read_parquet("Data/processed/golden_employees",
                     filters=[("company_origin", "=", "GlobalTech")])

# AcquiredCo only
acq = pd.read_parquet("Data/processed/golden_employees",
                      filters=[("company_origin", "=", "AcquiredCo")])
```

---

## Golden Dataset Schema

19 columns in the golden employee Parquet.

| # | Column | Type | Nullable | Description | Example |
|---|--------|------|----------|-------------|---------|
| 1 | `employee_id` | string | NO | Namespaced ID: GT-XXXXXX (GlobalTech) or AC-XXXXXX (AcquiredCo) | `GT-000001` |
| 2 | `source_system` | string | NO | Primary HR system of record | `GlobalTech_HRIS` |
| 3 | `first_name` | string | YES | Normalized first name (Unicode NFC, title-case) | `Michael` |
| 4 | `last_name` | string | YES | Normalized last name (title-case, hyphens/apostrophes handled) | `King` |
| 5 | `full_name` | string | YES | Display name: first_name + " " + last_name | `Michael King` |
| 6 | `email` | string | YES | Work email; validated against RFC-style regex | `michael.king@globaltech.com` |
| 7 | `department` | string | YES | Canonical department from 18-name taxonomy | `Engineering` |
| 8 | `job_title` | string | YES | Free-text job title as provided by source | `Senior Engineer` |
| 9 | `hire_date` | timestamp[ns] | YES | Date of hire; parsed from YYYY-MM-DD, MM/DD/YYYY, DD-Mon-YYYY | `2018-03-15` |
| 10 | `country` | string | YES | Country of primary employment | `United States` |
| 11 | `employment_type` | string | YES | Full-Time \| Part-Time \| Contractor | `Full-Time` |
| 12 | `employment_status` | string | YES | Active \| Inactive \| On Leave | `Active` |
| 13 | `manager_id` | string | YES | Manager's namespaced employee_id; `""` if none recorded | `GT-002341` |
| 14 | `first_name_raw` | string | YES | Original first name before normalization (audit trail) | `MICHAEL` |
| 15 | `last_name_raw` | string | YES | Original last name before normalization (audit trail) | `king` |
| 16 | `source_systems` | string | NO | Comma-sorted systems containing this employee_id | `globaltech_hris,payroll` |
| 17 | `dedup_method` | string | NO | How provenance was resolved: `exact_id` \| `email_match` \| `fuzzy_name` \| `single_source` | `exact_id` |
| 18 | `company_origin` | string | NO | Partition key: `GlobalTech` or `AcquiredCo` | `GlobalTech` |
| 19 | `tenure_years` | double | YES | Years of service = (report_date − hire_date) / 365.25, 2 d.p. | `7.42` |

---

## How to Run

### Prerequisites

- Python 3.10 or later
- All source files present in `Data/raw/`
- Required packages:

```bash
pip install pandas openpyxl rapidfuzz pyarrow matplotlib seaborn pillow
```

### Run the full pipeline

```bash
python pipeline.py
```

The pipeline will:
1. Load all four raw sources
2. Clean and standardize all records
3. Run three-pass deduplication
4. Run 15 validation checks — **halts if more than 2 fail**
5. Produce the EDA visualization report
6. Export the golden dataset and all audit files

### Override the validation gate (debugging only)

```bash
python pipeline.py --no-gate
```

### Run individual modules

Each module has a `__main__` block that runs a standalone smoke-test loading data from scratch:

```bash
python ingest.py        # test ingestion, prints row counts per source
python clean.py         # test cleaning, prints CleanReport summaries
python deduplicate.py   # test dedup, prints pass-by-pass summaries
python validate.py      # test validation, prints the 15-check report
python visualize.py     # test visualization, saves eda_report.png
python export.py        # test export, verifies Parquet round-trip
```

### Checking the validation gate result

Open `Data/processed/validation_report.html` in a browser for the styled report.  
The gate status (PASSED / FAILED) is shown in the summary banner at the top.

---

## Known Limitations

1. **Email uniqueness failures (8,861 rows).**  
   The synthetic test data generates email addresses as `first.last@company.com`. With 15,000+ employees, common name combinations (e.g., two people named Aaron Allen at GlobalTech) produce colliding email addresses. In a production system, email uniqueness is enforced at account provisioning time and this check would pass. The `unique.email` check will fail in CI on this dataset.

2. **AcquiredCo ID collisions (199 quarantined rows).**  
   AcquiredCo's system tags employees who also exist in GlobalTech as `ACQ_DUP_NNNNN`. The numeric suffix of `ACQ_DUP_00001` (→ AC-000001) overlaps with the regular employee `ACQ_00001` (→ AC-000001), creating 199 same-canonical-ID collisions between different people. The deduplicate module detects and quarantines the DUP copies. The clean module's ID normalization should be updated in production to use a distinct offset for DUP IDs (e.g., `AC-9XXXXX`).

3. **Salary annualization inflation.**  
   The payroll source stores `base_salary` as the per-period amount. Multiplying a $50,000 bi-weekly salary by 26 gives a $1.3M annual figure — mathematically correct but unusual for a typical workforce. 3,004 payroll records exceed the $2M annual threshold in the validation check. Investigate whether `base_salary` should be interpreted as annual rather than per-period for some records.

4. **Benefits data covers GlobalTech only.**  
   The benefits XML file (`benefits_enrollment.xml`) was provided for GlobalTech employees only. AcquiredCo benefits enrollment data was not included in the source extract. The benefits enrollment chart (Deliverable 5, Chart 5) and benefits deduplication are therefore limited to GlobalTech employees. The enrollment rate of ~55% reflects GT employees enrolled across one or more benefit plans; it does not represent the full merged workforce.

5. **Static FX rates.**  
   Currency conversion uses hardcoded rates (EUR = 1.08, GBP = 1.27 to USD). These rates must be updated or replaced with a live feed before any financial reporting use case.

6. **Point-in-time snapshot.**  
   The pipeline processes a single snapshot of all source files. There is no incremental or change-data-capture (CDC) logic. Re-running the pipeline replaces all outputs.

7. **113 probable matches require manual HR action.**  
   The deduplication module flags but does not auto-merge fuzzy-matched cross-company employee pairs. Until HR reviews `probable_matches_review.csv` and confirms or rejects each pair, the golden dataset may contain duplicate individuals counted as separate headcount.

8. **No payroll or benefits data for AcquiredCo.**  
   The payroll file contains records tagged as `AcquiredCo` (mapped to AC-XXXXXX IDs), but the source label is `source = "AcquiredCo"` in the raw payroll data — not `AcquiredCo_HRIS`. The mapping `clean_payroll()` handles this but the alignment should be confirmed with the AcquiredCo payroll team.

---

## Assumptions

1. **HRIS is the system of record.** When the same employee_id appears in HRIS, Payroll, and Benefits, the HRIS record is authoritative for all non-financial employee attributes (name, department, hire date, etc.).

2. **Employee IDs are unique within each HRIS system** at the point of export (ignoring the ACQ_DUP data-quality issue documented above).

3. **All GlobalTech payroll records use GT-XXXXXX IDs and all AcquiredCo payroll records use AC-XXXXXX IDs** after namespacing. The `source` column in the payroll file (`"GlobalTech"` / `"AcquiredCo"`) is the authoritative mapping signal.

4. **Department names in both HRIS sources are either canonical names** (matching the 18-name taxonomy directly) or GlobalTech legacy codes (ENG-01, MKT-03, etc.). Any unmapped department value is preserved as-is and logged as a warning.

5. **Hire dates are reliable.** The ±30-day blocking window for fuzzy name matching is a heuristic; a same person starting at both companies within 30 days of each other is plausible (e.g., an internal transfer with overlapping employment) but rare.

6. **Benefits premiums are non-negative.** Any premium value below zero is treated as a data entry error and flagged in the clean step.

7. **The ACQ_DUP records represent the same physical person as a GlobalTech employee** (they were intentionally seeded as cross-company duplicates). After the ID collision fix, Pass 3 fuzzy matching is expected to identify these pairs for HR review.

8. **No PII leaves the pipeline.** The probable matches CSV contains names and emails for HR review — it must be handled under the same access controls as the source HR data.

---

## Change Log

### v1.0.0 — 2026-06-22

Initial implementation — SK-01 Capstone Project.

| Deliverable | Module | Description |
|-------------|--------|-------------|
| 1 — Ingestion | `ingest.py` | Multi-source loader for CSV (GlobalTech HRIS), JSON/BambooHR (AcquiredCo), XLSX (Payroll), XML (Benefits). Dead-letter queue for unparseable records. |
| 2 — Cleaning | `clean.py` | Name normalization (Unicode NFC, title-case, Mc/Mac), employee ID namespacing (GT-/AC-), currency conversion to USD, pay-frequency annualization, department taxonomy mapping (18 canonical names), multi-format date parsing, employment type/status validation. |
| 3 — Deduplication | `deduplicate.py` | Pass 1: exact ID match with HRIS > Payroll > Benefits source priority and intra-source ID collision detection. Pass 2: cross-company email match. Pass 3: rapidfuzz token_sort_ratio ≥ 88 with ±30-day hire-date blocking (748,871 comparisons → 113 probable pairs). Ghost employee detection. Provenance tracking (`source_systems`, `dedup_method`). |
| 4 — Validation | `validate.py` | 15 checks across NOT NULL, UNIQUE, VALUES IN SET, REGEX, NUMERIC RANGE, DATE RANGE, and REFERENTIAL INTEGRITY categories. Pipeline gate: halt if > 2 checks fail. CSV + HTML report output. |
| 5 — Visualization | `visualize.py` | 6-chart 300 DPI EDA report: headcount by department, headcount by country, salary distribution by employment type, tenure distribution, benefits enrollment rate by department, data quality summary. Okabe-Ito colorblind-safe palette. |
| 6 — Export | `export.py` | Golden employee Parquet (Snappy-compressed, partitioned by company_origin), 19-column schema CSV, ghost employee CSV, probable-match review CSV with recommended actions. |
| — | `pipeline.py` | End-to-end orchestrator with `--no-gate` flag. |
| — | `config.py` | Central configuration: file paths, FX rates, pay-frequency multipliers, department taxonomy, logging (file + stream handlers). |
