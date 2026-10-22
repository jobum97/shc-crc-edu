# SQL 검색기 설계 대조표

상태: 구현 및 단위 시험 완료, 승인된 cardlab 실제 DB와 Groq 통합시험17건 통과  
갱신일: 2026-10-22  
구현과 시험: 아래 파일에 반영됨. 실제 시험 증거는 마지막 표와 test_report.md 참조함.

## 입력과 검토 범위

- 설계 기준: `crc/output/sql-retriever/sql_retriever_설계서_v2.pptx` 62쪽
- 보완 근거: 같은 폴더의 v2 Markdown, decisions.json, db_catalog.json, read_views.py
- 출력 폴더: 사용자 확정 `crc/sql-retriever`
- 금지된 빌드 코드, slides_v2.json과 별도 정형 후보 파일을 이번 개발 근거로 읽지 않음
- PPT 모든 슬라이드의 표와 도형 텍스트를 `design_ppt_text.json`에 추출함
- 지정 build/out의 구본 대신 v2를 `design_png`에 새로 렌더링함
- 4쪽의 고정/생성 분기와 S-R6 오류 합류 방향을 그림으로 확인함
- 새 렌더 환경에서 한글 폰트가 누락됨. 문구는 PPT XML 추출 원문으로 확인함
- 승인 설계 파일을 변경하지 않음

## 용어

- NL2SQL: 자연어 질문을 SQL 조회문으로 바꾸는 방식임
- 정형쿼리: 미리 등록한 SQL을 질문 조건과 정확히 맞을 때 선택하는 방식임
- AST: SQL을 문법 요소별 구조로 풀어 검사하기 위한 구문 트리임
- DIP: 업무 코드가 DB와 모델 구현 대신 포트라는 약속에 의존하도록 하는 원칙임

## 단계별 대조

| 설계 단계와 근거 | 구현 위치 | 시험 근거 |
|---|---|---|
| S-R1, PPT 5~6/56~57, MD 부록 G | application/services.py, domain/privacy.py | test_policies, test_workflow, test_presentation |
| S-R2, PPT 7/43~52, MD B~E | application/services.py, domain/routing.py | test_fixed_flow_never_calls_model, test_model_failure_no_db_or_retry |
| S-R3, PPT 8~9/29~30 | domain/plan_policy.py | tests/test_policies.py |
| S-R4, PPT 10/53~54 | infrastructure/sql_validator.py | tests/test_sql_validator.py |
| S-R5, PPT 11/55 | infrastructure/postgres.py | test_connectors, run_integration.py |
| S-R6, PPT 12/59~61, MD G | application/services.py | test_workflow, test_presentation, run_entrypoint_tests.py |

### 입력과 상태 필드

다음 항목은 MD 부록 G 원문의 계약임. 구현 시 임의로 추가하거나 이름을 바꾸지 않음.

