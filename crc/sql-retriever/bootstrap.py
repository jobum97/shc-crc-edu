"""설계 리소스와 구현체를 한 곳에서 조립함."""
import json
from pathlib import Path
from groq import AsyncGroq
from application.services import RetrieverService
from infrastructure.settings import load_configuration, LocalAuthenticator
from infrastructure.workflow import LangChainWorkflow
from infrastructure.groq_gateway import GroqGateway
from infrastructure.postgres import PostgresRepository
from infrastructure.sql_validator import SqlValidator

BASE = Path(__file__).resolve().parent


def create_runtime(config_path: Path | None = None, env_path: Path | None = None):
    """실제 구현체를 생성하고 워크플로우와 인증기 및 종료 함수를 반환함."""
    config, secrets = load_configuration(config_path or BASE / 'runtime.local.json',
                                         env_path or BASE.parent / 'secrets/.env')

    def resource(name):
        return json.loads((BASE / 'resources' / name).read_text())

    catalog, queries, errors = resource('view_catalog.json'), resource('fixed_queries.json'), resource('errors.json')
    config['policy'] = resource('sql_policy.json')
    client = AsyncGroq(api_key=secrets.get('GROQ_API_KEY') or 'unconfigured',
                       base_url='https://api.groq.com', timeout=15, max_retries=0)
    model_settings = resource('llm_settings.json')
    gateway = GroqGateway(client, (BASE / 'resources/system_prompt.txt').read_text(),
                          resource('response_format.json'), model_settings)
    connection = {'host': secrets.get('RDB_HOST'), 'port': secrets.get('RDB_PORT'),
                  'dbname': secrets.get('RDB_DATABASE'), 'user': secrets.get('RDB_USER'),
                  'password': secrets.get('RDB_PASSWORD')}
    config['execution_ready'] = all(connection.values()) and str(connection['user']).lower() not in {'cardlab', 'postgres'}
    repository = PostgresRepository(connection, catalog)
    validator = SqlValidator(catalog, queries)
    service = RetrieverService(config, catalog, queries, errors, gateway, repository, validator)
    workflow = LangChainWorkflow(service)
    auth = LocalAuthenticator(config.get('auth', []), secrets)
    return workflow, auth, client.close
