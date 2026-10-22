"""로컬 설정과 환경 파일을 읽고 인증 범위를 검증함."""
import hmac
import json
from pathlib import Path
from dotenv import dotenv_values


def load_configuration(config_path: Path, env_path: Path) -> tuple[dict, dict]:
    """설정 파일을 읽으며 실패 시 비밀값 없는 빈 설정으로 닫힘.

    .env는 읽기만 하며 치환과 환경변수 자동 주입을 하지 않음.
    """
    values = dict(dotenv_values(env_path, interpolate=False)) if env_path.is_file() else {}
    try:
        config = json.loads(config_path.read_text())
        if not isinstance(config, dict):
            config = {}
    except (OSError, ValueError):
        config = {}
    return config, values


class LocalAuthenticator:
    """서버 설정의 토큰과 허용 회원만 인증 결과로 변환함.

    클라이언트의 회원 범위를 신뢰하거나 질문에서 회원을 추출하지 않음.
    """

    def __init__(self, entries: list, secrets: dict):
        """관리자가 등록한 토큰 환경변수와 회원 목록을 주입받음."""
        self.entries = entries if isinstance(entries, list) else []
        self.secrets = secrets

    def authenticate(self, token: str | None) -> dict | None:
        """일치하는 단일 토큰의 범위를 반환하며 미일치나 중복은 거부함."""
        if not token or len(token.encode()) > 4096:
            return None
        matches = []
        for entry in self.entries:
            if not isinstance(entry, dict) or not isinstance(entry.get('token_env'), str):
                return None
            expected = self.secrets.get(entry.get('token_env', ''))
            if isinstance(expected, str) and len(expected) >= 32 and hmac.compare_digest(expected.encode(), token.encode()):
                if all(isinstance(entry.get(k), str) and entry[k] for k in ('principal_id', 'authorized_member_id')):
                    matches.append({k: entry[k] for k in ('principal_id', 'authorized_member_id')})
        return matches[0] if len(matches) == 1 else None