```json
{
  "AuthScope": {
    "principal_id": "인증된 상담 주체의 내부 식별자",
    "authorized_member_id": "인증 계층이 권한을 확인한 회원ID"
  },
  "Config": {
    "metadata": {
      "base_date": "date",
      "coverage_start": "date",
      "loaded_at": "timestamp",
      "freshness_policy": "운영 갱신 주기에 맞춰 설정한 유효성 규칙"
    },
    "schema_catalog": "8개 app 뷰의 컬럼/타입/의미/기간",
    "query_catalog": "Q-ID별 SQL, 조건, 결과 단위",
    "policy": "시간/크기/SQL/정제/프롬프트 버전",
    "policy_version": "sql-retriever-v2-2026-10-21"
  },
  "QuestionSlots": {
    "domain": "profile/cards/usage/delinquency 또는 null",
    "metrics": "승인금액/건수/연회비/연체금액 등 확인된 문자열 배열",
    "period_start": "명시된 요청 시작일 또는 null",
    "period_end": "명시된 요청 종료일 또는 null",
    "grain": "질문이 요구한 단위 또는 null",
    "filters": "허용 컬럼 이름, 비교 연산, 리터럴로 이뤄진 배열",
    "ambiguous": "규칙으로 단일 해석 불가하면 true",
    "intent": "확인된 의도 또는 null",
    "status_filter": "명시된 상태 또는 null. Q-08은 ACTIVE 필수"
  },
  "Context_required_after_S_R1": [
    "request_id",
    "started_at",
    "deadline_at",
    "member_scope",
    "base_date",
    "coverage_start",
    "question_clean",
    "question_slots",
    "schema_catalog",
    "query_catalog",
    "policy",
    "error"
  ],
  "Context_additions": {
    "S-R2": [
      "context.plan"
    ],
    "S-R3": [
      "context.plan_check"
    ],
    "S-R4": [
      "context.validated_sql",
      "context.sql_fingerprint"
    ],
    "S-R5": [
      "context.result"
    ],
    "모든 단계 오류": [
      "context.error"
    ]
  },
  "Error": {
    "code": "오류 코드 문자열",
    "message": "고정 한국어 메시지",
    "stage": "S-R1 ~ S-R6 문자열"
  },
  "QueryResult": {
    "rows": "array<object>",
    "columns": "array<{name:string, db_type:string, unit:string|null, currency:string|null}>",
    "grain": "검증된 결과 단위와 GROUP BY에서 생성한 설명 문자열",
    "period": "{start:string,end:string}|null. 조회 범위이며 카드별 실제 기간은 행의 기간 필드로 전달",
    "completeness": "complete|limit_reached_more_unknown. 행수 < LIMIT이면 전자, 같으면 후자"
  },
  "Response": {
    "request_id": "string",
    "status": "success|error",
    "data": "QueryResult|null",
    "error": "Error|null",
    "meta": {
      "base_date": "string|null",
      "coverage_start": "string|null",
      "route": "fixed|nl2sql|null",
      "query_id": "Q-ID|null",
      "warnings": "array<string>"
    }
  },
  "invariants": [
    "S-R1 오류의 최소 Context는 request_id/started_at/deadline_at/error이며 나머지 공통 필드는 없음 허용",
    "공통 Context 필드는 후속 단계에서 유지하며 LLM에 전체 Context를 보내지 않음",
    "성공 응답 data!=null,error=null. 실패 응답 data=null,error!=null",
    "단계 성공 출력만 해당 필드 필수. 모든 오류는 error를 채워 S-R6에 직접 전달",
    "날짜는 YYYY-MM-DD. 통화 단위 확인 전 currency=null. 기간 없는 조회 period=null",
    "행의 값은 null/boolean/string/number로 직렬화. 정밀도를 잃는 DB 큰 정수와 numeric는 문자열로 반환하고 columns에 타입 명시",
    "Context.plan.route와 Context.plan.query_id는 S-R2 코드가 추가하며 별도 최상위 반환값으로 전달하지 않음",
    "행수 == LIMIT일 때 has_more=true로 단정하지 않음",
    "card_fee_usage_6m 집계 NULL은 관측 기간 미성립이며 다른 View에 일괄 적용 금지",
    "운영 계정 검증은 관리자 카탈로그 조사와 분리함"
  ],
  "Context_transport": "S-R1 ~ S-R5는 Context 1개를 반환하며 S-R6만 Response와 HTTP 상태로 변환함. 추가 필드 모두 Context에 저장함."
}
```

## 단계 처리와 오류

### ② S-R1 검색 요청 접수와 검증

- 단계ID: S-R1
- 단계명: 검색 요청 접수와 검증
- 이전 단계: 트리거
- 비즈니스 목표: 허용된 회원 범위와 질문만 다음 단계로 전달
- 입력 데이터: question(string,필수,질문), auth_scope(AuthScope,필수,인증), config(Config,필수,설정)
- 수행 작업: 1. 시간 초기화 2. 회원 권한 검증 3. 식별자 정제와 잔존 검사 4. 원문 폐기
- 출력 데이터: context(Context,필수,공통 필드와 error:Error|null 포함)
- 담당자 유형: 규칙기반 코드
- 외부시스템: 없음
- 예외 처리: 입력, 권한 또는 정제 실패 시 오류 상태로 S-R6 이동
- 비고: 원문과 회원ID는 외부 전송과 일반 로그 금지

