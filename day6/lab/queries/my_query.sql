SELECT a.customer_name,
       a.email,
       a.tier,
       a.city,
       SUM(b.amount) AS s,
       (SELECT COUNT(*)
        FROM fact_transactions x
        WHERE x.customer_id = a.customer_id
          AND x.status = 'FAILED') AS f
FROM dim_customer a, fact_transactions b, dim_merchant c
WHERE a.customer_id = b.customer_id
  AND b.merchant_id = c.merchant_id
  AND c.category = 'Food Delivery'
GROUP BY a.customer_name, a.email, a.tier, a.city, a.customer_id
ORDER BY s DESC;
