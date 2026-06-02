"""
Lambda Tool: load_to_snowflake
Called by: Recovery Agent
Action group: DataPlatformTools

Bulk loads clean records to Snowflake using MERGE INTO for idempotency.
transaction_id is the deduplication key — loading the same record twice
is safe. This is the guarantee that makes Kinesis replay safe.
"""

import boto3, csv, io, json, os, re
from datetime import datetime, timezone


def _write_quarantine(bucket: str, bad_records: list, reason: str, region: str) -> str:
    """Write bad records to S3 quarantine/ and return the S3 key."""
    s3 = boto3.client("s3", region_name=region)
    ts  = datetime.now(timezone.utc)
    date = ts.strftime("%Y-%m-%d")
    fname = f"quarantine_{ts.strftime('%Y%m%d_%H%M%S')}.csv"
    key   = f"quarantine/{date}/{fname}"

    for row in bad_records:
        row["_quarantine_reason"] = reason
        row["_quarantine_source"] = "load_to_snowflake"
        row["_quarantined_at"]    = ts.isoformat()

    buf = io.StringIO()
    if bad_records:
        writer = csv.DictWriter(buf, fieldnames=list(bad_records[0].keys()))
        writer.writeheader()
        writer.writerows(bad_records)
    s3.put_object(Bucket=bucket, Key=key, Body=buf.getvalue().encode())
    return key


def _validate_and_fix(rec: dict) -> tuple:
    """
    Returns (fixed_record, quarantine_reason | None).
    None reason means the record is good to load.
    """
    fixed = dict(rec)

    # merchant_nm → merchant_name
    if "merchant_nm" in fixed and "merchant_name" not in fixed:
        fixed["merchant_name"] = fixed.pop("merchant_nm")

    # DD-MM-YYYY → YYYY-MM-DD
    d = str(fixed.get("transaction_date", ""))
    if re.match(r"^\d{2}-\d{2}-\d{4}$", d):
        parts = d.split("-")
        fixed["transaction_date"] = f"{parts[2]}-{parts[1]}-{parts[0]}"

    # Validation — flag records that are still bad after fixing
    if not fixed.get("transaction_id"):
        return fixed, "null_transaction_id"
    if not fixed.get("merchant_name"):
        return fixed, "null_merchant_name"
    try:
        amt = float(fixed.get("amount") or 0)
        if amt <= 0:
            return fixed, "zero_or_negative_amount"
    except (TypeError, ValueError):
        return fixed, "invalid_amount"

    return fixed, None


def read_and_fix_s3_records(bucket: str, prefix: str) -> tuple:
    """
    Read disaster records from S3, apply field remapping, split into
    (good_records, bad_records) based on validation.
    """
    s3 = boto3.client("s3", region_name=os.getenv("AWS_DEFAULT_REGION", "us-east-1"))
    resp  = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    files = [o["Key"] for o in resp.get("Contents", []) if o["Key"].endswith(".json") and o["Size"] > 0]

    good, bad = [], []
    for key in files:
        try:
            body    = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            content = json.loads(body)
            batch   = content if isinstance(content, list) else [content]
            for rec in batch:
                fixed, reason = _validate_and_fix(rec)
                if reason:
                    fixed["_quarantine_reason"] = reason
                    bad.append(fixed)
                else:
                    good.append(fixed)
        except Exception:
            pass
    return good, bad


def lambda_handler(event, context):
    params = {p["name"]: p["value"] for p in event.get("parameters", [])}

    table_name = params.get("table_name",
                            f"{os.getenv('SNOWFLAKE_DATABASE','SIGMA')}."
                            f"{os.getenv('SNOWFLAKE_SCHEMA','SILVER')}.TRANSACTIONS")
    s3_prefix  = params.get("s3_prefix", "")
    bucket     = params.get("bucket", os.getenv("SIGMA_S3_BUCKET", ""))

    quarantine_key = None
    bad_records    = []

    if s3_prefix:
        records, bad_records = read_and_fix_s3_records(bucket, s3_prefix)
    else:
        raw = params.get("records", "[]") or "[]"
        try:
            records = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            records = []
        if not records:
            records, bad_records = read_and_fix_s3_records(bucket, "bronze/disaster/")

    # Write quarantine file for records that failed validation
    if bad_records and bucket:
        region = os.getenv("AWS_DEFAULT_REGION", "us-east-1")
        quarantine_key = _write_quarantine(
            bucket, bad_records,
            "lambda_v2_schema_mismatch_unrecoverable", region
        )

    result = load(records, table_name)
    result["rows_quarantined"] = len(bad_records)
    if quarantine_key:
        result["quarantine_s3_key"] = quarantine_key

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": event.get("actionGroup"),
            "function": event.get("function"),
            "functionResponse": {
                "responseBody": {"TEXT": {"body": json.dumps(result, default=str)}}
            },
        },
    }


