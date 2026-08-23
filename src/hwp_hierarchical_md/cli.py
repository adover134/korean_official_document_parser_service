"""정식 CLI 진입점 — 단일 파일 처리(run_pipeline.py와 동일)에 더해 폴더 배치 처리와 사전 환경 점검을
추가한다.

배경: 원래(`run_pipeline.py`) 단일 파일만 처리했고, 42개 문서 일괄 처리는 저장소 안 하드코딩된
경로를 쓰는 1회성 스크립트(`run_all_42.py`, 공개 스킬 저장소에는 포함하지 않음)로만 가능했다. 이
스크립트를 일반 사용자가 재사용하려면 (a) 입력이 파일이든 폴더든 알아서 처리하고, (b) kordoc(npx)나
Ollama가 준비 안 된 상태에서 문서 처리 도중 알아보기 힘든 subprocess 에러로 죽는 대신 시작 전에
분명한 진단 메시지를 내야 한다 — 이 두 가지가 "서비스화 가능한 CLI"의 최소 조건이라고 판단해서
추가했다.

사용법:
    hwp2md convert <input.hwp|hwpx> [-o OUTPUT] [옵션]
    hwp2md convert <input_dir> -o <output_dir> [--recursive] [옵션]
    hwp2md doctor    # 환경 점검만 실행
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

from .run_pipeline import PASS1_DIR, PASS2_DIR, STAGE1_DIR, derive_title_from_filename, run_pass1, run_pass2, run_stage1

SUPPORTED_EXTENSIONS = {".hwp", ".hwpx"}


def check_npx() -> str | None:
    """kordoc 실행에 필요한 npx(Node.js) 존재 여부. 문제 없으면 None, 있으면 사용자용 진단 메시지."""
    if shutil.which("npx") is None:
        return (
            "npx(Node.js)를 찾을 수 없습니다. kordoc(HWP/HWPX 변환 라이브러리)을 npx로 실행하므로 "
            "Node.js가 설치되어 있어야 합니다. https://nodejs.org 에서 설치 후 다시 시도하세요."
        )
    return None


def check_ollama(host: str, model: str) -> str | None:
    """Ollama 서버 도달 가능 여부 + 지정 모델이 pull되어 있는지. 문제 없으면 None."""
    try:
        with urllib.request.urlopen(f"{host}/api/tags", timeout=3) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError):
        return (
            f"Ollama 서버({host})에 연결할 수 없습니다. 로컬에서 `ollama serve`를 먼저 실행하거나, "
            "--host로 다른 주소를 지정하세요."
        )
    names = {m.get("name", "").split(":")[0] for m in body.get("models", [])}
    wanted = model.split(":")[0]
    if wanted not in names:
        return (
            f"Ollama에 '{model}' 모델이 없습니다. `ollama pull {model}`로 먼저 받으세요. "
            f"(현재 설치된 모델: {', '.join(sorted(names)) or '없음'})"
        )
    return None


def run_doctor(host: str, model: str) -> bool:
    """환경 점검만 실행하고 결과를 출력. 전부 통과하면 True."""
    problems = [p for p in (check_npx(), check_ollama(host, model)) if p]
    if not problems:
        print("환경 점검 통과: npx, Ollama 서버, 모델 모두 정상.")
        return True
    print("환경 점검에서 문제를 발견했습니다:")
    for p in problems:
        print(f"  - {p}")
    return False


_DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
}

# --api-key를 CLI 인자로 직접 주는 대신 표준 환경변수명으로도 읽는다 — 쉘 히스토리/프로세스 목록에
# 키가 평문으로 남는 걸 피하려는 목적(.env + `set -a; source .env`로 넣는 사용을 전제).
_API_KEY_ENV_VARS = {
    "openai": "OPENAI_API_KEY",
    "groq": "GROQ_API_KEY",
    "gemini": "GEMINI_API_KEY",
}


def build_backend(args: argparse.Namespace):
    """--backend 선택에 따라 LLMBackend 인스턴스를 만든다 (ollama가 기본, 하위 호환)."""
    if args.backend == "ollama":
        return None  # run_pass1이 model/host로 알아서 OllamaBackend를 만듦

    api_key = args.api_key or os.environ.get(_API_KEY_ENV_VARS.get(args.backend, ""))
    if not api_key:
        env_name = _API_KEY_ENV_VARS.get(args.backend, "?")
        raise SystemExit(f"--backend {args.backend}는 --api-key 또는 환경변수 {env_name}가 필요합니다.")
    from .llm_backend import OpenAICompatBackend

    base_url = args.base_url or _DEFAULT_BASE_URLS.get(args.backend)
    if not base_url:
        raise SystemExit(f"--backend {args.backend}는 --base-url을 직접 지정해야 합니다.")
    return OpenAICompatBackend(model=args.model, api_key=api_key, base_url=base_url)


def _process_one(
    input_path: Path,
    pipeline_root: Path,
    output_path: Path | None,
    model: str,
    host: str,
    kordoc_version: str,
    title: str | None,
    skip_existing_stage1: bool,
    backend=None,
) -> Path:
    """단일 파일을 Stage1->Pass1->Pass2로 처리하고 최종 결과 경로를 반환. 실패 시 예외 발생."""
    base = input_path.name
    stage1_path = pipeline_root / STAGE1_DIR / f"{base}.md"
    pass1_path = pipeline_root / PASS1_DIR / f"{base}.classified.json"
    pass2_path = output_path if output_path else pipeline_root / PASS2_DIR / f"{base}.md"

    if skip_existing_stage1 and stage1_path.exists():
        stage1_text = stage1_path.read_text(encoding="utf-8")
    else:
        stage1_text = run_stage1(input_path, stage1_path, kordoc_version)

    classified = run_pass1(stage1_text, str(stage1_path), pass1_path, model, host, backend=backend)

    final_title = title or derive_title_from_filename(str(stage1_path))
    final_md = run_pass2(stage1_text, classified, final_title)

    pass2_path.parent.mkdir(parents=True, exist_ok=True)
    pass2_path.write_text(final_md, encoding="utf-8")
    return pass2_path


def run_convert(args: argparse.Namespace) -> int:
    input_path = Path(args.input)
    if not input_path.exists():
        print(f"입력 경로를 찾을 수 없습니다: {input_path}", file=sys.stderr)
        return 1

    try:
        backend = build_backend(args)
    except SystemExit as e:
        print(str(e), file=sys.stderr)
        return 1

    if not args.skip_doctor:
        # backend가 ollama가 아니면 로컬 Ollama 점검은 의미가 없다 — npx(kordoc)만 확인
        checks = [check_npx()]
        if backend is None:
            checks.append(check_ollama(args.host, args.model))
        problems = [p for p in checks if p]
        if problems:
            print("시작 전 환경 점검에서 문제를 발견했습니다 (--skip-doctor로 건너뛸 수 있음):", file=sys.stderr)
            for p in problems:
                print(f"  - {p}", file=sys.stderr)
            return 1

    pipeline_root = Path(args.pipeline_root)

    if input_path.is_file():
        output_path = Path(args.output) if args.output else None
        try:
            result_path = _process_one(
                input_path, pipeline_root, output_path, args.model, args.host,
                args.kordoc_version, args.title, args.skip_existing_stage1, backend=backend,
            )
        except Exception as e:
            print(f"실패: {input_path.name} — {type(e).__name__}: {e}", file=sys.stderr)
            return 1
        print(f"완료 -> {result_path}")
        return 0

    # 폴더 배치 모드
    if not args.output:
        print("폴더를 입력으로 줄 때는 -o/--output(결과 저장 폴더)이 필요합니다.", file=sys.stderr)
        return 1
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    pattern = "**/*" if args.recursive else "*"
    files = sorted(
        p for p in input_path.glob(pattern) if p.is_file() and p.suffix.lower() in SUPPORTED_EXTENSIONS
    )
    if not files:
        print(f"{input_path} 안에 .hwp/.hwpx 파일이 없습니다.", file=sys.stderr)
        return 1

    print(f"대상 {len(files)}개 파일 처리 시작")
    failures: list[tuple[Path, str]] = []
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {f.name}")
        out_path = output_dir / f"{f.name}.md"
        try:
            _process_one(
                f, pipeline_root, out_path, args.model, args.host,
                args.kordoc_version, None, args.skip_existing_stage1, backend=backend,
            )
            print(f"  완료 -> {out_path}")
        except Exception as e:
            print(f"  실패: {type(e).__name__}: {e}", file=sys.stderr)
            failures.append((f, str(e)))

    print(f"\n전체 완료: {len(files) - len(failures)}/{len(files)} 성공")
    if failures:
        print("실패 목록:", file=sys.stderr)
        for f, err in failures:
            print(f"  - {f.name}: {err}", file=sys.stderr)
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hwp2md", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    convert = sub.add_parser("convert", help="HWP/HWPX 파일(또는 폴더)을 Markdown으로 변환")
    convert.add_argument("input", help="입력 파일 또는 폴더 경로")
    convert.add_argument("-o", "--output", help="출력 경로 (파일 입력: 결과 .md 경로 / 폴더 입력: 결과 저장 폴더, 필수)")
    convert.add_argument("--pipeline-root", default="pipeline", help="Stage1/Pass1 중간 산출물 저장 위치 (기본: pipeline/)")
    convert.add_argument("--model", default="qwen3.5:9b", help="ollama 모델명, 또는 다른 backend일 때 그 제공자의 모델명")
    convert.add_argument("--host", default="http://localhost:11434", help="--backend ollama일 때만 사용")
    convert.add_argument(
        "--backend", choices=["ollama", "openai", "groq", "gemini"], default="ollama",
        help="Pass1b(헤더 계층 판단) LLM 호출 방식. ollama(기본, 로컬) 외에는 --api-key 필요",
    )
    convert.add_argument("--api-key", help="--backend가 ollama가 아닐 때 필요한 API 키")
    convert.add_argument("--base-url", help="OpenAI 호환 엔드포인트 base URL (openai/groq/gemini는 기본값 있음, 다른 제공자는 직접 지정)")
    convert.add_argument("--kordoc-version", default="4.9.0")
    convert.add_argument("--title", help="문서 제목 (파일 입력에만 적용, 미지정 시 파일명에서 유도)")
    convert.add_argument("--recursive", action="store_true", help="폴더 입력 시 하위 폴더까지 재귀 탐색")
    convert.add_argument("--skip-existing-stage1", action="store_true", help="기존 Stage1 결과가 있으면 재사용")
    convert.add_argument("--skip-doctor", action="store_true", help="시작 전 환경 점검을 건너뜀")

    doctor = sub.add_parser("doctor", help="npx/Ollama 등 실행 환경만 점검")
    doctor.add_argument("--model", default="qwen3.5:9b")
    doctor.add_argument("--host", default="http://localhost:11434")

    return ap


def _load_dotenv_if_present() -> None:
    """cwd에 .env가 있으면 로드(있으면 GROQ_API_KEY 등을 거기서 읽게). python-dotenv가 없으면
    조용히 건너뜀 — 필수 의존성으로 만들지 않는다(대부분 --api-key로 직접 줘도 되므로)."""
    if not Path(".env").exists():
        return
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def main() -> None:
    _load_dotenv_if_present()
    args = build_parser().parse_args()
    if args.command == "doctor":
        sys.exit(0 if run_doctor(args.host, args.model) else 1)
    elif args.command == "convert":
        sys.exit(run_convert(args))


if __name__ == "__main__":
    main()
