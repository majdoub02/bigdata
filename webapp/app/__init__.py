import logging
import os

from flask import Flask

from .services import check_all


def create_app():
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("webapp")

    app = Flask(__name__)
    app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "dev-only-change-me")
    app.config["MAX_CONTENT_LENGTH"] = int(os.getenv("MAX_UPLOAD_MB", "100")) * 1024 * 1024

    from .routes import bp
    app.register_blueprint(bp)

    # Test de connectivité au démarrage (n'empêche pas l'application de démarrer)
    for name, result in check_all().items():
        level = logging.INFO if result["status"] == "up" else logging.ERROR
        log.log(level, "Connexion %s : %s (%s ms) %s",
                name, result["status"], result["response_ms"], result["detail"])

    return app
