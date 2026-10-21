"""v2 설계의 카탈로그, 프롬프트, 시간 예산과 원고 정합성 검사. DB 실행 검증 아님."""
import json
import re
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
spec = json.loads((BASE / 'work/slides_v2.json').read_text())
catalog = json.loads((BASE / 'work/db_catalog.json').read_text())
md = (BASE / 'sql_retriever_설계서_v2.md').read_text()
checks = {}
checks['slide_sequence'] = [s['id'] for s in spec['slides']] == list(range(1, 63))
checks['public_view_count'] = len(spec['public_view_catalog']) == 8
checks['catalog_view_count'] = len(catalog['views']) == 12
db = {f"{v['schema']}.{v['name']}": v for v in catalog['views']}
checks['column_types'] = all(
    [(c['name'], c['type']) for c in v['columns']] ==
    [(c['name'], c['type']) for c in db[v['view']]['columns']]
    for v in spec['public_view_catalog'])
checks['catalog_semantics'] = all(
    all(v.get(k) for k in ['description', 'grain', 'period_policy', 'value_semantics'])
    for v in spec['public_view_catalog'])
schema = spec['llm_response_schema']
checks['schema_identity'] = spec['llm_response_format']['json_schema']['schema'] == schema
checks['schema_view_enum'] = set(schema['properties']['view']['enum']) == {
    *[v['view'] for v in spec['public_view_catalog']], None}
checks['strict_schema'] = schema['additionalProperties'] is False and set(schema['required']) == set(schema['properties'])
checks['prompt_sections'] = all(f'[{s}]' in spec['system_prompt'] for s in
    ['목표', '역할', '맥락', '입력', '처리', '출력', '제약조건', '예시'])
checks['time_budget'] = sum(spec['time_budget']['fixed']) == 14 and sum(spec['time_budget']['nl2sql']) == 29
checks['fixed_queries'] = all(f'Q-{i:02d}' in md for i in range(1, 9))
checks['markdown_width'] = all(len(line) <= 120 for line in md.splitlines())
checks['markdown_punctuation'] = '·' not in md
checks['script_slide_titles'] = all(s['title'] in md for s in spec['slides'])
checks['md_json_appendices_parse'] = all(json.loads(block) is not None for block in re.findall(r'```json\n(.*?)\n```', md, re.S))
report = {'result': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
          'claim_boundary': '설계 산출물 정합성 검사. Groq 호출, 운영 계정 격리와 성능 실측 미수행'}
(BASE / 'build/design_validation_v2.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
print(json.dumps(report, ensure_ascii=False))
raise SystemExit(0 if all(checks.values()) else 1)
