"""Control de stock para depósito logístico con varias empresas clientes."""
import os
from datetime import date

from flask import Flask

from . import db
from .formato import formato_fecha, formato_numero, texto_pallets

__all__ = ["create_app", "formato_fecha", "formato_numero"]


def create_app(config=None):
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(
        SECRET_KEY=os.environ.get("STOCK_SECRET_KEY", "cambiar-esta-clave"),
        DATABASE=os.environ.get(
            "STOCK_DB", os.path.join(os.path.dirname(app.root_path), "datos", "stock.db")
        ),
    )
    if config:
        app.config.update(config)

    app.teardown_appcontext(db.close_db)
    app.jinja_env.filters["num"] = formato_numero
    app.jinja_env.filters["fecha"] = formato_fecha
    app.jinja_env.filters["pallets"] = texto_pallets
    app.jinja_env.globals["hoy"] = lambda: date.today().isoformat()

    with app.app_context():
        db.init_db()

    from .rutas import bp

    app.register_blueprint(bp)
    return app
