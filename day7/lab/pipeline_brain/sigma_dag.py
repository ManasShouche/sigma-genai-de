from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.python import PythonOperator
from pyspark.sql import SparkSession
import logging
import json

# DAG Configuration
default_args = {
    'owner': 'data-engineering',
   'retries': 2,
   'retry_delay': timedelta(minutes=5),
    'email_on_failure': True,
}

dag = DAG(
    dag_id='sigma_transaction_pipeline',
    default_args=default_args,
    schedule='0 2 * * *',
    start_date=datetime(2024, 1, 1),
    catchup=False,
    sla_miss_callback=lambda context: logging.warning(f"SLA miss for {context['dag'].dag_id} on {context['execution_date']}"),
    tags=['sigma', 'transactions', 'daily'],
    description="Daily Bronze->Silver->Gold pipeline for Sigma DataTech transactions"
)

def log_failure(context):
    dag_id = context['dag'].dag_id
    task_id = context['task'].task_id
    execution_date = context['execution_date']
    logging.error(f"Task failed: dag_id={dag_id}, task_id={task_id}, execution_date={execution_date}, error={context['exception']}")

def extract_bronze(**context):
    """Ingest raw CSVs to Bronze Parquet"""
    spark = SparkSession.builder.appName("Sigma Transaction Pipeline").getOrCreate()
    logging.info(f"Starting extract_bronze task for {context['execution_date']}")
    try:
        # Read CSV files
        df_transactions = spark.read.csv('path/to/transactions.csv', header=True, inferSchema=True)
        df_merchants = spark.read.csv('path/to/merchants.csv', header=True, inferSchema=True)
        
        # Add metadata columns
        df_transactions = df_transactions.withColumn('ingestion_timestamp', current_timestamp()) \
                                         .withColumn('source_file', 'transactions.csv') \
                                        .withColumn('pipeline_run_id', context['execution_date'])
        
        df_merchants = df_merchants.withColumn('ingestion_timestamp', current_timestamp()) \
                                   .withColumn('source_file','merchants.csv') \
                                    .withColumn('pipeline_run_id', context['execution_date'])
        
        # Write as Parquet
        df_transactions.write.mode('overwrite').parquet('path/to/bronze/transactions/date={}'.format(context['execution_date'].strftime('%Y-%m-%d')))
        df_merchants.write.mode('overwrite').parquet('path/to/bronze/merchants/date={}'.format(context['execution_date'].strftime('%Y-%m-%d')))
    except Exception as e:
        raise e
    finally:
        spark.stop()
    logging.info(f"Finished extract_bronze task for {context['execution_date']}")

def transform_silver(**context):
    """Clean, enrich, deduplicate to Silver"""
    spark = SparkSession.builder.appName("Sigma Transaction Pipeline").getOrCreate()
    logging.info(f"Starting transform_silver task for {context['execution_date']}")
    try:
        # Read Bronze layer
        df_transactions = spark.read.parquet('path/to/bronze/transactions/date={}'.format(context['execution_date'].strftime('%Y-%m-%d')))
        df_merchants = spark.read.parquet('path/to/bronze/merchants/date={}'.format(context['execution_date'].strftime('%Y-%m-%d')))
        
        # Cast columns to correct types
        df_transactions = df_transactions.withColumn('amount', df_transactions['amount'].cast('float')) \
                                        .withColumn('transaction_date', df_transactions['transaction_date'].cast('date'))
        
        # Filter records
        df_transactions = df_transactions.filter((df_transactions['transaction_id'].isNotNull()) & (df_transactions['amount'] >= 0))
        
        # Deduplicate
        df_transactions = df_transactions.dropDuplicates(['transaction_id', 'ingestion_timestamp'], ['ingestion_timestamp'])
        
        # Enrich with merchant data
        df_transactions = df_transactions.join(df_merchants, df_transactions['merchant_id'] == df_merchants['merchant_id'], 'left_outer') \
                                        .withColumn('merchant_name', df_merchants['merchant_name']) \
                                       .withColumn('category', df_merchants['category']) \
                                       .withColumn('city', df_merchants['city'])
        
        # Add quality flag
        df_transactions = df_transactions.withColumn('quality_flag', when(col('merchant_name').isNull(), 'UNMATCHED').otherwise('MATCHED'))
        
        # Write as Parquet
        df_transactions.write.mode('overwrite').parquet('path/to/silver/transactions/date={}'.format(context['execution_date'].strftime('%Y-%m-%d')))
    except Exception as e:
        raise e
    finally:
        spark.stop()
    logging.info(f"Finished transform_silver task for {context['execution_date']}")

