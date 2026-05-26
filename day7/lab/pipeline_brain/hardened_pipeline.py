import shutil
import logging
import json
import os
from datetime import datetime
from pyspark.sql import SparkSession
from pyspark.sql.functions import col, lit, broadcast, when, sum, count, max, coalesce, avg, collect_set, collect_list, first, last, mode
from pyspark.sql.types import StringType, FloatType, DateType, IntegerType

logging.basicConfig(level=logging.INFO)

def ingest_bronze(spark, input_path, output_path, run_date, run_id):
    try:
        logging.info("Starting ingest_bronze stage")
        transactions_df = (spark.read.option("header", "true")
                          .option("inferSchema", "false")
                           .csv(input_path + "/transactions.csv")
                          .withColumn("ingestion_timestamp", lit(run_date))
                          .withColumn("source_file", lit("transactions.csv"))
                           .withColumn("pipeline_run_id", lit(run_id)))

        merchants_df = (spark.read.option("header", "true")
                        .option("inferSchema", "false")
                        .csv(input_path + "/merchants.csv")
                        .withColumn("ingestion_timestamp", lit(run_date))
                        .withColumn("source_file", lit("merchants.csv"))
                       .withColumn("pipeline_run_id", lit(run_id)))

        transactions_partition_path = f"{output_path}/bronze/transactions/date={run_date}"
        merchants_partition_path = f"{output_path}/bronze/merchants/date={run_date}"

        shutil.rmtree(transactions_partition_path, ignore_errors=True)
        shutil.rmtree(merchants_partition_path, ignore_errors=True)

        transactions_df.write.mode("overwrite").parquet(transactions_partition_path)
        merchants_df.write.mode("overwrite").parquet(merchants_partition_path)

        logging.info(f"[Stage: ingest_bronze] transactions_count: {transactions_df.count():,} rows")
        logging.info(f"[Stage: ingest_bronze] merchants_count: {merchants_df.count():,} rows")
    except Exception as e:
        logging.error(f"Error in ingest_bronze stage: {e}")
        raise

def transform_silver(spark, bronze_path, merchants_path, output_path, run_date):
    try:
        logging.info("Starting transform_silver stage")
        transactions_df = (spark.read.parquet(bronze_path + "/transactions/date=" + run_date)
                           .withColumn("transaction_date", col("transaction_date").cast(DateType()))
                          .withColumn("amount", col("amount").cast(FloatType())))

        merchants_df = (spark.read.parquet(merchants_path + "/merchants/date=" + run_date)
                        .withColumn("merchant_id", col("merchant_id").cast(StringType())))

        merchants_df = merchants_df.cache()

        filtered_df = transactions_df.filter((col("transaction_id").isNotNull()) & (col("amount") >= 0))
        logging.info(f"[Stage: transform_silver] after_filter_count: {filtered_df.count():,} rows")

        deduped_df = (filtered_df.withColumn("row_number",
                                              when(col("transaction_id").isNotNull(),
                                                   (col("ingestion_timestamp").cast("long") -
                                                    col("transaction_date").cast("long") + 1).cast("long")))
                         .withColumn("row_number",
                                      (col("row_number") + col("transaction_id").cast("long").cast("long")).cast("long"))
                         .orderBy(col("transaction_id"), col("row_number").desc())
                         .dropDuplicates(["transaction_id"])
                          .drop("row_number"))

        logging.info(f"[Stage: transform_silver] after_dedup_count: {deduped_df.count():,} rows")

        enriched_df = (deduped_df.join(broadcast(merchants_df), deduped_df.col("merchant_id") == merchants_df.col("merchant_id"), "left_outer")
                       .withColumn("quality_flag",
                                   when(col("merchant_id").isNull(), "UNMATCHED").otherwise("CLEAN")))

        silver_partition_path = f"{output_path}/silver/transactions/date={run_date}"
        shutil.rmtree(silver_partition_path, ignore_errors=True)
        enriched_df.write.mode("overwrite").parquet(silver_partition_path)

        logging.info(f"[Stage: transform_silver] output_count: {enriched_df.count():,} rows")
    except Exception as e:
        logging.error(f"Error in transform_silver stage: {e}")
        raise

def build_merchant_performance(spark, silver_path, output_path, run_date):
    try:
        logging.info("Starting build_merchant_performance stage")
        silver_df = spark.read.parquet(silver_path).where(col("date") == run_date)

        merchant_performance_df = silver_df.groupBy("merchant_id", "merchant_name", "category", "city", "date") \
           .agg(
                sum(when(col("status") == "COMPLETED", col("amount")).otherwise(0)).alias("total_revenue"),
                count("*").alias("txn_count"),
                (count(when(col("status") == "FAILED", 1)) / count("*") * 100).alias("failure_rate_pct")
            )

        merchant_performance_partition_path = f"{output_path}/merchant_performance/date={run_date}"
        shutil.rmtree(merchant_performance_partition_path, ignore_errors=True)
        merchant_performance_df.write.mode("overwrite").parquet(merchant_performance_partition_path)

        logging.info(f"[Stage: build_merchant_performance] output_count: {merchant_performance_df.count():,} rows")
    except Exception as e:
        logging.error(f"Error in build_merchant_performance stage: {e}")
        raise