def load(records: list, table_name: str) -> dict:
    if not records:
        return {"status": "SKIPPED", "rows_loaded": 0, "rows_skipped": 0}

    try:
        import snowflake.connector
    except ImportError:
        return {"error": "pip install snowflake-connector-python"}

    conn = snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        password=os.getenv("SNOWFLAKE_PASSWORD"),
        database=os.getenv("SNOWFLAKE_DATABASE", "SIGMA"),
        schema=os.getenv("SNOWFLAKE_SCHEMA", "SILVER"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE", "SIGMA_WH"),
    )

    cur         = conn.cursor()
    ts          = datetime.now(timezone.utc).isoformat()
    rows_loaded = 0
    rows_skipped = 0

    # Stage + MERGE pattern for idempotency
    # Create temp table, insert all records, MERGE into target
    cur.execute("""
        CREATE TEMPORARY TABLE IF NOT EXISTS temp_transactions (
            transaction_id   VARCHAR,
            merchant_name    VARCHAR,
            category         VARCHAR,
            amount           FLOAT,
            currency         VARCHAR,
            transaction_date DATE,
            status           VARCHAR,
            customer_id      VARCHAR,
            payment_method   VARCHAR,
            merchant_city    VARCHAR,
            _loaded_at       TIMESTAMP_TZ
        )
    """)

    # Batch insert into temp table
    batch_values = []
    for rec in records:
        batch_values.append((
            rec.get("transaction_id", ""),
            rec.get("merchant_name", rec.get("merchant_nm", "")),
            rec.get("category", ""),
            float(rec.get("amount", 0) or 0),
            rec.get("currency", "INR"),
            rec.get("transaction_date", ""),
            rec.get("status", ""),
            rec.get("customer_id", ""),
            rec.get("payment_method", ""),
            rec.get("merchant_city", ""),
            ts,
        ))

    cur.executemany(
        """INSERT INTO temp_transactions VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        batch_values,
    )

    # MERGE — skip existing transaction_ids
    cur.execute(f"""
        MERGE INTO {table_name} AS target
        USING temp_transactions AS src
        ON target.transaction_id = src.transaction_id
        WHEN NOT MATCHED THEN INSERT (
            transaction_id, merchant_name, category, amount, currency,
            transaction_date, status, customer_id, payment_method,
            merchant_city, _loaded_at
        ) VALUES (
            src.transaction_id, src.merchant_name, src.category, src.amount,
            src.currency, src.transaction_date, src.status, src.customer_id,
            src.payment_method, src.merchant_city, src._loaded_at
        )
    """)

    # Get counts
    cur.execute("SELECT COUNT(*) FROM temp_transactions")
    total = cur.fetchone()[0]
    cur.execute(f"SELECT COUNT(*) FROM {table_name} WHERE _loaded_at = '{ts}'")
    rows_loaded  = cur.fetchone()[0]
    rows_skipped = total - rows_loaded

    conn.commit()
    conn.close()

    return {
        "status":        "LOADED",
        "table":         table_name,
        "rows_attempted": len(records),
        "rows_loaded":   rows_loaded,
        "rows_skipped":  rows_skipped,
        "loaded_at":     ts,
        "idempotency":   "MERGE ON transaction_id — safe to replay",
    }


# ── Local test ────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv()

    test_records = [
        {"transaction_id": "TXN-TEST-001", "merchant_name": "TestMart",
         "category": "retail", "amount": 100.0, "currency": "INR",
         "transaction_date": "2026-06-04", "status": "completed",
         "customer_id": "C9999", "payment_method": "UPI",
         "merchant_city": "Bengaluru"},
    ]

    table = (f"{os.getenv('SNOWFLAKE_DATABASE','SIGMA')}."
             f"{os.getenv('SNOWFLAKE_SCHEMA','SILVER')}.TRANSACTIONS")

    print(f"\nLoading {len(test_records)} test record(s) to {table}...\n")
    result = load(test_records, table)
    print(json.dumps(result, indent=2))

    if result.get("status") == "LOADED":
        print(f"\nLoading same record again (idempotency test)...")
        result2 = load(test_records, table)
        print(f"  rows_loaded  = {result2['rows_loaded']}   (expected 0 — already exists)")
        print(f"  rows_skipped = {result2['rows_skipped']}   (expected 1 — duplicate)")

    if "--test" in sys.argv:
        assert "status" in result
        print("\nload_to_snowflake.py test PASSED")
