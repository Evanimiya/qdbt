"""
입찰 데이터 관리 시스템 v2 — 공통 설정
"""
import os
from pathlib import Path

ROOT = Path(__file__).parent.parent.resolve()

# 데이터
DB_PATH        = ROOT / "data" / "qdbt.db"
UPLOAD_DIR     = ROOT / "data" / "uploads"
EXTRACT_DIR    = ROOT / "data" / "extractions"

# 생성
for d in [UPLOAD_DIR, EXTRACT_DIR]:
    d.mkdir(parents=True, exist_ok=True)

# API
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
LLM_MODEL         = os.environ.get("LLM_MODEL", "claude-sonnet-4-20250514")

# 업로드
MAX_UPLOAD_MB     = 50
ALLOWED_EXTENSIONS = {".xlsx", ".pdf", ".docx"}

# 권한
ROLES = ["admin", "manager", "viewer"]

# 버전
VERSION = "0.8.5-test"
STATUS  = "test"
