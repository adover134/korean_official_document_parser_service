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

실행:
    uvicorn hwp_hierarchical_md.api:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import PlainTextResponse

from .cli import _API_KEY_ENV_VARS, _DEFAULT_BASE_URLS, check_npx
from .llm_backend import OllamaBackend, OpenAICompatBackend
from .run_pipeline import derive_title_from_filename, run_pass1, run_pass2, run_stage1

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
async def convert(file: UploadFile = File(...)) -> str:
    """HWP/HWPX 파일을 업로드하면 계층 구조가 보존된 Markdown을 반환한다."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise HTTPException(400, f"지원하지 않는 확장자: {suffix!r} (지원: {sorted(SUPPORTED_EXTENSIONS)})")

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
            stage1_text = run_stage1(input_path, stage1_path, kordoc_version="4.9.0")
            classified = run_pass1(stage1_text, str(input_path), pass1_path, model, host or "http://localhost:11434", backend=backend)
            title = derive_title_from_filename(str(input_path))
            return run_pass2(stage1_text, classified, title)
        except Exception as e:
            raise HTTPException(500, f"변환 실패: {type(e).__name__}: {e}") from e
