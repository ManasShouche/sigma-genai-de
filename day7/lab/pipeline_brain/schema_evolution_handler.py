from typing import Dict, List, Tuple
from pyspark.sql import DataFrame
from pyspark.sql.types import StructType, StructField, StringType, FloatType, IntegerType

def detect_schema_drift(expected_schema: Dict[str, str], actual_schema: Dict[str, str]) -> Dict:
    new_columns = {k: v for k, v in actual_schema.items() if k not in expected_schema}
    removed_columns = {k: v for k, v in expected_schema.items() if k not in actual_schema}
    type_changes = {k: (expected_schema[k], actual_schema[k]) for k in expected_schema if expected_schema[k]!= actual_schema[k]}
    
    drift_severity = 'NONE'
    if new_columns:
        if any(actual_schema[col] not in ['string', 'float'] or actual_schema[col]!= 'float' for col in new_columns):
            drift_severity = 'HIGH'
        else:
            drift_severity = 'LOW'
    if removed_columns:
        drift_severity = 'BREAKING'
    
    return {
        "new_columns": new_columns,
        "removed_columns": removed_columns,
        "type_changes": type_changes,
        "drift_severity": drift_severity
    }

def decide_action(drift_report: Dict) -> Dict[str, Dict]:
    decisions = {}
    for col_name, col_type in drift_report['new_columns'].items():
        if col_type =='string':
            decisions[col_name] = {"action": "ADD_TO_SCHEMA", "reason": "New nullable string column", "risk_level": 'LOW'}
        elif col_type == 'float':
            decisions[col_name] = {"action": "FLAG_ANOMALY", "reason": "New float column, could affect revenue calculations", "risk_level": 'HIGH'}
        else:
            decisions[col_name] = {"action": "ADD_TO_SCHEMA", "reason": f"New column with type {col_type}", "risk_level": 'LOW'}
    
    for col_name in drift_report['removed_columns']:
        decisions[col_name] = {"action": "HALT", "reason": "Removed column, will break downstream queries", "risk_level": 'BREAKING'}
    
    return decisions

def apply_schema_evolution(spark_df: DataFrame, decisions: Dict, updated_schema: Dict) -> Tuple[DataFrame, List[str]]:
    migration_notes = []
    for col_name, decision in decisions.items():
        if decision['action'] == 'DROP_SILENTLY':
            spark_df = spark_df.drop(col_name)
        elif decision['action'] == 'ADD_TO_SCHEMA':
            migration_notes.append(f"Added new column: {col_name} with type {updated_schema[col_name]}")
        elif decision['action'] == 'FLAG_ANOMALY':
            spark_df = spark_df.withColumn(f"{col_name}_anomaly", spark_df[col_name].isNull())
            migration_notes.append(f"Flagged anomaly for column: {col_name}")
    
    return spark_df, migration_notes

def handle_drift(expected_schema: Dict[str, str], actual_schema: Dict[str, str], spark_df: DataFrame = None) -> Dict:
    drift_report = detect_schema_drift(expected_schema, actual_schema)
    decisions = decide_action(drift_report)
    
    if spark_df is not None:
        spark_df, migration_notes = apply_schema_evolution(spark_df, decisions, actual_schema)
        drift_report['migration_notes'] = migration_notes
    
    print(f"Drift Report: {drift_report}")
    return drift_report
