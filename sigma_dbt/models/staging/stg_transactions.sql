WITH cleaned_transactions AS (
    SELECT
        transaction_id,
        CAST(amount AS DECIMAL(10, 2)) AS amount,
        status,
        merchant_id,
        customer_id,
        CAST(transaction_date AS DATE) AS transaction_date,
        payment_method,
        CURRENT_TIMESTAMP AS loaded_at
    FROM
        {{ source('sigma_analytics', 'fact_transactions') }}
    WHERE
        merchant_id NOT LIKE 'TEST_%'
)

SELECT
    *
FROM
    cleaned_transactions
