"""설계 원문의 수치, 단계, 스키마와 조사 증거를 빌드 전에 대조함."""
from pathlib import Path
import json
import re

BASE = Path(__file__).resolve().parents[1]


def read(name):
    return json.loads((BASE / 'work' / name).read_text())


def main():
    spec = read('slides.json')
    slides = spec['slides']
    catalog = read('db_catalog.json')
    queries = read('fixed_query_candidates.json')
    decisions = read('decisions.json')
    checks = []

    def check(name, condition, evidence):
        checks.append({'check': name, 'passed': bool(condition), 'evidence': evidence})

    required = ['단계ID', '단계명', '이전 단계', '비즈니스 목표', '입력 데이터',
                '수행 작업', '출력 데이터', '담당자 유형', '외부시스템', '예외 처리', '비고']
    stages = slides[4:10]
    for number, slide in enumerate(stages, 1):
        rows = dict(slide['rows'])
        check(f'S-R{number} 필수 11항목', list(rows) == required and all(rows.values()), list(rows))
        check(f'S-R{number} 이전 단계', rows['이전 단계'] == ('트리거' if number == 1 else
              'S-R5 성공 또는 S-R1 ~ S-R5 오류' if number == 6 else f'S-R{number-1}'), rows['이전 단계'])
    expected_edges = {'T': ['R1'], 'R1': ['R2', 'R6'], 'R2': ['F', 'L', 'R6'],
                      'F': ['R3'], 'L': ['R3', 'R6'], 'R3': ['R4', 'R6'],
                      'R4': ['R5', 'R6'], 'R5': ['R6'], 'R6': ['E']}
    edges = {}
    for line in spec['mermaid'].splitlines():
        source = re.match(r'\s*(T|R[1-6]|F|L)\b', line)
        target = re.search(r'(?:-->|\.->)\s*(T|R[1-6]|F|L|E)\b', line)
        if source and target:
            edges.setdefault(source[1], []).append(target[1])
    check('Mermaid 연결', {k: sorted(set(v)) for k, v in edges.items()} ==
          {k: sorted(v) for k, v in expected_edges.items()}, edges)
    check('외부 연동 최악값', 15 * (1 + 0) == 15 and '15 × (1 + 0) = 15초' in str(slides[10]), '15 × (1 + 0) = 15초')
    check('전체 예산', max(0, 15) + 0 <= 30 and '15초 ≤ 30초' in str(slides[12]), 'max(0,15)+0=15 ≤ 30')
    check('내부 처리 몫', 30 - 15 - 1 == 14 and '내부 DB와 규칙 14초' in str(slides[12]), '30-15-1=14')
    for label, calculation in slides[20]['rows']:
        m = re.search(r'30 - (\d+) - 1 - (\d+) = (\d+)초', calculation)
        check('예산 예: ' + label, bool(m) and 30-int(m[1])-1-int(m[2]) == int(m[3]), calculation)
    fixed_example = slides[20]['rows'][-1]
    elapsed = int(re.search(r'경과 (\d+)초', fixed_example[0])[1])
    check('정형 분기 예 진입 가능', 30-elapsed-1-15 >= 1.5, f'분기 전: 30-{elapsed}-1-15 ≥ 1.5')
    verify = catalog['verification']
    check('DB 뷰 수와 LIMIT 0', verify['database_selectable_view_count'] == len(catalog['views']) ==
          verify['catalog_view_count'] == verify['limit_zero_success_count'] == 11, verify)
    check('업무 행 미조회', verify['business_data_rows_read'] == queries['business_data_rows_read'] == 0,
          'LIMIT 0 및 EXPLAIN ANALYZE FALSE')
    check('정형 SQL EXPLAIN', queries['passed'] == queries['total'] == 7, {'passed': queries['passed'], 'total': queries['total']})
    for query in queries['queries']:
        slide = next(s for s in slides if s['kind'] == 'code' and query['id'] in s['title'])
        clean = lambda value: re.sub(r'\s+', ' ', value).strip()
        check(query['id'] + ' SQL 원문', clean(slide['code']) == clean(query['sql']), query['id'])
    schema = spec['llm_response_schema']
    check('엄격 JSON Schema', schema['additionalProperties'] is False and
          set(schema['required']) == set(schema['properties']), list(schema['required']))
    sections = ['목표', '역할', '맥락', '입력', '처리', '출력', '제약조건', '예시']
    check('시스템 프롬프트 8섹션', all('['+name+']' in spec['system_prompt'] for name in sections), sections)
    contract = spec['field_contract']
    check('Context 단계 필드 연결', contract['Context_additions'] == {
        'S-R2': ['context.plan'], 'S-R3': ['context.plan_check'],
        'S-R4': ['context.validated_sql', 'context.sql_fingerprint'], 'S-R5': ['context.result'],
        '모든 단계 오류': ['context.error']},
        contract['Context_additions'])
    research = decisions['llm_official_research']
    sources = {name: value.get('source_url') for name, value in research.items()
               if isinstance(value, dict) and 'value' in value}
    sources.update({name: value.get('source_url') for name, value in research['settings'].items()})
    check('LLM 공식 근거', all(sources.values()), sources)
    script = (BASE/'sql_retriever_워크플로우설계서.md').read_text()
    check('문서 가운뎃점 없음', '·' not in script and '·' not in json.dumps(spec, ensure_ascii=False), '0건')
    check('문서 줄 길이', all(len(line) <= 120 for line in script.splitlines()), max(map(len, script.splitlines())))
    report = {'result': 'passed' if all(c['passed'] for c in checks) else 'failed', 'checks': checks,
              'visual_review': '빌드 후 별도 수행', 'field_lineage_manual_review': 'Context 누적 및 오류 분기 검토'}
    (BASE/'work/design_validation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
    print(json.dumps({'result': report['result'], 'count': len(checks),
                      'failures': [c for c in checks if not c['passed']]}, ensure_ascii=False))
    raise SystemExit(0 if report['result'] == 'passed' else 1)


if __name__ == '__main__':
    main()
