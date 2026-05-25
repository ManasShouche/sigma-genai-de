# NL2SQL vs Cortex Analyst — Sigma DataTech Evaluation
Team: Jack Manas
Date: 2026-05-25

## 5-Question Head-to-Head Results

| # | Question | Module 2 SQL Correct? | Cortex SQL Correct? | Module 2 Time | Cortex Time |
|---|----------|--------------------|---------------------|------------|-------------|
| 1 | Total transaction count | YES (extra unrequested date filter, but row range matches all data) | YES | ~7s | ~55.7s |
| 2 | Failed transaction count | YES | YES | ~7s | ~232.1s |
| 3 | Highest revenue merchant | YES (CASE WHEN STATUS='COMPLETED') | YES (WHERE STATUS='COMPLETED') | ~11s | ~110.2s |
| 4 | Failure rate by payment method | YES (returns total, failed, and rate as percentage 0–100) | PARTIAL (returns rate as decimal 0–1, not percentage) | ~7s | ~26.8s |
| 5 | Total revenue (with COMPLETED filter) | YES (CASE WHEN STATUS='COMPLETED'; extra date filter) | YES (WHERE STATUS='COMPLETED') | ~8s | ~36.3s |

## Observations

### Where Module 2 NL2SQL was better:
- **Latency.** Every Module 2 query came back in 7–11 seconds. Cortex ranged from 27s to a 232s outlier on Q2 — roughly 5–30× slower in this run.
- **Failure-rate formatting (Q4).** Module 2 returned `FAILURE_RATE_PCT` on a 0–100 scale (multiplied by 100, rounded to 2 dp) and ordered the result. Cortex returned the raw ratio (0–1) with no ordering — technically the right number but not what a business user would paste into a deck.
- **Result legibility on Q4.** Module 2 also included supporting columns (`TOTAL_TRANSACTIONS`, `FAILED_TRANSACTIONS`) which makes the answer auditable in one query. Cortex returned only the rate.
- **Safety.** Module 2 has an explicit validator that rejected `DROP TABLE fact_transactions` before execution (visible in `nl2sql_audit.json` entry #1). Cortex relies on the role's read-only permissions — defence in depth is weaker.

### Where Cortex Analyst was better:
- **Cleaner, more literal SQL.** Cortex did not invent date filters. Module 2 added `WHERE TRANSACTION_DATE BETWEEN '2024-01-15' AND '2024-01-31'` on Q1, Q3, Q5 even though the user did not ask for a time window. The result is correct only because the sample data happens to fall entirely in that range — on a fuller production dataset this would silently undercount.
- **No prompt to maintain.** The semantic model YAML is declarative. Module 2's `SCHEMA_CONTEXT` is a long string in `sample_data.py` that has to be edited by hand any time a column, rule, or example changes.
- **Bounded surface.** Cortex cannot reference tables that aren't declared in the YAML; Module 2 depends on the LLM staying inside the schema described in the prompt.
- **Data residency.** Cortex never sends data outside Snowflake. Module 2 sends the schema (and could send sample rows) to Bedrock in `us-east-1`.

### Business Rule Accuracy
Question 5 is the critical test — revenue must only count COMPLETED transactions. Both systems applied the rule correctly.
- Module 2: Used `SUM(CASE WHEN STATUS='COMPLETED' THEN AMOUNT ELSE 0 END)` — correct, matches the rule baked into the prompt's `SCHEMA_CONTEXT`.
- Cortex: Used `SUM(AMOUNT) WHERE STATUS = 'COMPLETED'` — correct, matches the `total_revenue` metric defined in the semantic model YAML. Same answer, slightly different shape.

Q3 (highest revenue merchant) tells the same story: both systems filtered to COMPLETED. The metric and verified-query sections of the YAML did their job for Cortex; the prompt's business-rule section did its job for Module 2.

## Your Recommendation

Which approach would you deploy at Sigma DataTech for production self-serve analytics, and why?

Consider:
- Setup effort (Module 2: 200 lines of Python + prompt. Cortex: YAML + API call)
- Maintenance (Module 2: update prompt for new tables. Cortex: update YAML)
- Accuracy (your observed results above)
- Cost (Nova Pro API calls vs Snowflake credit consumption)
- Data residency (Module 2: data leaves Snowflake to Bedrock. Cortex: stays inside Snowflake)
- Scalability (Module 2: you maintain schema context. Cortex: semantic model scales)

Your recommendation: **Cortex Analyst (with a thin safety wrapper)**

Reason: Cortex's declarative semantic model is the right long-term home for schema, joins, metrics and verified queries — it keeps business rules versioned with the data, keeps payloads inside Snowflake (no Bedrock egress), and removes the prompt-engineering toil that Module 2 needs every time a table changes. The latency we observed (27–232s) and the Q4 formatting miss are real downsides, but they're addressable with a small Python wrapper (validator + result post-processing + caching) — and that wrapper is far cheaper to maintain than the 200-line NL2SQL pipeline. Module 2 stays valuable as the safety/validation layer in front of Cortex, not as the primary engine.
