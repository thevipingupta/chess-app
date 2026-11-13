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

## Source system: Five9

- Five9 API exports. Source column names, types, and sheet structure come from `/reference/Contact_Center.xlsx`, which is the source of truth. **Open item:** this workbook and `/docs/source_column_map.csv` were not available in the environment when this file was updated, so column lists and types are not yet recorded here. Load them before building bronze DDL or silver mappings.
- Sheet to bronze table:
  - `API_ACD_ALL` → `BRONZE.raw_api_acd_all`
  - `API_CALLLOG` → `BRONZE.raw_api_calllog`
  - `API_CALL_SEGMENTREPORTALL` → `BRONZE.raw_api_call_segment`
  - `API_VOICEMAIL_LIFECYCLE_LOG` → `BRONZE.raw_api_voicemail_lifecycle`
- **ASSUMPTION:** a separate WFM extract → `BRONZE.raw_wfm_daily`. Five9 has no workforce data, so this source must be confirmed with the client before it is built on.
- **Open item:** confirm the source time zone and grain of each sheet from the workbook. Bronze stores UTC (see Time zone).

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
- Bronze tables: prefix `raw_`, named `raw_api_acd_all`, `raw_api_calllog`, `raw_api_call_segment`, `raw_api_voicemail_lifecycle`, and `raw_wfm_daily` (ASSUMPTION). Use these names only.
- Bronze columns are the exception to snake_case: they keep the Five9 source names as received (for example `"MEDIA TYPE"`, `"SERVICE LEVEL"`), written as quoted identifiers. Sanitized snake_case names exist only in silver.
- Silver tables: prefix `stg_` for cleaned staging tables (for example, `SILVER.stg_api_calllog`), or `dim_` / `fact_` where a silver table is a conformed dimension or fact.
- Gold tables: prefix `dim_` for dimensions and `fact_` for facts (for example, `GOLD.fact_contact`, `GOLD.dim_disposition`).
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

These metadata names are fixed. Five9 already has `FILE_NAME` and `LOAD_TIMESTAMP` columns, so those names are never used for metadata. The metadata columns sit beside the Five9 columns and never replace them.

## Layer rules

### Bronze
- Append-only. Never update or delete bronze rows.
- Store raw values as received. Source columns are `VARCHAR` so no coercion happens at ingest; typing happens in silver.
- Keep Five9 column names exactly as received (quoted identifiers). Do not rename.
- Timestamps are UTC.
- **Schema drift:** Five9 adds columns over time. Before loading a file, the procedure compares its header with the table. Any new column is added to the bronze table as `VARCHAR` (quoted, source name) and logged to `CONTROL.schema_drift_log`. The load never fails on a new column. A column that is missing from a file loads as NULL and is logged. Columns are never dropped.
- **Access:** bronze holds Five9 PII as received (see Silver for masking). Restrict `BRONZE` read access to the load role.

### Silver
- Deduplicated, typed, and standardized (trimmed, case-normalized codes, Eastern time, valid domain values).
- Rows that fail validation are written to `stg_<entity>_quarantine` with a `dq_reason_code` column. They do not appear in the clean `stg_` table.
- Deduplication key and record hash are documented per table.
- **Column names:** map each Five9 column to snake_case using `/docs/source_column_map.csv`. Only mapped columns reach silver. Unmapped columns are logged to `CONTROL.schema_drift_log` with action `UNMAPPED_IN_SILVER`.
- **Channel:** `channel` = Five9 `MEDIA TYPE`, trimmed and lower-cased. `web_form` and `secure_message` are not valid channels. Rows with them go to quarantine with reason code `UNSUPPORTED_CHANNEL` (my choice; confirm with the client).
- **Service level:** carry Five9 `SERVICE LEVEL` as received (typed numeric). Carry `SOA MIN/THRESHOLD (SEC)` alongside as `service_level_threshold_sec`. Never recalculate service level from a fixed 20-second threshold, because the threshold varies by skill.
- **Disposition:** carry `DISPOSITION`, `DISPOSITION GROUP A`, `DISPOSITION GROUP B`, `DISPOSITION GROUP C`, and `DISPOSITION PATH` as the disposition hierarchy. There is no `contact_reason_code`.
- **Customer key:** `customer_key = HMAC-SHA256(secret, COALESCE(SALESFORCE_ID, ANI))`. Compute it from raw values before masking, so the key still matches across calls after ANI is masked. The secret comes from an environment variable and is never committed. If both values are null, `customer_key` is null.
- **Repeat contact:** `is_repeat_contact` = TRUE when an earlier call (start strictly earlier, in `_et`) has the same non-null `customer_key` and `DATEDIFF('day', earlier call date_et, this call date_et) <= 7`. Also store `days_since_prior_call`. Calls with a null key are never repeats. This replaces the earlier linked-case rule.
- **PII masking:** mask these Five9 fields to the literal `***` in silver: `ANI`, `CUSTOMER NAME`, `FIRST_NAME`, `LAST_NAME`, `EMAIL`, `STREET`, `ZIP`, `CITY`, `AGENT EMAIL`, `AGENT FIRST NAME`, `AGENT LAST NAME`. Do not reveal them in silver, logs, or quarantine rows. A DQ check counts unmasked values in these columns; the expected count is 0.
- All generated values are synthetic.

