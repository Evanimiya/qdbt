from flask import Blueprint, render_template, request, redirect, url_for, flash, session
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import attempt_login, logout_user, is_logged_in

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if is_logged_in():
        token = request.args.get('_t', '')
        return redirect(url_for("projects.index") + (f'?_t={token}' if token else ''))

    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        ok, err  = attempt_login(email, password)
        if ok:
            from auth.token_session import create_token
            token = create_token(
                session.get('user_id', ''),
                session.get('user_name', ''),
                session.get('user_role', ''),
                session.get('user_email', ''),
            )
            next_url = request.args.get("next") or "/"
            # localStorage에 토큰 저장 후 리디렉션 (쿠키 없이 동작)
            return f"""<!DOCTYPE html><html><head>
<meta charset="UTF-8">
<script>
  try {{ localStorage.setItem('qdbt_t', '{token}'); }} catch(e) {{}}
  window.location.replace({(next_url + '?_t=' + token).__repr__()});
</script>
</head><body style="font-family:sans-serif;padding:2rem;">로그인 중...</body></html>"""
        flash(err, "error")

    return render_template("auth/login.html")


@bp.route("/logout")
def logout():
    from auth.token_session import delete_token
    token = request.args.get('_t', '')
    if token:
        delete_token(token)
    logout_user()
    return """<!DOCTYPE html><html><head>
<meta charset="UTF-8">
<script>
  try { localStorage.removeItem('qdbt_t'); } catch(e) {}
  window.location.replace('/auth/login');
</script>
</head><body style="font-family:sans-serif;padding:2rem;">로그아웃 중...</body></html>"""
