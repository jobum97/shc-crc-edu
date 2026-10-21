SELECT
    card_ref,
    product_name,
    brand,
    product_effective_date,
    is_product_effective_on_base_date,
    listed_annual_fee,
    usage_period_start,
    usage_period_end,
    approved_amount_6m,
    transaction_count_6m,
    covered_month_count,
    active_month_count
FROM app.card_fee_usage_6m
WHERE current_status = 'ACTIVE'
ORDER BY listed_annual_fee DESC, approved_amount_6m ASC, card_ref
LIMIT 20;
