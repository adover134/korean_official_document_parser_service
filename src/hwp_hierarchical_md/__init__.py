"""hwp-hierarchical-md — 계층 구조 HWP/HWPX 공문서를 Markdown으로 변환하는 파이프라인.

공개 Claude Code Skill(hwp-hierarchical-md-skill)의 코드를 그대로 가져와 설치 가능한 패키지로
재구성한 버전. 서비스/CLI 확장(폴더 배치 처리, 환경 점검, 향후 웹 API 등)은 여기서만 진행하고,
공개 스킬 저장소에는 반영하지 않는다(README 참고)."""

from .run_pipeline import run_stage1, run_pass1, run_pass2

__all__ = ["run_stage1", "run_pass1", "run_pass2"]
__version__ = "0.1.0"
