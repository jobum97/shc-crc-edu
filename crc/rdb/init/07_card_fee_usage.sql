-- 연회비 부담 고객 상담용 읽기 뷰.
-- 04_views_rls.sql 이후 실행. 회원 범위는 기존 RLS가 결정함.
-- annual_fee는 2027-01-15 시행 예정 상품 자료의 안내값이며
-- 기준일(2026-08-31)의 실제 청구액을 뜻하지 않음.

SET ROLE app_reader;

CREATE VIEW app.card_fee_usage_6m WITH (security_barrier = true) AS
SELECT
    cards.card_ref,
    cards.product_name,
    cards.brand,
    cards.current_status,
    cards.product_effective_date,
    cards.is_product_effective_on_base_date,
    cards.annual_fee AS listed_annual_fee,
    CASE WHEN period.start_date <= scope.base_date
         THEN period.start_date END AS usage_period_start,
    CASE WHEN period.start_date <= scope.base_date
         THEN scope.base_date END AS usage_period_end,
    CASE WHEN period.start_date <= scope.base_date
         THEN coalesce(sum(txn.amount), 0)::bigint
    END AS approved_amount_6m,
    CASE WHEN period.start_date <= scope.base_date
         THEN count(txn.txn_id)::integer
    END AS transaction_count_6m,
    CASE WHEN period.start_date <= scope.base_date THEN
        (extract(year FROM scope.base_date)::integer
         - extract(year FROM period.start_date)::integer) * 12
        + extract(month FROM scope.base_date)::integer
        - extract(month FROM period.start_date)::integer + 1
    END AS covered_month_count,
    CASE WHEN period.start_date <= scope.base_date
         THEN count(DISTINCT date_trunc('month', txn.txn_date))::integer
    END AS active_month_count
FROM app_internal.customer_cards_full AS cards
CROSS JOIN app_internal.request_scope AS scope
CROSS JOIN LATERAL (
    SELECT greatest(
        (date_trunc('month', scope.base_date) - INTERVAL '5 months')::date,
        scope.coverage_start,
        cards.issue_date
    ) AS start_date
) AS period
LEFT JOIN public.card_txn AS txn
    ON txn.card_id = cards.card_id
   AND txn.approval_code = 'APPROVED'
   AND txn.txn_date BETWEEN period.start_date AND scope.base_date
WHERE scope.base_date IS NOT NULL
  AND scope.coverage_start IS NOT NULL
GROUP BY
    cards.card_id,
    cards.card_ref,
    cards.product_name,
    cards.brand,
    cards.current_status,
    cards.product_effective_date,
    cards.is_product_effective_on_base_date,
    cards.annual_fee,
    period.start_date,
    scope.base_date;

COMMENT ON VIEW app.card_fee_usage_6m IS
    '요청 회원의 카드별 최근 6개월 승인 사용액과 자료상 연회비. 실제 청구액 또는 혜택 손익 아님.';
COMMENT ON COLUMN app.card_fee_usage_6m.listed_annual_fee IS
    '상품 자료의 총연회비 안내값. product_effective_date와 함께 해석할 것.';

RESET ROLE;

GRANT SELECT ON app.card_fee_usage_6m TO sql_retriever_user;
