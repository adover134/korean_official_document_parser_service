"""배포용 HTTP API — FastAPI. 서버 쪽에서 백엔드(Ollama 또는 OpenAI 호환)를 환경변수로 미리
설정해두고, 클라이언트는 파일만 올리면 된다(remove.bg류 서비스처럼 클라이언트가 자기 LLM 키를
들고 올 필요가 없는 구조) — CLI의 `--backend`/`--api-key`를 매 요청마다 받는 대신, 서버 기동 시
한 번만 환경변수로 정한다.

환경변수:
  HWP2MD_BACKEND     ollama(기본) | openai | groq | gemini
  HWP2MD_MODEL       모델명 (기본: qwen3.5:9b)
  HWP2MD_HOST        Ollama 서버 주소 (기본: http://localhost:11434, backend=ollama일 때만)
  HWP2MD_API_KEY     backend가 ollama가 아닐 때 필요 (또는 표준 OPENAI_API_KEY 등도 인식)
  HWP2MD_BASE_URL    OpenAI 호환 엔드포인트 (openai/groq/gemini는 기본값 있음)
  HWP2MD_API_KEYS              /v1/convert 호출자 인증 키 목록(쉼표 구분). 미설정 시 인증 없이 열림.
  HWP2MD_RATE_LIMIT_PER_MINUTE API 키별 분당 요청 한도. 미설정 시 rate limit 없음(Langfuse 설정 필요).

실행:
    uvicorn hwp_hierarchical_md.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from .auth import require_api_key
from .cli import _API_KEY_ENV_VARS, _DEFAULT_BASE_URLS, _load_dotenv_if_present, check_npx
from .llm_backend import OllamaBackend, OpenAICompatBackend
from .rate_limit import check_rate_limit
from .run_pipeline import derive_title_from_filename, run_pass1, run_pass2, run_stage1
from .tracing import flush, trace_span

# 배포 환경(Docker 등)은 환경변수를 직접 주입하지만, 로컬에서 uvicorn을 바로 띄워 테스트할 때는
# cwd의 .env를 못 읽으면 HWP2MD_*/LANGFUSE_* 등이 전부 빠진 채로 서버가 뜬다 — cli.py와 동일하게
# 모듈 로드 시점(요청 처리 전, import 순서상 가장 이른 시점)에 조용히 시도한다.
_load_dotenv_if_present()

SUPPORTED_EXTENSIONS = {".hwp", ".hwpx"}

app = FastAPI(
    title="hwp-hierarchical-md",
    description="계층 구조를 보존하는 HWP/HWPX -> Markdown 변환 API",
    version="0.1.0",
)


def _backend_from_env():
    backend_name = os.environ.get("HWP2MD_BACKEND", "ollama")
    model = os.environ.get("HWP2MD_MODEL", "qwen3.5:9b")
    if backend_name == "ollama":
        host = os.environ.get("HWP2MD_HOST", "http://localhost:11434")
        return None, model, host  # run_pass1이 model/host로 OllamaBackend를 알아서 만듦
    api_key = os.environ.get("HWP2MD_API_KEY") or os.environ.get(_API_KEY_ENV_VARS.get(backend_name, ""))
    if not api_key:
        raise RuntimeError(f"HWP2MD_BACKEND={backend_name}인데 API 키가 없습니다 (HWP2MD_API_KEY 필요)")
    base_url = os.environ.get("HWP2MD_BASE_URL") or _DEFAULT_BASE_URLS.get(backend_name)
    if not base_url:
        raise RuntimeError(f"HWP2MD_BACKEND={backend_name}는 HWP2MD_BASE_URL이 필요합니다")
    return OpenAICompatBackend(model=model, api_key=api_key, base_url=base_url), model, None


@app.get("/v1/health")
def health() -> dict:
    """배포 환경 점검 — npx(kordoc)와 LLM 백엔드 설정이 유효한지."""
    problems = []
    npx_problem = check_npx()
    if npx_problem:
        problems.append(npx_problem)
    try:
        _backend_from_env()
    except RuntimeError as e:
        problems.append(str(e))
    return {"ok": not problems, "problems": problems}


@app.post("/v1/convert", response_class=PlainTextResponse)
async def convert(
    file: UploadFile = File(...),
    api_key: str = Depends(require_api_key),
) -> str:
    """HWP/HWPX 파일을 업로드하면 계층 구조가 보존된 Markdown을 반환한다.

    `HWP2MD_API_KEYS`가 설정돼 있으면 `Authorization: Bearer <key>`가 필요하고, 그
    키 기준으로(`HWP2MD_RATE_LIMIT_PER_MINUTE` 설정 시) 분당 요청 수를 제한한다 — 카운팅은
    이 요청 자체가 Langfuse에 남기는 `convert-document` 트레이스를 근거로 하므로
    (`rate_limit.py`), Langfuse 미설정 시엔 인증만 동작하고 rate limit은 적용되지 않는다."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"지원하지 않는 확장자: {suffix!r} (지원: {sorted(SUPPORTED_EXTENSIONS)})")

    check_rate_limit(api_key)

    try:
        backend, model, host = _backend_from_env()
    except RuntimeError as e:
        raise HTTPException(500, str(e)) from e

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        input_path = tmp_dir / (file.filename or f"upload{suffix}")
        input_path.write_bytes(await file.read())

        stage1_path = tmp_dir / "stage1.md"
        pass1_path = tmp_dir / "pass1.json"

        try:
            with trace_span(
                name="convert-document",
                input={"filename": file.filename, "size_bytes": input_path.stat().st_size},
                user_id=api_key,
            ):
                stage1_text = run_stage1(input_path, stage1_path, kordoc_version="4.9.0")
                classified = run_pass1(stage1_text, str(input_path), pass1_path, model, host or "http://localhost:11434", backend=backend)
                title = derive_title_from_filename(str(input_path))
                return run_pass2(stage1_text, classified, title)
        except Exception as e:
            raise HTTPException(500, f"변환 실패: {type(e).__name__}: {e}") from e
        finally:
            # 요청량이 낮은 문서 변환 API라 백그라운드 배치 전송을 기다리기보다, 요청 하나
            # 끝날 때마다 바로 보내서 Langfuse 대시보드에서 지연 없이 확인할 수 있게 한다.
            flush()
