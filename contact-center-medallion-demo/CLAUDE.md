# CLAUDE.md — contact-center-medallion-demo

Reference for all work in this project. Read it before every step.

## Purpose

A synthetic-data demo showing contact center data ingested from source systems into Snowflake using a medallion architecture (bronze, silver, gold).

## Audience

A state government client (VEC) evaluating a PFML (Paid Family and Medical Leave) solution. Build for clarity and evaluation, not production volume.

## Data handling (hard rule)

- All data is synthetic. Do not use any real claimant, employer, agency, or client data.
- Generate every source record with code in this repo. Do not copy, paste, or derive records from real systems, files, or tickets.
- Do not put real names, SSNs, addresses, phone numbers, emails, or case numbers in any file. Use obviously fake patterns (e.g., 555-area phone numbers, `CLM-000001`-style IDs).
- Never commit credentials. Snowflake connection details come from environment variables or a local, git-ignored profile.

## Target platform

- Snowflake.
- Snowflake SQL and SQL Scripting (`LANGUAGE SQL`) for stored procedures.
- Database: `CC_DEMO`.
- Schemas: `BRONZE`, `SILVER`, `GOLD`, `CONTROL`.

## Time zone

- Bronze stores all timestamps as UTC.
- Silver converts to `America/New_York`, using `CONVERT_TIMEZONE('UTC', 'America/New_York', <col>)`.
- Use `TIMESTAMP_NTZ` for every stored timestamp. Never use `TIMESTAMP_LTZ` or `TIMESTAMP_TZ` for stored values.
- Silver timestamp columns that hold Eastern wall-clock time carry the suffix `_et` (for example, `contact_start_ts_et`). Bronze columns carry the suffix `_utc`.

## Naming

- `snake_case` for every object: database, schema, table, view, column, procedure, and file name. Keywords in SQL stay uppercase.
- Bronze tables: prefix `raw_` (for example, `BRONZE.raw_contact_event`).
- Silver tables: prefix `stg_` for cleaned staging tables (for example, `SILVER.stg_contact_event`), or `dim_` / `fact_` where a silver table is a conformed dimension or fact.
- Gold tables: prefix `dim_` for dimensions and `fact_` for facts (for example, `GOLD.fact_contact`, `GOLD.dim_agent`).
- Quarantine tables in silver: `stg_<entity>_quarantine`.
- Procedures: `sp_load_raw_<entity>` (bronze), `sp_build_stg_<entity>` (silver), `sp_build_gold_<model>` (gold), and `sp_run_<layer>` orchestrators.

## Standard bronze metadata

Every bronze table has these four columns, in addition to the raw source columns:

| Column          | Type           | Meaning                                                              |
|-----------------|----------------|----------------------------------------------------------------------|
| `_batch_id`     | VARCHAR        | Identifier of the load batch that wrote the row.                     |
| `_source_file`  | VARCHAR        | Source file name (or stage path) the row came from.                  |
| `_load_ts`      | TIMESTAMP_NTZ  | UTC time the row was loaded.                                         |
| `_record_hash`  | VARCHAR        | Hash of the raw source column values (for example, `SHA2(..., 256)`).|

## Layer rules

### Bronze
- Append-only. Never update or delete bronze rows.
- Store raw values as received. Source columns are `VARCHAR` so no coercion happens at ingest; typing happens in silver.
- Timestamps are UTC.

### Silver
- Deduplicated, typed, and standardized (trimmed, case-normalized codes, Eastern time, valid domain values).
- Rows that fail validation are written to `stg_<entity>_quarantine` with a `dq_reason_code` column. They do not appear in the clean `stg_` table.
- Deduplication key and record hash are documented per table.

### Gold
- Star schema, optimized for analytics: `dim_` tables with surrogate keys, `fact_` tables that reference them, and a stable grain documented per fact.
- Built only from silver. Gold never reads bronze.

## Idempotency (required)

Every load procedure must be safe to re-run:

- Bronze: skip the load if `_batch_id` is already present in the target (checked against `CONTROL.load_run_log` and the target table). Re-running the same batch inserts zero rows.
- Silver and gold: use `MERGE` keyed on the business key (or `_record_hash` where no business key exists). Re-running a batch must not create duplicate rows.
- A re-run is logged as its own run with `rows_inserted = 0`.

## Control and observability (required)

Every load writes to the `CONTROL` schema:

- `CONTROL.load_run_log`: one row per procedure run. Columns include `run_id`, `batch_id`, `layer`, `procedure_name`, `target_table`, `status`, `rows_read`, `rows_inserted`, `rows_updated`, `rows_quarantined`, `started_at_utc`, `ended_at_utc`.
- `CONTROL.dq_result`: one row per data-quality check per run. Columns include `run_id`, `batch_id`, `target_table`, `check_name`, `severity`, `failed_count`, `status` (`PASS` / `FAIL` / `WARN`), `checked_at_utc`.

Procedures return a summary (run_id and status). A run with any `FAIL` DQ check at `ERROR` severity sets `status = 'FAILED'` and stops downstream layers.

## Procedure and SQL conventions

- Idempotent DDL: `CREATE TABLE IF NOT EXISTS` and `CREATE OR REPLACE` only for procedures and views.
- Procedures use `LANGUAGE SQL`, `EXECUTE AS OWNER`, and explicit `BEGIN ... EXCEPTION ... END` blocks that write a `FAILED` row to `CONTROL.load_run_log`.
- Fully qualify every object name (`CC_DEMO.BRONZE.raw_...`).
- Each script is re-runnable on its own.

## Proposed initial domain (confirm before building)

- Bronze sources (synthetic): contact events (call, chat, and email interactions), agents, queues, and dispositions.
- Silver: `stg_contact_event`, `stg_agent`, `stg_queue`, `stg_disposition`, plus `*_quarantine` for each.
- Gold: `fact_contact` (grain: one row per contact interaction), `dim_agent`, `dim_queue`, `dim_disposition`, `dim_date`.

## Repository layout

```
contact-center-medallion-demo/
├── CLAUDE.md
├── README.md
├── data/
│   └── source/             # generated source feeds (acd/, contacts/, wfm/, roster/), planted-issue log; git-ignored
├── src/
│   └── generate/           # generate_sources.py: seeded synthetic generator (stdlib only)
├── sql/
│   ├── 00_setup/           # database, schemas, CONTROL tables
│   ├── 10_bronze/          # raw_ DDL and sp_load_raw_* procedures
│   ├── 20_silver/          # stg_ / quarantine DDL and sp_build_stg_* procedures
│   ├── 30_gold/            # dim_ / fact_ DDL and sp_build_gold_* procedures
│   └── 90_orchestration/   # sp_run_* wrappers and run order
└── tests/                  # DQ and idempotency checks (SQL)
```

Scripts are numbered so they run in order. Later steps follow this layout unless the user changes it.
