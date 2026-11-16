"""Control de stock para depósito logístico con varias empresas clientes."""
import os
from datetime import date

from flask import Flask

from . import db


def formato_numero(valor):
    """1234.5 -> '1.234,5' ; 10.0 -> '10' (formato argentino)."""
    if valor is None:
        return ""
    valor = float(valor)
    if valor == int(valor):
        texto = f"{int(valor):,}"
    else:
        texto = f"{valor:,.3f}".rstrip("0").rstrip(".")
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def formato_fecha(valor):
    """'2026-11-16' -> '16/11/2026'."""
    if not valor:
        return ""
    partes = str(valor)[:10].split("-")
    if len(partes) != 3:
        return valor
    return f"{partes[2]}/{partes[1]}/{partes[0]}"


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
    app.jinja_env.globals["hoy"] = lambda: date.today().isoformat()

    with app.app_context():
        db.init_db()

    from .rutas import bp

    app.register_blueprint(bp)
    return app
