"""Lo que comparten las pantallas que cargan mercadería (ingreso, egreso y trasvaso).

Leer los renglones de productos del formulario, revisarlos, chequear el stock de lo que sale y
revisar si un ingreso se puede anular.
"""
from collections import defaultdict

from . import insumos as ins
from . import pallets as pl
from .db import get_db, stock_de_lotes, stock_por_lote_id
from .servicios import chequear_lote, obtener_o_crear_lote, parse_fecha, parse_numero

CAMPOS_RENGLON = ("producto_id", "cantidad", "lote", "vencimiento", "lote_id")


def _int_o_none(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def leer_renglones(form, prefijo=""):
    """Los renglones de productos del formulario, sin los que quedaron vacíos.

    prefijo: el trasvaso tiene dos tablas (lo que sale y lo que entra) y los campos de una llevan prefijo.
    """
    columnas = {c: form.getlist(prefijo + c) for c in CAMPOS_RENGLON}
    renglones = []
    for n in range(len(columnas["producto_id"])):
        r = {c: (v[n] if n < len(v) else "") for c, v in columnas.items()}
        r["producto_id"] = _int_o_none(r["producto_id"])
        r["lote_id"] = _int_o_none(r["lote_id"])
        r["lote"] = (r["lote"] or "").strip()
        if r["producto_id"] or (r["cantidad"] or "").strip():
            renglones.append(r)
    return renglones


def productos_de_empresa(empresa_id):
    if not empresa_id:
        return {}
    return {r["id"]: r for r in get_db().execute("SELECT * FROM productos WHERE empresa_id = ?", (empresa_id,))}


def revisar_renglones(tipo, renglones, productos):
    """Revisa los renglones y junta los repetidos (mismo producto y lote se suman).

    tipo 'INGRESO': el lote se escribe y lleva vencimiento. tipo 'EGRESO': se elige un lote que ya existe.
    Devuelve (items, vencimientos, errores): items = {(producto_id, lote o lote_id): cantidad} y
    vencimientos = {(producto_id, LOTE): (lote, vencimiento)} (solo en el ingreso).
    """
    items, vencimientos, errores = defaultdict(float), {}, []
    for r in renglones:
        cant = parse_numero(r["cantidad"])
        p = productos.get(r["producto_id"])
        if not r["producto_id"]:
            errores.append("Hay un renglón sin producto.")
            continue
        if p is None:
            errores.append("Hay productos que no son de la empresa elegida.")
            continue
        if cant is None or cant <= 0:
            errores.append(f"{p['codigo']}: la cantidad tiene que ser mayor a cero.")
            continue
        if tipo == "INGRESO":
            vto = parse_fecha(r["vencimiento"])
            if vto is None:
                errores.append(f"{p['codigo']}: la fecha de vencimiento no es válida.")
                continue
            clave = (p["id"], r["lote"].upper())
            if clave in vencimientos and vto and vencimientos[clave][1] and vencimientos[clave][1] != vto:
                errores.append(f"{p['codigo']}: el lote {r['lote']} está dos veces con distinto vencimiento.")
                continue
            if clave not in vencimientos or vto:
                vencimientos[clave] = (r["lote"], vto)
            error = chequear_lote(p["id"], r["lote"], vto, p["codigo"])
            if error:
                errores.append(error)
                continue
        else:
            clave = (p["id"], r["lote_id"])
            if not r["lote_id"]:
                errores.append(f"{p['codigo']}: elegí de qué lote sale.")
                continue
        items[clave] += cant
    return items, vencimientos, errores


def chequear_stock(items, productos):
    """Errores si algún lote no tiene stock suficiente para lo que sale. items = {(producto_id, lote_id): cantidad}."""
    errores = []
    if not items:
        return errores
    lotes = {l["id"]: l for l in stock_de_lotes(lote_ids=[lid for _, lid in items], solo_con_stock=False)}
    for (pid, lid), cant in items.items():
        l = lotes.get(lid)
        if l is None or l["producto_id"] != pid:
            errores.append(f"{productos[pid]['codigo']}: el lote elegido no corresponde al producto.")
        elif cant > l["stock"] + 1e-9:
            nombre = f"lote {l['lote']}" if l["lote"] else "sin lote"
            errores.append(
                f"No hay stock suficiente de {l['codigo']} - {l['descripcion']} ({nombre}): "
                f"hay {l['stock']:g} y querés sacar {cant:g}."
            )
    return errores


def lineas_de_ingreso(items, vencimientos):
    """Crea los lotes nuevos y devuelve [(producto_id, lote_id, cantidad)]. No hace commit."""
    lineas = []
    for (pid, clave_lote), cant in items.items():
        lote, vto = vencimientos[(pid, clave_lote)]
        lineas.append((pid, obtener_o_crear_lote(pid, lote, vto), cant))
    return lineas


def lineas_de_egreso(items, productos):
    """Devuelve ([(producto_id, lote_id, cantidad)], plan de pallets).

    Sin pallets escaneados, el programa decide de qué pallets sale (ver pallets.planear_salida).
    Hay que llamarla antes de guardar el egreso.
    """
    lineas = [(pid, lid, cant) for (pid, lid), cant in items.items()]
    plan = {(pid, lid): pl.planear_salida(lid, cant, productos[pid]["unidades_por_pallet"])
            for pid, lid, cant in lineas}
    return lineas, plan


def problema_al_anular_ingreso(movimiento, vuelve=None):
    """Por qué no se puede anular un ingreso (texto para mostrar), o None si se puede.

    vuelve: {lote_id: cantidad} que vuelve al stock a la vez (en un trasvaso se anula también lo que salió).
    """
    db = get_db()
    movimiento_id = movimiento["id"]
    # Si ya se sacó mercadería de este ingreso, anularlo dejaría stock negativo.
    items = db.execute(
        """SELECT i.lote_id, i.cantidad, p.codigo, l.lote FROM movimiento_items i
           JOIN productos p ON p.id = i.producto_id JOIN lotes l ON l.id = i.lote_id
           WHERE i.movimiento_id = ?""",
        (movimiento_id,),
    ).fetchall()
    stock = stock_por_lote_id({i["lote_id"] for i in items})
    a_restar = defaultdict(float)
    for i in items:
        a_restar[i["lote_id"]] += i["cantidad"]
    vuelve = vuelve or {}
    problemas = [
        i["codigo"] + (f" (lote {i['lote']})" if i["lote"] else "") for i in items
        if stock.get(i["lote_id"], 0) - a_restar[i["lote_id"]] + vuelve.get(i["lote_id"], 0) < -1e-9
    ]
    if problemas:
        return ("No se puede anular: el stock quedaría negativo para " + ", ".join(sorted(set(problemas)))
                + ". Primero anulá los egresos que usaron esa mercadería.")
    problemas = pl.problemas_al_anular(movimiento_id)
    if problemas:
        return ("No se puede anular: " + "; ".join(problemas)
                + ". Primero anulá esos egresos o desarmá esos pallets.")
    problemas = ins.problemas_al_anular(movimiento)
    if problemas:
        return ("No se puede anular: " + "; ".join(problemas)
                + ". Primero anulá los egresos que sacaron esos pallets.")
    return None