def build_gold(**context):
    """Generate the 3 Gold aggregation tables"""
    spark = SparkSession.builder.appName("Sigma Transaction Pipeline").getOrCreate()
    logging.info(f"Starting build_gold task for {context['execution_date']}")
    try:
        # Read Silver layer
        df_transactions = spark.read.parquet('path/to/silver/transactions/date={}'.format(context['execution_date'].strftime('%Y-%m-%d')))
        
        # Table 1 -- merchant_performance
        df_merchant_performance = df_transactions.groupBy('merchant_id','merchant_name', 'category', 'city', 'date') \
                                                  .agg({'amount': 'sum' if'status' == 'COMPLETED' else'sum', 'transaction_id': 'count'}) \
                                                 .withColumnRenamed('sum(amount)', 'total_revenue') \
                                                 .withColumnRenamed('count(transaction_id)', 'txn_count')
        
        # Table 2 -- customer_ltv
        df_customer_ltv = df_transactions.groupBy('customer_id') \
                                          .agg({'amount': 'sum', 'transaction_id': 'count','status': 'first', 'transaction_date':'min', 'transaction_date':'max'}) \
                                         .withColumnRenamed('sum(amount)', 'total_spent') \
                                         .withColumnRenamed('count(transaction_id)', 'total_txns') \
                                         .withColumnRenamed('avg(amount)', 'avg_txn_value') \
                                         .withColumnRenamed('first(status)', 'first_txn_date') \
                                         .withColumnRenamed('max(transaction_date)', 'last_txn_date')
        
        # Table 3 -- daily_summary
        df_daily_summary = df_transactions.groupBy('date') \
                                          .agg({'amount':'sum' if'status' == 'COMPLETED' else'sum', 'transaction_id': 'count', 'customer_id': 'countDistinct','merchant_id': 'countDistinct'}) \
                                          .withColumn('failure_rate_pct', (count('status') == 'FAILED' / count('*')) * 100) \
                                         .withColumnRenamed('sum(amount)', 'total_revenue') \
                                         .withColumnRenamed('count(transaction_id)', 'total_txns') \
                                          .withColumnRenamed('countDistinct(customer_id)', 'unique_customers') \
                                         .withColumnRenamed('countDistinct(merchant_id)', 'unique_merchants')
        
        # Write as Parquet
        df_merchant_performance.write.mode('overwrite').parquet('path/to/gold/merchant_performance/date={}'.format(context['execution_date'].strftime('%Y-%m-%d')))
        df_customer_ltv.write.mode('overwrite').parquet('path/to/gold/customer_ltv/date={}'.format(context['execution_date'].strftime('%Y-%m-%d')))
        df_daily_summary.write.mode('overwrite').parquet('path/to/gold/daily_summary/date={}'.format(context['execution_date'].strftime('%Y-%m-%d')))
    except Exception as e:
        raise e
    finally:
        spark.stop()
    logging.info(f"Finished build_gold task for {context['execution_date']}")

# Define tasks
extract_bronze_task = PythonOperator(
    task_id='extract_bronze',
    python_callable=extract_bronze,
    on_failure_callback=log_failure,
    dag=dag,
)

transform_silver_task = PythonOperator(
    task_id='transform_silver',
    python_callable=transform_silver,
    on_failure_callback=log_failure,
    dag=dag,
)

build_gold_task = PythonOperator(
    task_id='build_gold',
    python_callable=build_gold,
    on_failure_callback=log_failure,
    dag=dag,
)

# Set task dependencies
extract_bronze_task >> transform_silver_task >> build_gold_task