### ② S-R2 정형쿼리 또는 NL2SQL 판단

- 단계ID: S-R2
- 단계명: 정형쿼리 또는 NL2SQL 판단
- 이전 단계: S-R1
- 비즈니스 목표: 고정 조회를 먼저 쓰고 미해당 질문만 SQL 생성
- 입력 데이터: context(Context,필수,S-R1 출력 전체)
- 수행 작업: 의도, 기간, 단위, 지표, 상태가 모두 일치한 단일 Q-ID 선택. 그 외 C-01 또는 명확화
- 출력 데이터: context(Context,필수,이전 필드 + plan:Plan, error 포함)
- 담당자 유형: 규칙기반 코드 + 생성형AI
- 외부시스템: Groq LLM(C-01, NL2SQL 경로만)
- 예외 처리: 시간 초과, 잘못된 JSON, 모호한 질문은 S-R6 이동
- 비고: LLM 최대 1회, 통신 재시도 0회

### ② S-R3 계획 적합성 판정

- 단계ID: S-R3
- 단계명: 계획 적합성 판정
- 이전 단계: S-R2
- 비즈니스 목표: 제공 가능한 기간과 결과 단위에 맞는 조회만 진행
- 입력 데이터: context(Context,필수,S-R2 출력과 plan 포함)
- 수행 작업: 상태와 사유 쌍, 질문 슬롯, View, 기간, 단위와 컬럼 일치 검사
- 출력 데이터: context(Context,필수,이전 필드 + plan_check:boolean, error 포함)
- 담당자 유형: 규칙기반 코드
- 외부시스템: 없음
- 예외 처리: 미지원 기간, 단위 또는 불명확한 조건은 S-R6으로 오류 반환
- 비고: SQL 수정과 LLM 재호출 없음

### ② S-R4 SQL 검증

- 단계ID: S-R4
- 단계명: SQL 검증
- 이전 단계: S-R3
- 비즈니스 목표: 허용된 읽기 전용 SQL만 실행 단계로 전달
- 입력 데이터: context(Context,필수,S-R3 출력과 plan_check 포함)
- 수행 작업: 1. 구문 트리 검사 2. 뷰, 컬럼과 함수 검사 3. SELECT와 LIMIT 검사 4. SQL 고정
- 출력 데이터: context(Context,필수,이전 필드 + validated_sql:string, sql_fingerprint:string, error 포함)
- 담당자 유형: 규칙기반 코드
- 외부시스템: 없음
- 예외 처리: SQL_REJECTED로 S-R6 이동. SQL 재생성 없음
- 비고: 정형 SQL도 같은 검사를 통과해야 실행 가능

### ② S-R5 SQL 실행

- 단계ID: S-R5
- 단계명: SQL 실행
- 이전 단계: S-R4
- 비즈니스 목표: 같은 회원 범위에서 검증된 SQL 결과만 수신
- 입력 데이터: context(Context,필수,S-R4 출력과 validated_sql 포함)
- 수행 작업: 1. 연결 확보 2. 읽기 전용 트랜잭션 3. 세션 변수 3개 설정 4. 제한 시간 내 조회
- 출력 데이터: context(Context,필수,이전 필드 + result:QueryResult, error 포함)
- 담당자 유형: 규칙기반 코드
- 외부시스템: 없음(시스템 내부 DB)
- 예외 처리: 취소와 롤백, 오염 연결 폐기 후 DB 오류를 숨기고 S-R6 이동
- 비고: DB 단계 전체 8초. SQL 실행 3초 이하, 전체 종료 1초 예약

### ② S-R6 응답 구성과 반환

- 단계ID: S-R6
- 단계명: 응답 구성과 반환
- 이전 단계: S-R5 성공 또는 S-R1 ~ S-R5 오류
- 비즈니스 목표: 결과 범위와 한계를 포함한 일관된 응답 반환
- 입력 데이터: context(Context,필수,이전 출력. result:QueryResult 또는 error:Error 포함)
- 수행 작업: 1. 성공/오류 선택 2. 범위와 단위 부착 3. 민감정보 제외 4. 응답 반환
- 출력 데이터: response(Response,필수,응답), http_status(integer,필수,HTTP 상태)
- 담당자 유형: 규칙기반 코드
- 외부시스템: 없음
- 예외 처리: 직렬화 실패 시 최소 오류 응답. 전송 불가 시 연결 종료 기록
- 비고: 종료용 1초 사용. 시작 기준 1.5초 검사 예외

