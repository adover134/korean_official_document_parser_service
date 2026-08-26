"""`HWP2MD_API_KEYS`로 설정하는 API 키 인증.

미설정이면 인증 없이 열린 상태로 동작한다(로컬 개발/사내망처럼 인증이 필요 없는 배포를
막지 않기 위함) — README "다음 단계"에 있던 경고대로, 공인망에 그대로 노출할 배포는
반드시 이 값을 설정해야 한다.
"""

from __future__ import annotations

import os

from fastapi import Header, HTTPException

ANONYMOUS = "anonymous"


def _configured_keys() -> set[str] | None:
    raw = os.environ.get("HWP2MD_API_KEYS", "").strip()
    if not raw:
        return None
    return {k.strip() for k in raw.split(",") if k.strip()}


def require_api_key(authorization: str | None = Header(default=None)) -> str:
    """`Authorization: Bearer <key>` 검증. `HWP2MD_API_KEYS` 미설정이면 통과(`ANONYMOUS`).

    반환값은 rate_limit.check_rate_limit()과 tracing.trace_span(user_id=...)에 그대로
    넘겨져, 같은 키의 요청들을 Langfuse에서 한데 묶는 식별자로도 쓰인다.
    """
    keys = _configured_keys()
    if keys is None:
        return ANONYMOUS

    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "인증 필요: Authorization: Bearer <API_KEY> 헤더가 없습니다")
    key = authorization.removeprefix("Bearer ").strip()
    if key not in keys:
        raise HTTPException(401, "유효하지 않은 API 키")
    return key
