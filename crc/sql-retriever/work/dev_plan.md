# SQL 검색기 개발 계획

상태: 사용자 “구현까지 진행해줘” 지시에 따라 기본안 채택 및 구현 완료, 실제 DB와 Groq 통합시험 통과  
출력: crc/sql-retriever  
기준: 승인 v2 설계서. 모델, 시간, SQL과 오류 코드는 원본 유지함.

## 확정한 결정

- D-01: schema_catalog는 PPT의 object 계약에 맞춰 View 이름을 키로 구성함
- D-02: 로컬 Bearer 토큰을 서버의 회원 목록과 대조하여 AuthScope 생성함
- 날짜는 관리 설정의 명시값만 사용함. loaded_at 기준 24시간 이내인지 검사함
- D-03: 기본은 비관리자 계정이며 사용자 승인 cardlab 시험 예외를 적용함
- D-04: Context7 미제공으로 공식 문서를 대체 근거로 사용함
- D-05: Q-04 최신 현황의 period는 null, 실제 월은 결과 행으로 전달함
- 사용자 지시를 구현 진행 승인으로 기록함. 원본 설계 파일은 수정하지 않음

## 계층과 파일

| 계층 | 파일과 책임 |
|---|---|
| domain | budget, privacy, routing, plan_policy: 외부 입출력 없는 규칙 |
| application | models, ports, services: 상태, 인터페이스, 여섯 단계 업무 |
| infrastructure | workflow: LangChain RunnableLambda로 순서 실행 |
| infrastructure | sql_validator: SQLGlot 구조 검사, 식과 계획 대조 |
| infrastructure | postgres, groq_gateway: 읽기 전용 DB와 최대 1회 모델 호출 |
| infrastructure | settings: .env 읽기 및 관리 설정의 인증 범위 검증 |
| presentation | api, cli: 질문 변환, 서비스 호출, 동일 응답 계약 |
| bootstrap.py | 모든 실제 구현체 생성과 주입 |
| resources | 승인 프롬프트 전문, 스키마, 공개 카탈로그, 정형 SQL과 오류 |
| tests | 가짜 포트 시험, 구조와 보안 회귀, 선택적 실제 통합시험 |

## 실행 방식

- S-R1부터 S-R5까지 Context를 누적하고 S-R6에서만 Response로 변환함
- LCEL의 각 Runnable 입출력은 None임. 요청 상태는 요청별 지역 클로저에만 보관함
- 자동 추적을 명시적으로 비활성화하여 질문과 회원 범위의 외부 유출을 막음
- 고정 경로는 모델을 호출하지 않으며 생성 경로는 AsyncGroq를 한 번 호출함
- PostgreSQL은 요청마다 독립 연결 사용함. 연결 재사용으로 회원 범위가 섞이지 않음
- SQL 실행 전 read-only와 역할 권한 및 검증 SQL 해시를 재확인함
- 16KiB SQL, 4KiB 질문, 200행/1MiB 결과 및 전체30초/단계별 상한을 검사함
- API POST /search는 question만 수신함. 회원 범위는 Bearer 토큰으로 결정함
- CLI는 질문 인자 또는 표준 입력, 토큰은 환경변수로만 수신함

## 패키지와 출처

Python 3.12 전용 .venv에 설치함. 시스템 패키지를 공유하지 않음.  
아래 직접 의존성과 전이 의존성의 실제 설치 버전은 requirements.txt에 고정함.

| 패키지 | 버전 | 목적 |
|---|---|---|
| langchain-core | 1.6.12 | 단계 실행 |
| groq | 1.7.0 | 승인 AsyncGroq 연동 |
| psycopg / psycopg-binary | 3.3.6 | 비동기 PostgreSQL |
| sqlglot | 30.24.0 | SQL 문법 구조 검사 |
| fastapi | 0.156.8 | 동기 응답 HTTP API |
| uvicorn | 0.54.0 | API 서버 |
| python-dotenv | 1.2.4 | 읽기 전용 환경파일 로딩 |
| pytest / pytest-asyncio | 9.1.1 / 1.4.0 | 일반 및 비동기 시험 |
| httpx | 0.28.1 | API 시험 |
| mypy / ruff | 2.4.0 / 0.17.1 | 타입, 계층과 주석 점검 |

- 배포 출처: [공식 PyPI](https://pypi.org/)
- [LangChain RunnableLambda](https://reference.langchain.com/python/langchain-core/runnables/base/RunnableLambda)
- [Psycopg 비동기 연결](https://www.psycopg.org/psycopg3/docs/advanced/async.html)
- [FastAPI 시험](https://fastapi.tiangolo.com/tutorial/testing/)
- [Groq 공식 SDK](https://github.com/groq/groq-python)
- [SQLGlot 프로젝트](https://github.com/tobymao/sqlglot)

공식 웹 문서를 확인하고 .temp에 저장함. Context7을 사용했다고 주장하지 않음.

## 시험과 완료 기준

1. 입력과 개인정보, 모든 정형 질문과 모호한 질문, SQL 허용/거부 구조 시험
2. 6개 예산 계산 예, 모든 단계 오류와 모델 무호출/한 번 호출 확인
3. DB/모델 가짜 객체로 읽기전용 세션과 페이로드, 오류 변환 확인
4. 단위 회귀 실행 및 CLI 실제 프로세스와 API 실제 서버 HTTP 응답 확인
5. 승인 계정과 저장 설정으로 전체 카탈로그 비교, 정형8개와 실제 생성 질문 및 회원 범위 시험
6. 실제 호출은 단계 소요시간과 결과 행수만 보고하고 업무행은 시험 로그에 남기지 않음
7. DIP 금지 import 및 주석 체크리스트를 실행하고 근거를 test_report.md에 기록함

설정과 실제 시험은 완료됨. 현재 결과와 지원 범위는 test_report.md로 통합함.

## 추가 사용자 결정: cardlab 시험

사용자가 제한 계정 제약 해제와 cardlab 테스트를 명시적으로 요청함.  
로컬 testing.allow_cardlab_test=true와 정확한 cardlab 계정 조합만 관리자 권한 거부를 면제함.  
읽기 전용과 모델 SQL 제한은 유지하며 제한 계정의 권한 격리 증거와 구분함.  
토큰 미등록 상태의 시험은 --ephemeral-test-tokens로 메모리에서 인증값 생성함.

## 실제 Groq 전송 승인과 시험 결과

사용자가 조회뷰 구조와 정책, 기준일 및 합성 질문의 api.groq.com 전송을 명시 승인함.  
회원 ID와 비밀값, DB 결과 행은 전송 대상에서 제외함.  
실제 DB와 모델 통합시험17건 통과, 상세 결과는 integration_report.json과 test_report.md 참조.

## 최종 저장 설정과 진입점 검증

최신 사용자 지시에 따라 .env 인증 토큰을 직접 저장하고 로컬 메타데이터와 회원 설정을 재확인함.  
저장 설정 통합17건과 실제 CLI/API8건 통과함. 실행 재현용 run_entrypoint_tests.py 추가함.  
최종 자동 회귀199개 및 타입24파일, 공개 docstring 검사 통과함.

## 미실행 항목 보완 결과

run_catalog_checks.py로 내부4개 포함12개 뷰를 비교하고 run_response_benchmark.py로 최대 응답을 측정함.  
통합시험에 단계별 시간과 모델 호출 횟수 및 회원 교차 동시조회4건을 추가해 모두 통과함.  
과거 미실행 문구를 현재 결과로 교체했으며 승인된 범위의 필수 미실행 시험은 없음.