## 오류 코드 대조

| 코드 | HTTP와 단계 | 고정 메시지 |
|---|---|---|
| INVALID_REQUEST | 400 / S-R1 | 질문과 필수 입력을 확인해 주세요 |
| UNAUTHORIZED_SCOPE | 403 / S-R1 | 조회 권한이 없습니다 |
| PRIVACY_REJECTED | 422 / S-R1 | 개인정보를 제외하고 다시 입력해 주세요 |
| CONFIG_UNAVAILABLE | 503 / S-R1 | 조회 기준 정보를 준비하지 못했습니다 |
| AMBIGUOUS_REQUEST | 422 / S-R2, S-R3 | 기간과 항목을 구체적으로 입력해 주세요 |
| UNSUPPORTED_REQUEST | 422 / S-R2, S-R3 | 현재 제공하는 조회 범위를 벗어났습니다 |
| INVALID_MODEL_OUTPUT | 502 / S-R2 | 조회 계획을 만들지 못했습니다 |
| SQL_REJECTED | 422 / S-R4 | 안전하게 실행할 수 없는 조회입니다 |
| UPSTREAM_TIMEOUT | 504 / S-R2 | 조회 계획 생성 시간이 초과되었습니다 |
| UPSTREAM_UNAVAILABLE | 503 / S-R2 | 조회 계획 서비스를 사용할 수 없습니다 |
| TIME_BUDGET_EXCEEDED | 504 / S-R1 ~ S-R5 | 검색 처리 시간이 초과되었습니다 |
| DB_EXECUTION_FAILED | 503 / S-R5 | 조회 중 오류가 발생했습니다 |
| RESULT_TOO_LARGE | 422 / S-R5 | 조회 범위를 줄여 다시 요청해 주세요 |
| RESPONSE_FAILED | 500 / S-R6 | 응답을 구성하지 못했습니다 |

오류는 application/models.py에 한 번 정의하고 단계 및 CLI/API에서 공통 사용함.

## 흐름과 시간

- S-R1 → S-R2 → S-R3 → S-R4 → S-R5 → S-R6, 모든 실패는 S-R6로 합류함
- 고정 경로 모델 호출 0회, 생성 경로 최대 1회, SQL 재생성과 통신 재시도 0회임
- 전체 30초, 종료 1초, 시작 기준 1.5초임
- 단계 상한: 정형 [1,1,1,2,8,1]=14초, 생성 [1,16,1,2,8,1]=29초임
- 검사 A: 30 - 경과 - 종료1 - 미실행 외부15 >= 1.5
- 검사 B: 실제 남은 시간 >= 현재부터 종료까지 남은 단계 상한 합계
- A와 B 모두 통과할 때 진행함. 분기 미정은 큰 경로를 적용함
- S-R6는 예약한 1초를 사용하고 시작 기준을 다시 검사하지 않음
- DB8초: 풀1, 연결1, 세션1, SQL3, 수신1, 정리1초임
- statement_timeout=min(3초, DB 잔여시간-수신1-정리1)임
- 구현: domain/budget.py와 infrastructure/workflow.py, 시험: test_budget와 test_deadlines
- PPT25쪽 6개 계산 예를 그대로 시험 입력과 기대값으로 사용함

## 커넥터와 프롬프트

- 근거: PPT17~23쪽, MD 부록 B~E, decisions.llm_official_research
- Groq / openai/gpt-oss-120b / https://api.groq.com
- POST /openai/v1/chat/completions / AsyncGroq / GROQ_API_KEY
- temperature=0.1, top_p=1, max_completion_tokens=2048, reasoning_effort=low
- include_reasoning=false, stream=false, n=1, json_schema.strict=true
- tools, top_k, reasoning_format 미전송. timeout=15, max_retries=0
- 시스템 전문은 MD 부록 B를 그대로 resources/system_prompt.txt에 추출함
- 응답 스키마와 user prompt는 MD 부록 C~E를 사용함
- 전체 Context 대신 정제 질문, 슬롯, 날짜, 공개 스키마, SQL정책만 전송함
- 구현: infrastructure/groq_gateway.py, 시험: test_connector_payload와 test_live_nl2sql

