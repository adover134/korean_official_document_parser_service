"""Langfuse에 쌓인 요청 트레이스를 세어 API 키별 요청량을 제한한다.

별도 카운터 저장소(Redis 등) 없이, 이미 매 요청마다 `tracing.py`가 `user_id`=API 키로
태깅해 남기는 Langfuse 트레이스(`api.py`의 `convert-document` 루트 span)를 그대로
rate limit의 근거 데이터로 재사용한다.

주의(설계상 한계, 실측 확인됨): `flush()`로 강제 전송해도 Langfuse 서버가 그 트레이스를
실제로 조회 API(`GET /api/public/v2/observations`)에서 반환하기까지 인입 후 색인 지연이
있다 — 실측 결과 몇 초 안에도 안 잡히는 경우가 있었다(정확히 몇 초인지는 Langfuse 인프라
상태에 따라 달라 보장할 수 없음). 즉 이 rate limit은 "정확한 실시간 카운터"가 아니라
"대략적인 제한"이고, **같은 색인 지연 구간 안에 몰린 버스트 요청은 한도를 넘겨도 통과할 수
있다** — 순간적 버스트를 정확히 막는 용도가 아니라, 지속적인 남용을 거칠게 막는 용도로만
써야 한다(엄격한 하드 리밋이 필요하면 Redis 등 별도 카운터가 필요함, 이 구현은 그 정도
정확도를 목표하지 않음). Langfuse가 비활성(미설정)이거나 조회 자체가 실패하면 조용히
통과시킨다(fail-open) — `tracing.py`와 동일한 철학: 관측/제어 인프라의 장애가 핵심 기능
(변환)을 막아선 안 됨. 같은 이유로, 인증이 꺼져 있어 모든 요청이 `auth.ANONYMOUS`로
뭉뚱그려질 때는(개별 클라이언트를 구분할 수 없으므로) rate limit 자체를 적용하지 않는다.
"""

from __future__ import annotations

import datetime
import os

from fastapi import HTTPException

from .auth import ANONYMOUS
from .tracing import _get_client


def _limit_per_minute() -> int | None:
    raw = os.environ.get("HWP2MD_RATE_LIMIT_PER_MINUTE", "").strip()
    if not raw:
        return None
    try:
        limit = int(raw)
    except ValueError:
        return None
    return limit if limit > 0 else None


def check_rate_limit(api_key: str) -> None:
    """한도 초과 시 429를 던진다. 비활성 조건(미설정/anonymous/조회 실패)이면 그냥 통과."""
    limit = _limit_per_minute()
    if limit is None or api_key == ANONYMOUS:
        return

    client = _get_client()
    if client is None:
        return

    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(minutes=1)
    try:
        response = client.api.observations.get_many(
            user_id=api_key,
            is_root_observation=True,
            from_start_time=since,
            limit=limit,
        )
        recent_count = len(response.data)
    except Exception:
        return

    if recent_count >= limit:
        raise HTTPException(
            429,
            f"요청 한도 초과: 최근 1분간 {recent_count}건 (한도 {limit}건) — 잠시 후 다시 시도하세요",
        )
