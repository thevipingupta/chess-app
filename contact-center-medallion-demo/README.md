# Contact Center Medallion Demo

A synthetic-data demo that loads contact center data into Snowflake using a bronze / silver / gold medallion architecture, with control and data-quality logging in a `CONTROL` schema.

All data is synthetic. No real claimant, employer, agency, or client data is used.

See `CLAUDE.md` for the full design rules (naming, time zones, idempotency, and DQ requirements).

## Folder layout

```
contact-center-medallion-demo/
├── CLAUDE.md               # project reference and rules
├── README.md               # this file
├── data/source/            # generated source feeds + _planted_issues.csv (git-ignored)
├── src/generate/           # seeded synthetic data generator
├── sql/
│   ├── 00_setup/           # database CC_DEMO, schemas, CONTROL tables
│   ├── 10_bronze/          # raw_ tables and sp_load_raw_* procedures
│   ├── 20_silver/          # stg_ tables, quarantine tables, sp_build_stg_* procedures
│   ├── 30_gold/            # dim_ / fact_ tables and sp_build_gold_* procedures
│   └── 90_orchestration/   # sp_run_* wrappers
└── tests/                  # DQ and idempotency checks
```

## Prerequisites

- A Snowflake account and a role that can create databases, schemas, tables, and procedures.
- Python 3.9+ for the synthetic data generators.
- Snowflake connection details in environment variables (never committed):
  - `SNOWFLAKE_ACCOUNT`, `SNOWFLAKE_USER`, `SNOWFLAKE_ROLE`, `SNOWFLAKE_WAREHOUSE`
  - Authentication: `SNOWFLAKE_PASSWORD`, or a key pair configured in your Snowflake client profile.

## How to run each step

Run the steps in order. Each script can be re-run safely.

### Step 0: Set up the database, schemas, and control tables

Run `sql/00_setup/*.sql` in your Snowflake client (Snowsight, SnowSQL, or `snowsql -f`).

```bash
snowsql -f sql/00_setup/01_database_and_schemas.sql
snowsql -f sql/00_setup/02_control_tables.sql
```

This creates `CC_DEMO` with the `BRONZE`, `SILVER`, `GOLD`, and `CONTROL` schemas, plus `CONTROL.load_run_log` and `CONTROL.dq_result`.

### Step 1: Generate synthetic source data

```bash
python src/generate/generate_sources.py                 # seed 20260302 -> data/source/
python src/generate/generate_sources.py --seed 7        # a different, still reproducible, dataset
```

The generator reads the Five9 column lists and types from `reference/Contact_Center.xlsx` (copy the workbook there; it is git-ignored). It needs `openpyxl` plus the standard library. It writes 14 weeks (2026-03-02 to 2026-06-07, batches B001 to B098) to `data/source/` (git-ignored):

- `five9/ACD_ALL_<yyyymmdd>_B<nnn>.csv`: 30-minute interval rows by skill and campaign
- `five9/CALLLOG_<yyyymmdd>_B<nnn>.csv`: one row per call (177 workbook columns)
- `five9/CALL_SEGMENT_<yyyymmdd>_B<nnn>.csv`: one row per call segment, joined on CALL ID
- `five9/VOICEMAIL_LIFECYCLE_<yyyymmdd>_B<nnn>.csv`: voicemail events, joined on CONTACT ID
- `wfm/wfm_daily_<yyyymmdd>_B<nnn>.csv`: ASSUMPTION, not a Five9 export (commented header)
- `_planted_issues.csv`: planted data quality issues, with batch, file, column, and detection rule
- `_reference/header_changes.csv`: batches where the header changed

It also writes `docs/source_column_map.csv`, one row per workbook column with its snake_case name, silver table, silver type, and PII flag.

The generator is seeded, so the same seed produces byte-identical files. It prints row counts, planted-issue totals, the schema change batch, and a check that every generated header column exists in the workbook.

### Step 2: Load bronze

Stage the extracts, then load them with the bronze procedures:

```bash
snowsql -f sql/10_bronze/01_raw_tables.sql
snowsql -f sql/10_bronze/02_sp_load_raw.sql
snowsql -q "CALL CC_DEMO.BRONZE.sp_load_raw_contact_event('BATCH_0001', '@CC_DEMO.BRONZE.stg_synthetic/contact_event/')"
```

Re-running the same `batch_id` inserts no new rows.

### Step 3: Build silver

```bash
snowsql -f sql/20_silver/01_stg_tables.sql
snowsql -f sql/20_silver/02_sp_build_stg.sql
snowsql -q "CALL CC_DEMO.SILVER.sp_build_stg_contact_event('BATCH_0001')"
```

Deduplicates, types, and converts timestamps to `America/New_York`. Rows that fail validation go to the `stg_*_quarantine` tables with a `dq_reason_code`.

### Step 4: Build gold

```bash
snowsql -f sql/30_gold/01_star_schema.sql
snowsql -f sql/30_gold/02_sp_build_gold.sql
snowsql -q "CALL CC_DEMO.GOLD.sp_build_gold_contact('BATCH_0001')"
```

Builds the star schema (`fact_contact`, `dim_agent`, `dim_queue`, `dim_disposition`, `dim_date`) from silver.

### Step 5: Run everything end to end

```bash
snowsql -f sql/90_orchestration/sp_run_all.sql
snowsql -q "CALL CC_DEMO.CONTROL.sp_run_all('BATCH_0001')"
```

### Step 6: Check the results

```bash
snowsql -q "SELECT * FROM CC_DEMO.CONTROL.load_run_log ORDER BY started_at_utc DESC LIMIT 20"
snowsql -q "SELECT * FROM CC_DEMO.CONTROL.dq_result ORDER BY checked_at_utc DESC LIMIT 50"
```

Run the checks in `tests/` to confirm idempotency (a re-run adds zero rows) and DQ.

> The scripts listed above are the planned layout. They are created in later steps; see `CLAUDE.md`.