## 정형쿼리 대조

SQL은 PPT45~52쪽 및 MD 동일 쪽에서 직접 추출함. 별도 후보 파일을 읽지 않음.

### Q-01 / PPT 45쪽

```sql
SELECT
  join_date, age_band
FROM app.customer_profile
LIMIT 1;
```

### Q-02 / PPT 46쪽

```sql
SELECT
  card_ref, product_id, product_name, brand, issue_date, current_status,
  product_effective_date, is_product_effective_on_base_date, annual_fee
FROM app.customer_cards
ORDER BY card_ref
LIMIT 200;
```

### Q-03 / PPT 47쪽

```sql
SELECT
  card_ref, month, period_start, period_end, approved_amount,
  transaction_count
FROM app.monthly_usage
ORDER BY month DESC, card_ref
LIMIT 200;
```

### Q-04 / PPT 48쪽

```sql
SELECT
  base_month, as_of_date, overdue_amount, overdue_days, overdue_count_12m
FROM app.customer_delinquency
ORDER BY base_month DESC
LIMIT 1;
```

### Q-05 / PPT 49쪽

```sql
SELECT
  card_ref, month, period_start, period_end, merchant_name, category,
  first_approved_date, last_approved_date, approved_amount,
  transaction_count
FROM app.merchant_usage
ORDER BY month DESC, approved_amount DESC, card_ref, merchant_name, category
LIMIT 200;
```

### Q-06 / PPT 50쪽

```sql
SELECT
  base_month, as_of_date, overdue_amount, overdue_days, overdue_count_12m
FROM app.customer_delinquency_history
ORDER BY base_month DESC
LIMIT 12;
```

### Q-07 / PPT 51쪽

```sql
SELECT
  usage_date, approved_amount, transaction_count
FROM app.customer_daily_usage
ORDER BY usage_date DESC
LIMIT 90;
```

### Q-08 / PPT 52쪽

```sql
SELECT card_ref, product_name, brand,
  product_effective_date, is_product_effective_on_base_date,
  listed_annual_fee, usage_period_start, usage_period_end,
  approved_amount_6m, transaction_count_6m,
  covered_month_count, active_month_count
FROM app.card_fee_usage_6m
WHERE current_status = 'ACTIVE'
ORDER BY listed_annual_fee DESC,
  approved_amount_6m ASC, card_ref
LIMIT 20;
```

- 구현: resources/fixed_queries.json 및 domain/routing.py
- 시험: 8개 고정 경로, 일부 조건 일치와 복수 후보 거부, 8개 실제 SQL 각1회

## SQL와 DB 정책

- 공개 app View8개와 각 컬럼 및 타입이 PPT, MD H, decisions, DB catalog에서 일치함
- app_internal4개와 원본 테이블은 모델 SQL에서 제외함
- 단일 SELECT, 허용 View1개, LIMIT 정수1~200 필수임. COUNT(*)만 별표 예외임
- WHERE, GROUP BY, HAVING, ORDER BY, LIMIT 허용. CTE/서브쿼리/JOIN/집합연산 금지함
- 허용함수: COUNT, SUM, AVG, MIN, MAX, COALESCE, ROUND, DATE_TRUNC, TO_CHAR
- 비교, 논리, IS NULL, 상수 IN, BETWEEN, 사칙연산 허용. CASE와 창 함수 금지함
- date/numeric 상수만 형변환 허용. 함수 인자형과 pg_catalog 해석 검증 필요함
- SQL16KiB, 결과200행/1MiB. 검증 SQL SHA-256을 실행 직전 재검사함
- 읽기 전용 연결과 BEGIN READ ONLY, set_config(...,true)로 세션3개 지역 설정함
- app.member_id는 인증 범위만 사용. app.base_date와 app.coverage_start는 신뢰 설정 사용함
- 실패/취소 후 롤백 및 연결 폐기, 회원 범위를 풀에 남기지 않음
- 통합 전 카탈로그는 work에만 저장. 설계 카탈로그는 덮어쓰지 않음
- 현재 설계 카탈로그는 관리자 조사 결과이므로 운영 계정 검증 증거가 아님

