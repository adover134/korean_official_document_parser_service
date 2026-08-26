"""Pass1(헤더 계층 LLM 판단)에 대한 선택적 Langfuse 트레이싱.

`langfuse` 패키지가 설치돼 있지 않거나 `LANGFUSE_PUBLIC_KEY`/`LANGFUSE_SECRET_KEY`가
설정 안 돼 있으면 완전히 조용한 no-op으로 동작한다 — 이 파이프라인은 Langfuse 없이도
정상 동작해야 하므로, 여기서 발생하는 어떤 예외도 실제 LLM 호출을 막아선 안 된다.

`chat_json()`이 raw urllib로 API를 직접 호출하는 구조라(OpenAI SDK를 안 씀) Langfuse의
자동 계측(auto-instrumentation)을 못 쓴다 — 대신 수동 계측 두 단계로 구성한다:
  - `trace_span()`: 문서 하나의 Pass1 분류 작업 전체를 감싸는 span(`run_pipeline.run_pass1()`에서 사용).
  - `trace_generation()`: 그 안에서 실제 LLM 호출 한 번(`llm_backend.py`의 `chat_json()`)을
    감싸는 generation — span 안에서 열리므로 OTel 컨텍스트로 자동 중첩된다(문서 하나가 후보
    개수 때문에 여러 배치로 나뉘어도, 그 배치별 generation들이 같은 span 아래 형제로 묶임).

Groq TPM(분당 토큰) 한도 튜닝(README "다음 단계" 참고)이 이 트레이싱을 붙인 원래 동기다 —
후보 개수/배치에 따라 실제 프롬프트·응답 토큰이 얼마나 쓰이는지 Langfuse 대시보드에서 바로
보고 `_MAX_CANDIDATES_PER_CALL`을 튜닝할 수 있다. 이 도메인(나라장터 공공기관 입찰공고문)은
문서 자체가 비저작물 공개 정보라(README "검증" 절 참고) 프롬프트/응답 원문을 그대로 트레이싱에
남긴다 — 다른 도메인에 재사용할 때는 `Langfuse(mask=...)`로 마스킹을 고려할 것.

배포 단계 구분(로컬 테스트 트레이스가 프로덕션 대시보드를 오염시키지 않도록)은 코드가 아니라
배포하는 쪽이 `LANGFUSE_TRACING_ENVIRONMENT`(예: `development`/`staging`/`production`)
환경변수로 설정한다 — Langfuse SDK가 자동으로 읽는다(Langfuse 공식 best-practices 가이드 권장).
"""

from __future__ import annotations

import contextlib
import os
from typing import Any, Iterator

_client: Any = None
_client_checked = False


def _get_client() -> Any:
    """설정돼 있으면 Langfuse 클라이언트를 반환하고, 아니면 None(비활성)을 반환한다.

    `.env`가 아직 로드되기 전에(예: 모듈 import 시점) 이 함수가 먼저 불리면 키를 못 찾아
    영구적으로 비활성으로 캐시될 수 있다 — 반드시 `load_dotenv()` 이후, 실제 LLM 호출
    직전에만 호출되는 지연 초기화로 유지한다(공식 가이드: "Langfuse import는 환경변수
    로드 이후")."""
    global _client, _client_checked
    if _client_checked:
        return _client
    _client_checked = True

    if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
        return None
    try:
        from langfuse import get_client

        # public/secret key, base_url(LANGFUSE_BASE_URL)은 Langfuse가 환경변수에서 직접
        # 읽는다 — 여기서 값을 다시 넘기지 않아도 된다.
        _client = get_client()
    except Exception:
        _client = None
    return _client


def is_enabled() -> bool:
    return _get_client() is not None


def flush() -> None:
    """버퍼링된 트레이스를 즉시 전송한다. 짧게 끝나는 프로세스(CLI 명령 등)는 종료 전에
    반드시 호출해야 데이터 유실이 없다(공식 가이드)."""
    client = _get_client()
    if client is not None:
        with contextlib.suppress(Exception):
            client.flush()