### Gold
- Star schema, optimized for analytics: `dim_` tables with surrogate keys, `fact_` tables that reference them, and a stable grain documented per fact.
- Built only from silver. Gold never reads bronze.
- `dim_disposition` holds one row per distinct disposition path: `DISPOSITION`, `DISPOSITION GROUP A`/`B`/`C`, and `DISPOSITION PATH`, with a surrogate key. `fact_contact` references it by key.

## Idempotency (required)

Every load procedure must be safe to re-run:

- Bronze: skip the load if `_batch_id` is already present in the target (checked against `CONTROL.load_run_log` and the target table). Re-running the same batch inserts zero rows.
- Silver and gold: use `MERGE` keyed on the business key (or `_record_hash` where no business key exists). Re-running a batch must not create duplicate rows.
- A re-run is logged as its own run with `rows_inserted = 0`.

## Control and observability (required)

Every load writes to the `CONTROL` schema:

- `CONTROL.load_run_log`: one row per procedure run. Columns include `run_id`, `batch_id`, `layer`, `procedure_name`, `target_table`, `status`, `rows_read`, `rows_inserted`, `rows_updated`, `rows_quarantined`, `started_at_utc`, `ended_at_utc`.
- `CONTROL.dq_result`: one row per data-quality check per run. Columns include `run_id`, `batch_id`, `target_table`, `check_name`, `severity`, `failed_count`, `status` (`PASS` / `FAIL` / `WARN`), `checked_at_utc`.

- `CONTROL.schema_drift_log`: one row per schema change. Columns include `drift_id`, `batch_id`, `source_table`, `source_column`, `sanitized_column`, `action` (`ADDED_TO_BRONZE`, `MISSING_IN_FILE`, `UNMAPPED_IN_SILVER`), `data_type`, `detected_at_utc`.

Procedures return a summary (run_id and status). A run with any `FAIL` DQ check at `ERROR` severity sets `status = 'FAILED'` and stops downstream layers.

## Procedure and SQL conventions

- Idempotent DDL: `CREATE TABLE IF NOT EXISTS` and `CREATE OR REPLACE` only for procedures and views.
- Procedures use `LANGUAGE SQL`, `EXECUTE AS OWNER`, and explicit `BEGIN ... EXCEPTION ... END` blocks that write a `FAILED` row to `CONTROL.load_run_log`.
- Fully qualify every object name (`CC_DEMO.BRONZE.raw_...`).
- Each script is re-runnable on its own.

## Domain (Five9)

- Bronze: the five tables under Source system. Silver and gold objects follow the workbook once it is loaded.
- Silver: `stg_api_acd_all`, `stg_api_calllog`, `stg_api_call_segment`, `stg_api_voicemail_lifecycle`, and `stg_wfm_daily` (ASSUMPTION), each with a `*_quarantine` table.
- Gold: `fact_contact` (grain confirmed from `API_CALLLOG`; open item), `dim_disposition`, `dim_date`, and `fact_agent_day` (ASSUMPTION: built from the WFM extract). `dim_agent` and `dim_queue` follow the workbook once it is loaded.

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

## Changes from v1

1. Source system is now Five9 API exports; column names and types come from `/reference/Contact_Center.xlsx`.
2. Added the four Five9 sheets and the WFM extract as an assumption, labeled wherever it appears.
3. Bronze tables renamed to `raw_api_acd_all`, `raw_api_calllog`, `raw_api_call_segment`, `raw_api_voicemail_lifecycle`, and `raw_wfm_daily`.
4. Bronze keeps Five9 column names as received; silver maps them to snake_case with `/docs/source_column_map.csv`.
5. Channel is Five9 `MEDIA TYPE`. `web_form` and `secure_message` removed from the channel list.
6. Repeat contacts use `customer_key = coalesce(SALESFORCE_ID, ANI)` (hashed), within 7 calendar days. Replaces the linked-case rule.
7. Service level uses Five9 `SERVICE LEVEL` as received. No recalculation at a fixed 20 seconds; threshold from `SOA MIN/THRESHOLD (SEC)`, which varies by skill.
8. `contact_reason_code` replaced by the disposition hierarchy (`DISPOSITION`, groups A/B/C, `DISPOSITION PATH`). Gold keeps `dim_disposition`.
9. PII masking in silver for 11 Five9 fields, with a zero-unmasked DQ check.
10. Metadata columns kept as `_batch_id`, `_source_file`, `_load_ts`, `_record_hash`. `FILE_NAME` and `LOAD_TIMESTAMP` stay Five9 columns.
11. Bronze accepts new Five9 columns (added as VARCHAR, never failing), logged to `CONTROL.schema_drift_log`.
12. Idempotency, time zone, and layer rules carry over unchanged.
13. Open items: workbook and column map not yet available; source time zone and grain per sheet to confirm; bronze PII access to restrict.
