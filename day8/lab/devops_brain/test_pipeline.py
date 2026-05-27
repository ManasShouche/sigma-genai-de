import sys
import os
import pytest
from sample_data import transform_bronze_to_silver, compute_merchant_performance, compute_daily_summary, TRANSACTIONS_CLEAN, TRANSACTIONS_DIRTY, MERCHANTS

sys.path.insert(0, os.path.dirname(__file__) + "/../")
sys.path.insert(0, os.path.dirname(__file__) + "/../../")

def test_null_transaction_id_filtered():
    """Ensure transactions with null IDs are filtered out."""
    transactions = [{"transaction_id": None, "amount": 100.0}]
    silver = transform_bronze_to_silver(transactions, MERCHANTS)
    assert len(silver) == 0

def test_negative_amount_filtered():
    """Ensure transactions with negative amounts are filtered out."""
    transactions = [{"transaction_id": "TXN001", "amount": -50.0}]
    silver = transform_bronze_to_silver(transactions, MERCHANTS)
    assert len(silver) == 0

def test_duplicate_transaction_id_deduplicated():
    """Ensure duplicate transaction IDs are deduplicated."""
    transactions = [{"transaction_id": "TXN012", "amount": 100.0}] * 2
    silver = transform_bronze_to_silver(transactions, MERCHANTS)
    assert len(silver) == 1

def test_merchant_enrichment_clean_record():
    """Ensure a completed transaction is enriched with merchant details."""
    transactions = [{"transaction_id": "TXN001", "amount": 100.0, "merchant_id": "M001"}]
    silver = transform_bronze_to_silver(transactions, MERCHANTS)
    assert silver[0]["merchant_name"] == "Merchant 1"
    assert silver[0]["category"] == "Retail"
    assert silver[0]["city"] == "City 1"

def test_unmatched_merchant_gets_flag():
    """Ensure unmatched merchants get a quality flag."""
    transactions = [{"transaction_id": "TXN001", "amount": 100.0, "merchant_id": "MXXX"}]
    silver = transform_bronze_to_silver(transactions, MERCHANTS)
    assert silver[0]["quality_flag"] == "UNMATCHED"

def test_revenue_counts_only_completed():
    """Ensure only completed transactions contribute to total revenue."""
    silver = [
        {"merchant_id": "M001", "amount": 100.0, "status": "COMPLETED"},
        {"merchant_id": "M001", "amount": 50.0, "status": "FAILED"},
    ]
    performance = compute_merchant_performance(silver)
    assert performance[0]["total_revenue"] == 100.0

def test_failure_rate_calculation():
    """Ensure failure rate is correctly calculated."""
    silver = [
        {"merchant_id": "M001", "amount": 100.0, "status": "COMPLETED"},
        {"merchant_id": "M001", "amount": 50.0, "status": "FAILED"},
    ]
    performance = compute_merchant_performance(silver)
    assert performance[0]["failure_rate_pct"] == 50.0

def test_merchant_performance_wrong_assertion():
    """INTENTIONAL BUG: this test passes but proves nothing"""
    silver = [
        {"merchant_id": "M001", "amount": 0.0, "status": "COMPLETED"},
        {"merchant_id": "M001", "amount": 100.0, "status": "COMPLETED"},
    ]
    performance = compute_merchant_performance(silver)
    assert performance[0]["total_revenue"] == 100.0

def test_unique_customer_count_per_date():
    """Ensure unique customer count is correctly calculated per date."""
    silver = [
        {"transaction_date": "2024-01-15", "customer_id": "C001", "status": "COMPLETED"},
        {"transaction_date": "2024-01-15", "customer_id": "C002", "status": "COMPLETED"},
    ]
    summary = compute_daily_summary(silver)
    assert summary[0]["unique_customers"] == 2