## 결과 해석과 시험

- 반환행수 < LIMIT은 complete, 같은 경우 limit_reached_more_unknown임
- 잘린 행을 합산해 전체 합계로 표시하지 않음
- 숫자 정밀도 손실 시 문자열 직렬화, DB 타입은 columns에 표시함
- 금액 단위는 미확정이므로 currency=null을 유지함
- Q08 안내 연회비는 실제 청구액이나 혜택 손익이 아님
- 실제 기간, 0행, 거래0, 기간 미성립 NULL을 구분함
- 월별 시작월과 가맹점/6개월뷰 실제 시작일 차이를 시험함
- 최신 연체는 자료가 있는 최신 완료월이며 직전 달로 단정하지 않음

## 확정된 설계 해석과 사용자 결정

- D-01: schema_catalog는 PPT의 object 계약에 따라 View 이름을 키로 구성함
- D-02: 로컬 Bearer token 매핑 사용. 기준일/제공 시작일은 DB에서 확인하여 설정하고 24시간 유효성 적용함
- D-03: 사용자 승인으로 cardlab 시험 예외 적용. 읽기 전용과 SQL 검증 및 회원 세션은 유지함
- D-04: Context7 미제공은 승인된 공식 문서로 대체함. dev_plan.md에 출처 기록함
- D-05: 최신 연체 Q-04의 period는 null이며 실제 월은 반환 행에서 확인함
- 사용자 요청에 따라 .env 토큰과 runtime.local.json의 날짜 및 시험 회원 설정을 완료함
- Groq에는 승인된 공개뷰 구조와 정책, 날짜 및 합성 질문만 전송함

## 구현과 현재 시험 근거

| 설계 항목 | 구현 위치 | 확인한 시험 |
|---|---|---|
| S-R1 입력과 인증, 개인정보 | services, privacy, settings | 단위시험 및 실제 CLI/API 인증 성공과 거부 |
| S-R2 정형8건과 모델1회 | routing, services, groq_gateway | 실제 정형8건 모델0회, NL2SQL2건 모델1회 |
| S-R3 기간과 단위, 지표 | plan_policy | test_policies 및 실제 NL2SQL 성공 |
| S-R4 구조와 필터, 집계 | sql_validator | test_sql_validator89개 및 실제 SQL 검증 |
| S-R5 읽기 전용과 회원 세션 | postgres | 실제 DB 조회, 회원별 대조2건과 동시 조회4건 |
| S-R6 응답 및 오류 | services | 실제 진입점8건, 200행/1MiB 직전 결과 직렬화5회 |
| A/B 예산과 재시도0 | budget, services, gateway | 예산 계산6개, 실제 단계 시간 및 호출 횟수 검사 |
| 공개/내부 뷰와 컬럼 | run_catalog_checks.py | 12개 목록/컬럼/타입/순서/소유자/옵션/접근 일치 |
| CLI 및 API | presentation, run_entrypoint_tests.py | 저장 토큰으로 실제 프로세스와 HTTP8건 통과 |
| 계층과 주석 | 전체 구현 | mypy24파일, docstring 및 DIP 검사 통과 |

실제 통합17건, CLI/API8건, 자동 회귀199개가 통과함. 상세 수치와 한계는 test_report.md 참조.  
모든 증거는 work에 기록하고 승인 원본 설계 파일은 변경하지 않음.  
제한 계정의 권한 자체 검증은 사용자가 해제한 범위이며, cardlab 시험 증거와 구분함.

정형 SQL과 허용 함수를 보수적으로 검증하며 명시적 pg_catalog 함수 표기와 임의 HAVING은 거부함.  
이 범위는 README와 test_report.md에 명시함. 운영 부하의 최대 지연 및 전체 자연어 정확도 보장을 뜻하지 않음.