@contextlib.contextmanager
def _start_observation(*, name: str, as_type: str, **kwargs: Any) -> Iterator[Any]:
    """`client.start_as_current_observation()`을 감싸서, 트레이싱 자체의 실패가 실제
    작업(안쪽 `yield`)을 막지 않도록 시작/종료를 수동으로 나눈다.

    바깥에서 그냥 `with client.start_as_current_observation(...) as obs:` 하나로 감싸면,
    안쪽 실제 작업이 예외를 던졌을 때 그 예외 처리가 이 컨텍스트 매니저의 예외 처리와
    얽혀 원래 예외가 아니라 트레이싱 쪽 예외가 대신 전파되거나 제너레이터가 두 번
    yield되는 문제가 생기기 쉽다."""
    client = _get_client()
    if client is None:
        yield None
        return

    try:
        cm = client.start_as_current_observation(name=name, as_type=as_type, **kwargs)
        observation = cm.__enter__()
    except Exception:
        # 트레이싱 시작 자체가 실패해도 실제 작업은 트레이싱 없이 정상 진행.
        yield None
        return

    try:
        yield observation
    except Exception as e:
        with contextlib.suppress(Exception):
            observation.update(level="ERROR", status_message=str(e)[:500])
        with contextlib.suppress(Exception):
            cm.__exit__(type(e), e, e.__traceback__)
        raise
    else:
        with contextlib.suppress(Exception):
            cm.__exit__(None, None, None)


@contextlib.contextmanager
def trace_span(
    *,
    name: str,
    input: Any = None,
    metadata: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    user_id: str | None = None,
) -> Iterator[Any]:
    """문서 하나에 대한 작업 단위(예: Pass1 헤더 분류 전체, API 요청 하나)를 감싸는 span
    겸 트레이스 루트.

    이 span 안에서 연 generation들은 OTel 컨텍스트로 자동 중첩된다. `tags`/`user_id`는
    개별 observation이 아니라 트레이스 전체에 붙는 속성이라(Langfuse SDK 제약 —
    `LangfuseGeneration.update()`엔 그런 인자가 없음) `propagate_attributes()`로 설정한다.
    `user_id`는 API 요청을 감쌀 때(`api.py`) 호출한 API 키를 넘겨, `rate_limit.py`가
    그 값으로 최근 요청 수를 셀 수 있게 한다."""
    client = _get_client()
    if client is None or not (tags or user_id):
        with _start_observation(name=name, as_type="span", input=input, metadata=metadata) as span:
            yield span
        return

    from langfuse import propagate_attributes

    with propagate_attributes(tags=tags, user_id=user_id):
        with _start_observation(name=name, as_type="span", input=input, metadata=metadata) as span:
            yield span


@contextlib.contextmanager
def trace_generation(
    *,
    name: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> Iterator[dict[str, Any]]:
    """LLM 호출 하나를 감싸는 컨텍스트 매니저.

    호출부는 `with trace_generation(...) as record:` 안에서 실제 API를 호출하고, 끝나면
    `record["output"] = ...`, `record["usage"] = {"input": N, "output": M}`을 채워 넣으면
    된다(공식 필드명은 `usage_details`). Langfuse가 비활성이면 `record`는 그냥 버려지는
    평범한 dict다."""
    record: dict[str, Any] = {"output": None, "usage": None}
    with _start_observation(
        name=name,
        as_type="generation",
        model=model,
        input={"system": system_prompt, "user": user_prompt},
    ) as generation:
        yield record
        if generation is not None:
            with contextlib.suppress(Exception):
                update_kwargs: dict[str, Any] = {"output": record.get("output")}
                if record.get("usage"):
                    update_kwargs["usage_details"] = record["usage"]
                generation.update(**update_kwargs)
