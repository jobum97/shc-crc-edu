"""질문에서 식별자를 제거하고 확실하게 정제할 수 없는 입력을 거부함."""
import re
import unicodedata

from .errors import PolicyError

_PATTERNS = (
    r"(?:이메일\s*[:=]?\s*)?[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}",
    r"(?:주민(?:등록)?번호\s*[:=]?\s*)?(?<!\d)\d{6}[- ]?[1-8]\d{6}(?!\d)",
    r"(?:전화번호|휴대폰번호|연락처)?\s*(?<!\d)0\d{1,2}[- ]?\d{3,4}[- ]?\d{4}(?!\d)",
    r"(?:카드\s*번호\s*[:=]?\s*)?(?<!\d)(?:\d{13,19}|\d{4}(?:[- ]\d{4}){3}|\d{4}[- ]\d{6}[- ]\d{5})(?!\d)",
    r"(?i)(?:api[_ -]?key|access[_ -]?token|인증\s*토큰|비밀번호|password)\s*[:=]\s*[^\s,;]+",
    r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*",
    r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
    r"(?i)\b(?:sk|gsk)[-_][A-Za-z0-9_-]{8,}\b",
)
_IDENTIFIERS = tuple(re.compile(p) for p in _PATTERNS)


def clean_question(question: str) -> str:
    """식별자를 제거한 질문을 반환하고 잔존 의심 입력은 PolicyError로 거부함.

    이름이나 소유자를 추정하지 않으며 타인 조회와 분할 식별자는 마스킹으로 허용하지 않음.
    정제 전후 문자열은 로그나 오류 메시지에 포함하지 않음.
    """
    if not isinstance(question, str) or not question.strip():
        raise PolicyError("INVALID_REQUEST")
    value = unicodedata.normalize("NFKC", question)
    if any(unicodedata.category(c) in {"Cf", "Cs"} for c in value):
        raise PolicyError("PRIVACY_REJECTED")
    if any(unicodedata.category(c) == "Cc" and c not in "\n\r\t" for c in value):
        raise PolicyError("PRIVACY_REJECTED")
    if re.search(r"다른\s*(?:회원|고객|사람)|타인|남의|전체\s*(?:회원|고객)|모든\s*(?:회원|고객)|친구의|배우자의", value):
        raise PolicyError("UNAUTHORIZED_SCOPE")
    if re.search(r"(?i)</?\w|member[_ ]?id\s*[:=]|회원\s*(?:번호|아이디|ID)\s*[:=]?\s*\w+|\bM-\d+\b", value):
        raise PolicyError("PRIVACY_REJECTED")
    # 한 자리씩 나뉜 번호는 문맥을 지운 뒤 정상 질문으로 오인하지 않도록 먼저 거부함.
    if re.search(r"(?:\d\s+){6,}\d|카드\s*번호.*(?:한|일|이|삼|사|오|육|칠|팔|구)\s", value):
        raise PolicyError("PRIVACY_REJECTED")
    for pattern in _IDENTIFIERS:
        value = pattern.sub(" ", value)
    if re.search(r"\d{7,}|(?:\d[ .,_/\-]+){6,}\d|@|(?i:bearer|token|password|api[_ -]?key|\b(?:gsk|sk)[-_])", value):
        raise PolicyError("PRIVACY_REJECTED")
    if re.search(r"주민(?:등록)?번호|카드\s*번호|인증\s*토큰|비밀번호", value):
        raise PolicyError("PRIVACY_REJECTED")
    value = re.sub(r"\s+", " ", value).strip(" ,;:.")
    if not value:
        raise PolicyError("PRIVACY_REJECTED")
    return value
