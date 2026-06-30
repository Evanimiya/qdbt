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

    # 현재 provider의 알려진 모델 id 목록 (직접입력 모델 판별용)
    known_model_ids = []
    for p in providers:
        if p["id"] == llm["provider"]:
            known_model_ids = [m[0] for m in p["models"]]
            break

    return render_template("profile/index.html",
                           user=user, llm=llm,
                           masked_key=masked, providers=providers,
                           known_model_ids=known_model_ids)


@bp.route("/llm", methods=["POST"])
@login_required
def save_llm():
    provider    = request.form.get("provider", "claude").strip()
    # 모델: 드롭다운(model_select) + 직접입력(model_custom) 처리
    model_select = request.form.get("model_select", "").strip()
    model_custom = request.form.get("model_custom", "").strip()
    if model_select == "__custom__":
        model = model_custom or None          # 직접 입력값 사용 (게이트웨이 모델명)
    else:
        model = model_select or None          # 드롭다운 선택값
    # 구버전 호환: model 필드가 직접 올 수도 있음
    if not model and not model_select:
        model = request.form.get("model", "").strip() or None
    key         = request.form.get("api_key", "").strip()
    base_url    = request.form.get("base_url", "").strip() or None
    verify_ssl  = request.form.get("verify_ssl") != "0"

    # provider 유효성
    from extractors.providers import PROVIDERS
    if provider not in PROVIDERS:
        flash(f"지원하지 않는 provider: {provider}", "error")
        return redirect(url_for("profile.index"))

    # 키 형식 검증 — base_url이 설정된 경우 prefix 검증 생략 (커스텀 엔드포인트)
    if key and not base_url:
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
        base_url=base_url,
        verify_ssl=verify_ssl,
    )
    flash("✅ LLM 설정이 저장되었습니다.", "success")
    return redirect(url_for("profile.index"))


@bp.route("/llm/models.json", methods=["POST"])
@login_required
def fetch_models():
    """게이트웨이(base_url)의 /models 를 조회해 모델 목록 반환.

    프로필 화면의 '모델 불러오기' 버튼이 호출.
    화면에서 입력한 base_url/api_key 를 우선 사용하고,
    비어있으면 저장된 설정을 사용한다.
    """
    from flask import jsonify
    base_url   = (request.form.get("base_url", "") or "").strip()
    api_key    = (request.form.get("api_key", "") or "").strip()
    verify_ssl = request.form.get("verify_ssl") != "0"

    # 화면 입력이 비어있으면 저장된 설정 사용
    if not base_url or not api_key:
        saved = get_user_llm_settings(session["user_id"])
        base_url = base_url or (saved.get("base_url") or "")
        api_key  = api_key  or (saved.get("api_key") or "")

    # base_url이 없으면(게이트웨이 미사용 = OpenAI 직접 호출) OpenAI 기본 주소 사용.
    # 이렇게 하면 맥북처럼 게이트웨이 없는 환경에서도 OpenAI의 /models를
    # 조회해 gpt-5.5 등 최신 모델 목록을 동적으로 가져온다.
    provider = (request.form.get("provider", "") or "").strip()
    if not base_url:
        if provider == "gpt" or (api_key or "").startswith("sk-"):
            base_url = "https://api.openai.com/v1"

    if not base_url:
        return jsonify({"ok": False, "error": "base_url(게이트웨이 주소)이 필요합니다."}), 400
    if not api_key:
        return jsonify({"ok": False, "error": "API 키가 필요합니다."}), 400

    try:
        import httpx
    except ImportError:
        return jsonify({"ok": False, "error": "httpx 라이브러리가 없습니다."}), 500

    url = base_url.rstrip("/") + "/models"
    try:
        client = httpx.Client(verify=verify_ssl, timeout=20)
        r = client.get(url, headers={"Authorization": f"Bearer {api_key}"})
        if r.status_code != 200:
            return jsonify({
                "ok": False,
                "error": f"게이트웨이 응답 오류 (HTTP {r.status_code}): {r.text[:200]}"
            }), 502
        data = r.json()
        # OpenAI 호환 형식: {"data": [{"id": "..."}]} / 또는 단순 리스트
        if isinstance(data, list):
            items = data
        elif isinstance(data, dict):
            items = data.get("data", [])
        else:
            items = []
        models = []
        for m in items:
            mid = m.get("id") if isinstance(m, dict) else m
            if mid:
                models.append(mid)
        if not models:
            return jsonify({"ok": False,
                            "error": "모델 목록이 비어있습니다.",
                            "raw": str(data)[:300]}), 200

        # OpenAI 직접 호출 시 모델이 매우 많고 추출과 무관한 것(임베딩,
        # whisper, dall-e, tts 등)이 섞임. 채팅/추출용 gpt·o 계열만 추려
        # 최신순으로 정렬해 보여준다. (게이트웨이는 보통 목록이 짧아 그대로 둠)
        if "api.openai.com" in base_url:
            chat_like = [m for m in models
                         if (m.startswith("gpt-") or m.startswith("o1")
                             or m.startswith("o3") or m.startswith("o4")
                             or m.startswith("chatgpt"))
                         and not any(x in m for x in
                                     ("embedding", "whisper", "tts",
                                      "dall-e", "image", "audio", "realtime",
                                      "moderation", "transcribe", "search"))]
            if chat_like:
                # 최신 버전이 위로 오도록 역순 정렬
                models = sorted(chat_like, reverse=True)

        return jsonify({"ok": True, "models": models})
    except httpx.ConnectError as e:
        return jsonify({"ok": False, "error": f"연결 실패: {e}"}), 502
    except Exception as e:
        return jsonify({"ok": False, "error": f"{type(e).__name__}: {e}"}), 500


@bp.route("/api-key/delete", methods=["POST"])
@login_required
def delete_api_key():
    save_user_llm_settings(session["user_id"],
                           provider="claude", plain_key="")
    flash("API 키가 삭제되었습니다.", "info")
    return redirect(url_for("profile.index"))
