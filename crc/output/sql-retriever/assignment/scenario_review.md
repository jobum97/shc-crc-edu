# 연회비 상담 시나리오·논리 뷰 독립 검토

검토 역할: 법률 분석가 리야  
검토 범위: 데이터 적합성, 조회 의미, 고객 오해와 개인정보 노출 위험  
검토 방법: 저장소의 스키마·초기 데이터·기존 뷰·설계서 직접 대조

## 확인된 사실

- `public.member.segment_id=6`의 정의는 `연회비 부담`임. 근거: `01_schema.sql` 17 ~ 24행  
- `M-6001`, `M-6049`는 상담 이력이 있는 세그먼트 6 회원이며 카드 각 2장 보유.  
  근거: `rdb/README.md` 19 ~ 24행, `02_seed.sql`의 회원·카드 COPY 레코드  
- 두 회원에게 연결된 카드는 `D2-C058` 국내전용, `D2-C062` 해외 A이고 모두 `ACTIVE`임.  
  자료상 총연회비는 각각 22,000원, 35,000원임. 근거: `02_seed.sql` 794 ~ 803, 1894 ~ 1895, 1990 ~ 1991행  
- 거래 기준일은 `2026-08-31`이고 두 상품의 시행일은 `2027-01-15`임.  
  근거: `01_schema.sql` 1 ~ 5행, `02_seed.sql` 665·669행  
- 거래의 `APPROVED`와 `CANCELLED`는 별개 상태이며 기존 `app.monthly_usage`는 승인분만 집계함.  
  근거: `01_schema.sql` 77 ~ 88행, `04_views_rls.sql` 171 ~ 195행  
- `app.customer_cards`는 카드 상태를 노출하지만 `ACTIVE`만으로 제한하지 않음.  
  근거: `04_views_rls.sql` 123 ~ 154행  
- `app.monthly_usage`는 기준월 포함 최근 6개 달을 만들고 거래가 없는 유효 월에는 0원·0건을 반환함.  
  근거: `04_views_rls.sql` 159 ~ 195행

## 제안 시나리오

고객이 보유 카드의 **자료상 연회비**와 최근 승인 사용액을 함께 확인하려는 상담.  
상담사는 `M-6001`과 같은 연회비 부담 고객의 활성 카드별로 다음 항목을 안내함.

1. 카드 상품명·브랜드와 자료상 총연회비  
2. 상품 자료 시행일과 조회 기준일 당시 시행 여부  
3. 관측 가능한 최근 6개 달의 승인 사용액·건수와 실제 관측 기간

`2027-01-15` 시행 자료를 `2026-08-31` 현재 청구 중인 연회비로 표현하면 오류임.  
승인 사용액은 혜택 실적이나 청구금액이 아니므로 연회비 대비 손익·해지 권고의 근거로 사용 불가.  
후속 상담에서는 실제 청구내역, 상품 약관·혜택 조건, 가족카드 규칙을 별도로 확인할 필요 있음.

## 새 논리 뷰 제안

이름 예시: `app.card_fee_usage_6m`  
결과 단위: 요청 회원의 기준일까지 발급된 카드 한 장당 한 행  
집계 범위: 기준월 포함 최근 6개 달의 시작일, `app.coverage_start`, 카드 발급일 중 가장 늦은 날부터  
`app.base_date`까지. 정확한 `coverage_start` 날짜를 적용하여 월중 제공 시작일도 처리 필요

| 출력 컬럼 | 의미·처리 기준 |  
| --- | --- |  
| `card_ref` | 요청 회원 범위에서만 생성한 표시용 카드 번호. 원본 `card_id` 비노출 |  
| `product_name`, `brand`, `current_status` | 카드 식별 보조 및 활성 상태 필터용 |  
| `product_effective_date` | 연회비 출처 상품 자료의 시행일 |  
| `is_product_effective_on_base_date` | 시행일이 기준일 이전 또는 같은 날인지 여부 |  
| `listed_annual_fee` | `(product_id, brand)`로 찾은 자료상 총연회비. 실제 청구액과 구별 |  
| `usage_period_start`, `usage_period_end` | 승인 거래 관측 구간. 유효 구간 없으면 NULL |  
| `approved_amount_6m`, `approved_txn_count_6m` | 관측 구간 내 `APPROVED`만 집계 |

월별 뷰를 다시 합치는 구현은 기본 `coverage_start=2025-08-01`에서는 합리적임.  
다만 월별 뷰가 제공 시작월을 **월초로 확장**하므로 `coverage_start`가 월중이면 실제 제공 이전 거래가 섞일 수 있음.  
새 뷰는 `app_internal.customer_cards_full`과 원본 `card_txn`을 카드 단위로 직접 집계하는 편이 정확함.  
원본 접근은 `app_reader` 소유자 권한과 기존 RLS 정책을 통해서만 이뤄져야 함.

관측 가능한 날짜 구간이 있으나 승인 거래가 없으면 `0원·0건`으로 반환.  
`coverage_start > base_date` 등으로 관측 구간 자체가 없으면 금액·건수·기간을 `NULL`로 반환.  
두 경우를 같은 `0`으로 처리하면 미제공 자료를 사용 없음으로 오해할 위험 있음.

## 정형쿼리 Q-08 제안

```sql  
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
    approved_txn_count_6m  
FROM app.card_fee_usage_6m  
WHERE current_status = 'ACTIVE'  
ORDER BY listed_annual_fee DESC, card_ref  
LIMIT 200;  
```

질문 예시: "보유 중인 카드별 자료상 연회비와 최근 6개월 승인 사용액을 알려줘."  
응답 예시 설명: "연회비 자료는 2027-01-15 시행 예정이며 2026-08-31 현재 청구액을 뜻하지 않음."  
금액 비율·절약 예상액·해지 순위는 계산하지 않는 방향 권고.

## 독립 피드백과 수용 기준

| 검토 항목 | 피드백 | 수용 기준 |  
| --- | --- | --- |  
| 시나리오 적합성 | 세그먼트와 카드가 실제 존재하므로 적합 | M-6001 사례의 카드 2건과 연회비 출처 확인 |  
| 시행일 | 미래 시행일 누락 시 오인 가능성 큼 | 시행일·기준일 시행 여부 출력, 고객 설명 문구 포함 |  
| 기간 | 월중 제공 시작일에서 기존 월별 뷰와 차이 가능 | 정확한 `coverage_start` 및 카드 발급일 적용 |  
| 거래 상태 | 취소 건 합산 위험 | `APPROVED`만 집계, 0원과 미관측 구분 |  
| 상태 | 해지·정지 카드를 보유 중인 카드로 안내할 위험 | Q-08에서 `ACTIVE` 필터 |  
| 접근 제어 | 원본 카드 ID·회원 ID 노출 위험 | `card_ref`만 공개, `security_barrier`, `app_reader` 소유, 권한·RLS 확인 |  
| 해석 | 승인액만으로 연회비 부담·혜택 실적 판단 불가 | 자동 해지 권고·손익 계산 제외, 한계 문구 포함 |

현재 검토는 저장소 파일의 정적 확인임. 데이터베이스 실행 결과와 쿼리 성능 검증은 미수행.
