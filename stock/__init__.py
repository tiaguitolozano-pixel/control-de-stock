"""Control de stock para depósito logístico con varias empresas clientes."""
import os
from datetime import date

from flask import Flask, flash, redirect, request

from . import db
from .formato import formato_fecha, formato_numero, texto_kilos, texto_pallets
from .qr import qr_svg

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
    # Las fotos de los remitos van al lado de la base de datos (datos/fotos).
    app.config.setdefault("FOTOS", os.path.join(os.path.dirname(app.config["DATABASE"]), "fotos"))
    # Tope para lo que se sube de una vez (las fotos se achican en el navegador antes de mandarse).
    if not app.config.get("MAX_CONTENT_LENGTH"):
        app.config["MAX_CONTENT_LENGTH"] = 80 * 1024 * 1024

    app.teardown_appcontext(db.close_db)
    app.register_error_handler(413, _demasiado_grande)
    app.jinja_env.filters["num"] = formato_numero
    app.jinja_env.filters["fecha"] = formato_fecha
    app.jinja_env.filters["pallets"] = texto_pallets
    app.jinja_env.filters["kilos"] = texto_kilos
    app.jinja_env.globals["hoy"] = lambda: date.today().isoformat()
    app.jinja_env.globals["qr_svg"] = qr_svg

    with app.app_context():
        db.init_db()

    from .deposito import bp as bp_deposito
    from .rutas import bp

    app.register_blueprint(bp)
    app.register_blueprint(bp_deposito)
    return app


def _demasiado_grande(error):
    limite = round(request.max_content_length / (1024 * 1024)) if request.max_content_length else 0
    flash(f"Lo que se mandó pesa demasiado (más de {limite} MB): probá con menos fotos por vez. "
          "No se guardó nada.", "error")
    volver = request.referrer or ""
    return redirect(volver if volver.startswith(request.host_url) else "/")
