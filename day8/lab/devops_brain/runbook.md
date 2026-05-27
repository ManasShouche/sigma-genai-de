# Pipeline Overview

This pipeline processes transaction data, transforming it into a cleaned and enriched format. It runs to ensure that downstream analytics and reporting have up-to-date, high-quality data. If this pipeline stops, critical business metrics and reports will be outdated, impacting decision-making.

## Pipeline Steps

1. Connect to the DuckDB database using `get_connection()`.
2. Set up necessary tables using `setup_tables()`.
3. Load merchant data into the `merchants` table using `load_merchants()`.
4. Load raw transaction data into the `bronze_transactions` table using `load_bronze()`.
5. Transform raw transactions into enriched transactions and load them into the `silver_transactions` table using `transform_bronze_to_silver()` and `load_silver()`.
6. Compute merchant performance metrics and load them into the `gold_merchant_performance` table using `compute_merchant_performance()` and `load_gold()`.
7. Compute daily summary metrics and load them into the `gold_daily_summary` table using `compute_daily_summary()` and `load_gold()`.

## Schedule / Trigger

This pipeline runs every day at 2 AM UTC. It is triggered by a cron job configured in the cloud infrastructure.

## Failure Modes

1. **DuckDB Connection Failure**
   - **Root Cause:** Database server is down or unreachable.
   - **Symptom:** `get_connection()` throws an exception.
2. **Table Creation Failure**
   - **Root Cause:** Syntax error in SQL or insufficient permissions.
   - **Symptom:** `setup_tables()` throws an exception.
3. **Merchant Data Load Failure**
   - **Root Cause:** Corrupt or missing merchant data.
   - **Symptom:** `load_merchants()` throws an exception.
4. **Bronze Table Load Failure**
   - **Root Cause:** Invalid or malformed transaction data.
   - **Symptom:** `load_bronze()` throws an exception.
5. **Silver Table Transformation Failure**
   - **Root Cause:** Missing merchant mapping for transactions.
   - **Symptom:** `transform_bronze_to_silver()` produces incomplete data.

## Recovery Actions

1. **DuckDB Connection Failure**
   - Check database server status.
   - Restart the database server if necessary.
   - Retry the pipeline.
2. **Table Creation Failure**
   - Review SQL syntax in `setup_tables()`.
   - Ensure the user has sufficient permissions.
   - Retry the pipeline.
3. **Merchant Data Load Failure**
   - Verify the integrity of `MERCHANTS` data.
   - Correct any issues and retry the pipeline.
4. **Bronze Table Load Failure**
   - Inspect `TRANSACTIONS_CLEAN` and `TRANSACTIONS_DIRTY` for issues.
   - Correct any malformed data and retry the pipeline.
5. **Silver Table Transformation Failure**
   - Ensure all merchants in transactions have corresponding entries in `merchants`.
   - Correct any missing data and retry the pipeline.

## Known Bugs

- Hardcoded AWS credentials in the source code.
- Lack of null handling in `transform_bronze_to_silver()`.

## Escalation Contacts

1. **On-call DE:** Priya Nair (priya.nair@sigmadatatech.in, +91-98400-11111)
2. **Tech Lead:** Arjun Mehta (arjun.mehta@sigmadatatech.in)
3. **Platform Manager:** Kavya Reddy (kavya.reddy@sigmadatatech.in)

## Data Quality Checks

- Verify the count of records in `bronze_transactions`, `silver_transactions`, `gold_merchant_performance`, and `gold_daily_summary`.
- Ensure `quality_flag` is set correctly in `silver_transactions`.
- Check for any `NULL` values in critical fields.
- Validate the computed metrics in `gold_merchant_performance` and `gold_daily_summary` against expected values.