import logging
import os

from flask import Flask, jsonify, request
from werkzeug.exceptions import HTTPException

from .errors import ApiError
from .services import check_all
from .validation import ValidationError

JSON_PREFIXES = ("/api", "/upload", "/search")


def _json_error(status, code, message):
    return jsonify({"error": {"code": code, "message": message}}), status


def create_app():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("webapp")

    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-only-change-me")
    max_mb = int(os.getenv("MAX_UPLOAD_MB", "100"))
    app.config["MAX_CONTENT_LENGTH"] = max_mb * 1024 * 1024

    from .api import bp as api_bp
    from .routes import bp
    app.register_blueprint(bp)
    app.register_blueprint(api_bp)

    # ----- Gestion des erreurs : toujours du JSON pour l'API -----
    @app.errorhandler(ApiError)
    def handle_api_error(exc):
        return _json_error(exc.status, exc.code, exc.message)

    @app.errorhandler(ValidationError)
    def handle_validation_error(exc):
        status = 415 if exc.code == "unsupported_extension" else 400
        return _json_error(status, exc.code, exc.message)

    @app.errorhandler(HTTPException)
    def handle_http_error(exc):
        if not request.path.startswith(JSON_PREFIXES):
            return exc
        if exc.code == 413:
            return _json_error(413, "file_too_large", f"Fichier trop volumineux (maximum {max_mb} Mo).")
        return _json_error(exc.code, exc.name.lower().replace(" ", "_"), exc.description)

    @app.errorhandler(Exception)
    def handle_unexpected(exc):
        log.exception("Erreur inattendue")
        if request.path.startswith(JSON_PREFIXES):
            return _json_error(500, "internal_error", "Erreur interne du serveur.")
        return "Erreur interne du serveur.", 500

    # Test de connectivité au démarrage (n'empêche pas l'application de démarrer)
    for name, result in check_all().items():
        level = logging.INFO if result["status"] == "up" else logging.ERROR
        log.log(level, "Connexion %s : %s (%s ms) %s",
                name, result["status"], result["response_ms"], result["detail"])

    return app
