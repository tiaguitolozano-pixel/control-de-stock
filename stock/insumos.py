"""Insumos adicionales de cada remito: pallets (propios o del cliente), film stretch, etc.

Los insumos "con propietario" (los pallets) se anotan separando los propios del depósito y los del
cliente. Los del cliente llevan saldo por empresa: entran con los ingresos, salen con los egresos y el
saldo nunca puede quedar negativo. Los propios y los demás insumos solo quedan anotados en el remito.
"""
from collections import defaultdict

from .db import get_db
from .formato import formato_numero
from .servicios import parse_numero

PROPIETARIOS = {"PROPIO": "Propios", "CLIENTE": "Del cliente"}
EPS = 1e-6


def lista(solo_activos=True):
    sql = "SELECT * FROM insumos"
    if solo_activos:
        sql += " WHERE activo = 1"
    return get_db().execute(sql + " ORDER BY con_propietario DESC, nombre").fetchall()


def campos(insumo):
    """Nombres de los campos del formulario para un insumo: [(campo, propietario)]."""
    if insumo["con_propietario"]:
        return [(f"ins_{insumo['id']}_{p}", p) for p in PROPIETARIOS]
    return [(f"ins_{insumo['id']}", None)]


def leer_formulario(form):
    """Lee las cantidades de insumos del formulario.

    Devuelve (renglones, errores, textos): renglones es una lista de (insumo_id, propietario, cantidad)
    y textos guarda lo escrito, para volver a mostrar el formulario si hay errores.
    """
    renglones, errores, textos = [], [], {}
    for ins in lista():
        for campo, propietario in campos(ins):
            texto = (form.get(campo) or "").strip()
            if not texto:
                continue
            textos[campo] = texto
            cantidad = parse_numero(texto)
            nombre = ins["nombre"] + (f" {PROPIETARIOS[propietario].lower()}" if propietario else "")
            if cantidad is None or cantidad < 0:
                errores.append(f"Insumos: la cantidad de {nombre.lower()} no es un número válido.")
            elif cantidad > EPS:
                renglones.append((ins["id"], propietario, cantidad))
    return renglones, errores, textos


def saldos_cliente(empresa_id=None):
    """{(empresa_id, insumo_id): saldo} de los insumos del cliente (los pallets) en el depósito."""
    sql = """SELECT m.empresa_id, mi.insumo_id,
                    SUM(CASE WHEN m.tipo = 'INGRESO' THEN mi.cantidad ELSE -mi.cantidad END) AS saldo
             FROM movimiento_insumos mi JOIN movimientos m ON m.id = mi.movimiento_id
             WHERE m.anulado = 0 AND mi.propietario = 'CLIENTE'"""
    params = []
    if empresa_id:
        sql += " AND m.empresa_id = ?"
        params.append(empresa_id)
    filas = get_db().execute(sql + " GROUP BY m.empresa_id, mi.insumo_id", params).fetchall()
    return {(r["empresa_id"], r["insumo_id"]): round(r["saldo"], 6) for r in filas}


def saldos_por_empresa():
    """Para mostrar: cada empresa con el saldo de cada insumo del cliente (solo las que tuvieron movimientos)."""
    filas = get_db().execute(
        """SELECT e.id AS empresa_id, e.nombre AS empresa, i.id AS insumo_id, i.nombre AS insumo, i.unidad,
                  SUM(CASE WHEN m.tipo = 'INGRESO' THEN mi.cantidad ELSE -mi.cantidad END) AS saldo,
                  MAX(m.fecha) AS ultimo
           FROM movimiento_insumos mi
           JOIN movimientos m ON m.id = mi.movimiento_id
           JOIN empresas e ON e.id = m.empresa_id
           JOIN insumos i ON i.id = mi.insumo_id
           WHERE m.anulado = 0 AND mi.propietario = 'CLIENTE'
           GROUP BY e.id, i.id ORDER BY e.nombre, i.nombre"""
    ).fetchall()
    return [{**dict(r), "saldo": round(r["saldo"], 6)} for r in filas]


def _nombres():
    return {r["id"]: r for r in get_db().execute("SELECT * FROM insumos")}


def _texto_cantidad(cantidad, unidad):
    return f"{formato_numero(cantidad)} {unidad}"


def chequear_egreso(empresa_id, renglones):
    """Errores si en un egreso salen más pallets del cliente de los que tiene en el depósito."""
    errores = []
    saldos = saldos_cliente(empresa_id)
    nombres = _nombres()
    salen = defaultdict(float)
    for iid, propietario, cantidad in renglones:
        if propietario == "CLIENTE":
            salen[iid] += cantidad
    for iid, cantidad in salen.items():
        hay = saldos.get((empresa_id, iid), 0)
        if cantidad > hay + EPS:
            ins = nombres[iid]
            errores.append(
                f"{ins['nombre']} del cliente: en el depósito hay {_texto_cantidad(hay, ins['unidad'])} de esta empresa "
                f"y querés sacar {_texto_cantidad(cantidad, ins['unidad'])}. Si ya estaban antes de empezar a usar "
                f"el programa, cargá primero un ingreso solo con esos {ins['nombre'].lower()} (por ejemplo, remito "
                f"'Saldo inicial')."
            )
    return errores


def problemas_al_anular(movimiento):
    """Al anular un ingreso, los pallets del cliente que entraron con él no pueden haber salido ya."""
    if movimiento["tipo"] != "INGRESO":
        return []
    renglones = de_movimiento(movimiento["id"])
    saldos = saldos_cliente(movimiento["empresa_id"])
    problemas = []
    entraron = defaultdict(float)
    for r in renglones:
        if r["propietario"] == "CLIENTE":
            entraron[(r["insumo_id"], r["nombre"], r["unidad"])] += r["cantidad"]
    for (iid, nombre, unidad), cantidad in entraron.items():
        hay = saldos.get((movimiento["empresa_id"], iid), 0)
        if hay - cantidad < -EPS:
            problemas.append(
                f"los {nombre.lower()} del cliente quedarían en negativo (hay {_texto_cantidad(hay, unidad)} "
                f"y este ingreso trajo {_texto_cantidad(cantidad, unidad)})"
            )
    return problemas


def de_movimiento(movimiento_id):
    return get_db().execute(
        """SELECT mi.insumo_id, mi.propietario, mi.cantidad, i.nombre, i.unidad
           FROM movimiento_insumos mi JOIN insumos i ON i.id = mi.insumo_id
           WHERE mi.movimiento_id = ? ORDER BY i.con_propietario DESC, i.nombre, mi.propietario DESC""",
        (movimiento_id,),
    ).fetchall()


def nombre_renglon(r):
    """'Pallets del cliente', 'Film stretch'."""
    if r["propietario"] == "CLIENTE":
        return f"{r['nombre']} del cliente"
    if r["propietario"] == "PROPIO":
        return f"{r['nombre']} propios"
    return r["nombre"]