def build_customer_ltv(spark, silver_path, output_path):
    try:
        logging.info("Starting build_customer_ltv stage")
        silver_df = spark.read.parquet(silver_path)

        customer_ltv_df = silver_df.groupBy("customer_id") \
           .agg(
                sum(when(col("status") == "COMPLETED", col("amount")).otherwise(0)).alias("total_spent"),
                count("*").alias("total_txns"),
                avg(col("amount")).alias("avg_txn_value"),
                first("transaction_date").alias("first_txn_date"),
                last("transaction_date").alias("last_txn_date"),
                mode("payment_method").alias("preferred_payment_method")
            )

        customer_ltv_partition_path = f"{output_path}/customer_ltv"
        shutil.rmtree(customer_ltv_partition_path, ignore_errors=True)
        customer_ltv_df.write.mode("overwrite").parquet(customer_ltv_partition_path)

        logging.info(f"[Stage: build_customer_ltv] output_count: {customer_ltv_df.count():,} rows")
    except Exception as e:
        logging.error(f"Error in build_customer_ltv stage: {e}")
        raise

def build_daily_summary(spark, silver_path, output_path, run_date):
    try:
        logging.info("Starting build_daily_summary stage")
        silver_df = spark.read.parquet(silver_path).where(col("date") == run_date)

        daily_summary_df = silver_df.groupBy("date") \
           .agg(
                sum(when(col("status") == "COMPLETED", col("amount")).otherwise(0)).alias("total_revenue"),
                count("*").alias("total_txns"),
                count(distinct("customer_id")).alias("unique_customers"),
                count(distinct("merchant_id")).alias("unique_merchants"),
                (count(when(col("status") == "FAILED", 1)) / count("*") * 100).alias("failure_rate_pct")
            )

        daily_summary_partition_path = f"{output_path}/daily_summary/date={run_date}"
        shutil.rmtree(daily_summary_partition_path, ignore_errors=True)
        daily_summary_df.write.mode("overwrite").parquet(daily_summary_partition_path)

        logging.info(f"[Stage: build_daily_summary] output_count: {daily_summary_df.count():,} rows")
    except Exception as e:
        logging.error(f"Error in build_daily_summary stage: {e}")
        raise

def run_gold(spark, silver_path, gold_output_dir, run_date):
    try:
        logging.info("Starting run_gold stage")
        build_merchant_performance(spark, silver_path, gold_output_dir, run_date)
        build_customer_ltv(spark, silver_path, gold_output_dir)
        build_daily_summary(spark, silver_path, gold_output_dir, run_date)

        run_metadata = {
            "run_date": run_date,
            "silver_path": silver_path,
            "gold_output_dir": gold_output_dir,
            "status": "success"
        }
        spark.sparkContext.parallelize([run_metadata]).write.mode("overwrite").json(gold_output_dir + "/run_metadata")
    except Exception as e:
        logging.error(f"Error in run_gold stage: {e}")
        raise

def main(spark, input_path, output_path, run_date, run_id):
    try:
        started_at = datetime.now().isoformat()
        logging.info(f"Pipeline started at: {started_at}")

        row_counts = {}

        ingest_bronze(spark, input_path, output_path, run_date, run_id)
        transform_silver(spark, output_path + "/bronze/transactions", output_path + "/bronze/merchants", output_path + "/silver", run_date)

        silver_df = spark.read.parquet(output_path + "/silver/transactions/date=" + run_date)
        row_counts["input_count"] = silver_df.count()

        run_gold(spark, output_path + "/silver/transactions", output_path + "/gold", run_date)

        completed_at = datetime.now().isoformat()
        logging.info(f"Pipeline completed at: {completed_at}")

        run_metadata = {
            "pipeline_name": "Sigma DataTech Transaction Analytics Pipeline",
            "run_date": run_date,
            "run_id": run_id,
            "run_status": "SUCCESS",
            "started_at": started_at,
            "completed_at": completed_at,
            "row_counts": row_counts
        }

        with open(output_path + f"/run_metadata_{run_date}.json", "w") as outfile:
            json.dump(run_metadata, outfile)
    except Exception as e:
        completed_at = datetime.now().isoformat()
        logging.error(f"Pipeline failed at: {completed_at} with error: {e}")

        run_metadata = {
            "pipeline_name": "Sigma DataTech Transaction Analytics Pipeline",
            "run_date": run_date,
            "run_id": run_id,
            "run_status": "FAILED",
            "error_message": str(e),
            "started_at": started_at,
            "completed_at": completed_at
        }

        with open(output_path + f"/run_metadata_{run_date}.json", "w") as outfile:
            json.dump(run_metadata, outfile)

if __name__ == "__main__":
    spark = (SparkSession.builder
            .appName("Sigma DataTech Transaction Analytics Pipeline")
             .getOrCreate())

    input_path = "s3://your-bucket/bronze"
    output_path = "s3://your-bucket/silver"
    run_date = "2026-05-27"
    run_id = "run-001"

    main(spark, input_path, output_path, run_date, run_id)
