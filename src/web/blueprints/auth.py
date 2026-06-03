from flask import Blueprint, render_template, request, redirect, url_for, flash, session
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from auth.auth import attempt_login, logout_user, is_logged_in

bp = Blueprint("auth", __name__)


@bp.route("/login", methods=["GET", "POST"])
def login():
    if is_logged_in():
        return redirect(url_for("projects.index"))

    if request.method == "POST":
        email    = request.form.get("email", "").strip()
        password = request.form.get("password", "")
        ok, err  = attempt_login(email, password)
        if ok:
            flash(f"환영합니다!", "success")
            next_url = request.args.get("next") or url_for("projects.index")
            return redirect(next_url)
        flash(err, "error")

    return render_template("auth/login.html")


@bp.route("/logout")
def logout():
    logout_user()
    flash("로그아웃되었습니다.", "info")
    return redirect(url_for("auth.login"))
