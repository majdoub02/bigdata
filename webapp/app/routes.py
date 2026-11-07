from flask import Blueprint, jsonify, render_template

from .services import check_all, overall_status

bp = Blueprint("main", __name__)


@bp.route("/")
def index():
    return render_template("index.html", checks=check_all())


@bp.route("/health")
def health():
    checks = check_all()
    status = overall_status(checks)
    code = 200 if status == "healthy" else 503
    return jsonify({"status": status, "dependencies": checks}), code
