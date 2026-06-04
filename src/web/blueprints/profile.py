"""
프로필 Blueprint — LLM 설정 (provider + model + API 키).
"""
from flask import (Blueprint, render_template, request,
                   redirect, url_for, flash, session)
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import login_required
from auth.crypto import mask_api_key
from db.queries import get_user, get_user_llm_settings, save_user_llm_settings

bp = Blueprint("profile", __name__)


@bp.route("/")
@login_required
def index():
    user     = get_user(session["user_id"])
    llm      = get_user_llm_settings(session["user_id"])
    masked   = mask_api_key(llm["api_key"]) if llm["api_key"] else ""

    from extractors.providers import list_providers
    providers = list_providers()

    return render_template("profile/index.html",
                           user=user, llm=llm,
                           masked_key=masked, providers=providers)


@bp.route("/llm", methods=["POST"])
@login_required
def save_llm():
    provider = request.form.get("provider", "claude").strip()
    model    = request.form.get("model", "").strip() or None
    key      = request.form.get("api_key", "").strip()

    # provider 유효성
    from extractors.providers import PROVIDERS
    if provider not in PROVIDERS:
        flash(f"지원하지 않는 provider: {provider}", "error")
        return redirect(url_for("profile.index"))

    # 키 입력된 경우 형식 검증
    if key:
        p = PROVIDERS[provider]
        if not p.validate_key(key):
            flash(
                f"올바른 {p.provider_name} API 키 형식이 아닙니다. "
                f"({p.key_prefix}...로 시작해야 합니다)",
                "error"
            )
            return redirect(url_for("profile.index"))

    # 저장 (키 미입력이면 기존 키 유지)
    save_user_llm_settings(
        session["user_id"],
        provider=provider,
        model=model,
        plain_key=key if key else None,
    )
    flash("✅ LLM 설정이 저장되었습니다.", "success")
    return redirect(url_for("profile.index"))


@bp.route("/api-key/delete", methods=["POST"])
@login_required
def delete_api_key():
    save_user_llm_settings(session["user_id"],
                           provider="claude", plain_key="")
    flash("API 키가 삭제되었습니다.", "info")
    return redirect(url_for("profile.index"))
