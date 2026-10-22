"""공개 및 내부 뷰의 현재 구조와 접근 권한을 승인 당시 카탈로그와 비교함."""
import json
from datetime import datetime, timezone
from typing import Any

import psycopg
from psycopg import sql

from bootstrap import BASE
from infrastructure.settings import load_configuration


def main() -> int:
    """업무 행을 읽지 않고 뷰 목록, 컬럼과 LIMIT 0 접근 결과를 기록함.

    부수효과: 읽기 전용 메타데이터 조회와 work 보고서 작성만 수행함.
    내부 뷰가 보인다는 사실은 모델의 조회 허용 목록에 추가한다는 의미가 아님.
    """
    config, values = load_configuration(BASE / 'runtime.local.json', BASE.parent / 'secrets/.env')
    expected = json.loads((BASE.parent / 'output/sql-retriever/work/db_catalog.json').read_text())
    public = json.loads((BASE / 'resources/view_catalog.json').read_text())
    expected_views = {v['schema'] + '.' + v['name']: v for v in expected['views']}
    report: dict[str, Any] = {'status': 'running', 'business_rows_read': 0, 'model_called': False}
    try:
        kwargs = {name: values[key] for name, key in {'host': 'RDB_HOST', 'port': 'RDB_PORT',
                  'dbname': 'RDB_DATABASE', 'user': 'RDB_USER', 'password': 'RDB_PASSWORD'}.items()}
        exception = config.get('testing', {}).get('allow_cardlab_test') is True and kwargs['user'] == 'cardlab'
        if kwargs['user'].lower() in {'cardlab', 'postgres'} and not exception:
            raise ValueError('account policy')
        actual: dict[str, Any] = {}
        with psycopg.connect(**kwargs, connect_timeout=3,
                              options='-c default_transaction_read_only=on -c statement_timeout=3000') as connection:
            with connection.cursor() as cursor:
                cursor.execute('SHOW transaction_read_only')
                mode = cursor.fetchone()
                report['read_only'] = mode is not None and mode[0] == 'on'
                cursor.execute('SELECT rolsuper,rolcreaterole,rolcreatedb,rolbypassrls '
                               'FROM pg_roles WHERE rolname=current_user')
                flags = cursor.fetchone()
                if flags is None or (any(flags) and not exception):
                    raise ValueError('role policy')
                report['cardlab_test_exception'] = exception
                report['restricted_role_tested'] = not any(flags)
                cursor.execute("SELECT n.nspname,c.relname,pg_get_userbyid(c.relowner),c.reloptions, "
                               "has_table_privilege(current_user,c.oid,'SELECT') FROM pg_class c "
                               "JOIN pg_namespace n ON n.oid=c.relnamespace "
                               "WHERE c.relkind IN ('v','m') AND n.nspname NOT LIKE 'pg_%' "
                               "AND n.nspname <> 'information_schema' "
                               "AND has_schema_privilege(current_user,n.oid,'USAGE') "
                               "ORDER BY n.nspname,c.relname")
                for schema, name, owner, options, can_select in cursor.fetchall():
                    full_name = schema + '.' + name
                    cursor.execute('SELECT a.attname,format_type(a.atttypid,a.atttypmod) '
                                   'FROM pg_attribute a WHERE a.attrelid=to_regclass(%s) '
                                   'AND a.attnum>0 AND NOT a.attisdropped ORDER BY a.attnum', (full_name,))
                    columns = [{'name': c, 'type': t} for c, t in cursor.fetchall()]
                    accessible = False
                    if can_select:
                        # Identifier를 사용해 카탈로그 이름을 SQL 코드로 해석하지 않게 함.
                        cursor.execute(sql.SQL('SELECT * FROM {}.{} LIMIT 0').format(
                            sql.Identifier(schema), sql.Identifier(name)))
                        accessible = cursor.description is not None
                    actual[full_name] = {'owner': owner, 'options': options or [], 'columns': columns,
                                         'select_privilege': can_select, 'limit_zero_access': accessible}
        changes = []
        for name in sorted(expected_views.keys() & actual.keys()):
            previous, current = expected_views[name], actual[name]
            fields = []
            if current['columns'] != [{'name': c['name'], 'type': c['type']} for c in previous['columns']]:
                fields.append('columns')
            if current['owner'] != previous['owner']:
                fields.append('owner')
            if sorted(current['options']) != sorted(previous.get('options') or []):
                fields.append('options')
            if current['limit_zero_access'] != previous.get('limit_zero_access', False):
                fields.append('access')
            if fields:
                changes.append({'view': name, 'changed_fields': fields})
        report.update(design_only=sorted(expected_views.keys()-actual.keys()),
                      db_only=sorted(actual.keys()-expected_views.keys()), changed=changes,
                      public_views=sorted(name for name in actual if name in public),
                      other_visible_views=sorted(name for name in actual if name not in public), views=actual,
                      internal_views_in_model_allowlist=sorted(name for name in public if name.startswith('app_internal.')))
        report['status'] = 'passed' if (not report['design_only'] and not report['db_only'] and not changes
                                       and report['read_only'] and not report['internal_views_in_model_allowlist']) else 'failed'
        return 0 if report['status'] == 'passed' else 1
    except Exception as exc:
        report.update(status='failed', failure_type=type(exc).__name__, sqlstate=getattr(exc, 'sqlstate', None))
        return 1
    finally:
        report['completed_at'] = datetime.now(timezone.utc).isoformat()
        (BASE / 'work/catalog_comparison.json').write_text(json.dumps(report, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps({key: value for key, value in report.items() if key != 'views'}, ensure_ascii=False))


if __name__ == '__main__':
    raise SystemExit(main())
