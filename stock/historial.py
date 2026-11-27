"""Historial de movimientos como un libro: por producto y por lote, de arriba (lo más viejo) hacia abajo.

Dentro de un mismo día van primero los ingresos y después los egresos: lo que entra un día se puede sacar
ese mismo día, así que el saldo no se ve negativo por el orden en que se cargaron los remitos.
"""
from collections import defaultdict

from .db import get_db
from .fotos import cantidad_por_movimiento

EPS = 1e-6


def _con_signo(r):
    return r["cantidad"] if r["tipo"] == "INGRESO" else -r["cantidad"]


def _marcas(ids):
    return ",".join("?" * len(ids))


def saldos_de_lotes(lote_ids, desde="", hasta=""):
    """Recorre todos los remitos no anulados de esos lotes, en orden de fecha.

    Devuelve (despues, anterior, final):
      despues[item_id] = saldo del lote después de ese renglón,
      anterior[lote_id] = saldo antes de la fecha 'desde',
      final[lote_id] = saldo al terminar el día 'hasta' (o el actual si no hay 'hasta').
    """
    despues, anterior, final = {}, defaultdict(float), defaultdict(float)
    ids = list(lote_ids)
    if not ids:
        return despues, anterior, final
    filas = get_db().execute(
        f"""SELECT i.id, i.lote_id, i.cantidad, m.tipo, m.fecha
            FROM movimiento_items i JOIN movimientos m ON m.id = i.movimiento_id
            WHERE m.anulado = 0 AND i.lote_id IN ({_marcas(ids)})
            ORDER BY m.fecha, m.tipo = 'EGRESO', m.id, i.id""",
        ids,
    ).fetchall()
    acumulado = defaultdict(float)
    for r in filas:
        lote = r["lote_id"]
        acumulado[lote] = round(acumulado[lote] + _con_signo(r), 6)
        despues[r["id"]] = acumulado[lote]
        if desde and r["fecha"] < desde:
            anterior[lote] = acumulado[lote]
        if not hasta or r["fecha"] <= hasta:
            final[lote] = acumulado[lote]
    return despues, anterior, final


def saldos_de_productos(producto_ids, hasta=""):
    """{producto_id: cantidad general (todos sus lotes) al terminar el día 'hasta', o la actual}."""
    ids = list(producto_ids)
    if not ids:
        return {}
    sql = f"""SELECT i.producto_id,
                     SUM(CASE WHEN m.tipo = 'INGRESO' THEN i.cantidad ELSE -i.cantidad END) AS saldo
              FROM movimiento_items i JOIN movimientos m ON m.id = i.movimiento_id
              WHERE m.anulado = 0 AND i.producto_id IN ({_marcas(ids)})"""
    params = list(ids)
    if hasta:
        sql += " AND m.fecha <= ?"
        params.append(hasta)
    filas = get_db().execute(sql + " GROUP BY i.producto_id", params).fetchall()
    return {r["producto_id"]: round(r["saldo"], 6) for r in filas}


def _kilos(cantidad, kilos_por_unidad):
    if not kilos_por_unidad or cantidad is None:
        return None
    return round(cantidad * kilos_por_unidad, 3)


def libro_por_lote(renglones, desde="", hasta=""):
    """Agrupa los renglones por producto y lote, cada lote en orden de fecha.

    renglones: un renglón por producto de cada remito (con item_id, lote_id, producto_id, cantidad, tipo...).
    Cada renglón lleva también cuántas fotos tiene su remito (se muestran junto a las observaciones).
    El saldo de cada renglón es el saldo real del lote después de ese remito, contando todos los remitos
    no anulados aunque los filtros oculten algunos. Si hay fecha 'desde', cada lote empieza con el saldo
    que tenía antes de esa fecha.
    """
    renglones = sorted(renglones, key=lambda r: (r["fecha"], r["tipo"] == "EGRESO", r["id"], r["item_id"]))
    despues, anterior, final = saldos_de_lotes({r["lote_id"] for r in renglones}, desde, hasta)
    generales = saldos_de_productos({r["producto_id"] for r in renglones}, hasta)
    fotos = cantidad_por_movimiento({r["id"] for r in renglones})
    productos = {}
    for r in renglones:
        kg = r["kilos_por_unidad"]
        prod = productos.get(r["producto_id"])
        if prod is None:
            saldo = generales.get(r["producto_id"], 0)
            prod = productos[r["producto_id"]] = {
                "id": r["producto_id"], "empresa": r["empresa"], "codigo": r["codigo"],
                "descripcion": r["descripcion"], "unidad": r["unidad"], "kilos_por_unidad": kg,
                "unidades_por_pallet": r["unidades_por_pallet"], "saldo": saldo, "kilos": _kilos(saldo, kg),
                "lotes": {}, "negativo": False,
            }
        lote = prod["lotes"].get(r["lote_id"])
        if lote is None:
            saldo_anterior = anterior.get(r["lote_id"], 0) if desde else None
            saldo_final = final.get(r["lote_id"], 0)
            lote = prod["lotes"][r["lote_id"]] = {
                "id": r["lote_id"], "lote": r["lote"], "vencimiento": r["vencimiento"],
                "saldo_anterior": saldo_anterior, "kilos_anterior": _kilos(saldo_anterior, kg),
                "saldo_final": saldo_final, "kilos_final": _kilos(saldo_final, kg),
                "ingresos": 0.0, "egresos": 0.0, "renglones": [], "negativo": False,
            }
        fila = dict(r)
        fila["kilos"] = _kilos(r["cantidad"], kg)
        fila["saldo"] = None if r["anulado"] else despues.get(r["item_id"])
        fila["kilos_saldo"] = _kilos(fila["saldo"], kg)
        fila["fotos"] = fotos.get(r["id"], 0)
        fila["negativo"] = fila["saldo"] is not None and fila["saldo"] < -EPS
        if fila["negativo"]:
            # Salió mercadería que, con esas fechas, todavía no había entrado: hay que revisar las fechas.
            lote["negativo"] = prod["negativo"] = True
        if not r["anulado"]:
            lote["ingresos" if r["tipo"] == "INGRESO" else "egresos"] += r["cantidad"]
        lote["renglones"].append(fila)
    resultado = sorted(productos.values(), key=lambda p: (p["empresa"].lower(), p["codigo"].lower(), p["id"]))
    for p in resultado:
        p["lotes"] = list(p["lotes"].values())
    return resultado


def lotes_con_saldo_negativo(empresa_id=None):
    """Lotes cuyo saldo quedó negativo algún día: hay egresos con fecha anterior a la mercadería.

    Pasa, por ejemplo, si el stock inicial se cargó con la fecha de hoy y los egresos con fechas anteriores.
    Devuelve [{lote_id, lote, codigo, descripcion, unidad, empresa_id, empresa, desde, minimo}],
    con el primer día en que quedó negativo y el saldo más bajo.
    """
    sql = """SELECT i.lote_id, m.fecha,
                    SUM(CASE WHEN m.tipo = 'INGRESO' THEN i.cantidad ELSE -i.cantidad END) AS neto
             FROM movimiento_items i JOIN movimientos m ON m.id = i.movimiento_id
             WHERE m.anulado = 0"""
    params = []
    if empresa_id:
        sql += " AND m.empresa_id = ?"
        params.append(empresa_id)
    negativos, actual, acumulado = {}, None, 0.0
    for r in get_db().execute(sql + " GROUP BY i.lote_id, m.fecha ORDER BY i.lote_id, m.fecha", params):
        if r["lote_id"] != actual:
            actual, acumulado = r["lote_id"], 0.0
        acumulado = round(acumulado + r["neto"], 6)
        if acumulado < -EPS:
            n = negativos.setdefault(actual, {"desde": r["fecha"], "minimo": acumulado})
            n["minimo"] = min(n["minimo"], acumulado)
    if not negativos:
        return []
    filas = get_db().execute(
        f"""SELECT l.id AS lote_id, l.lote, p.id AS producto_id, p.codigo, p.descripcion, p.unidad,
                   e.id AS empresa_id, e.nombre AS empresa
            FROM lotes l JOIN productos p ON p.id = l.producto_id JOIN empresas e ON e.id = p.empresa_id
            WHERE l.id IN ({_marcas(negativos)})""",
        list(negativos),
    ).fetchall()
    res = [{**dict(f), **negativos[f["lote_id"]]} for f in filas]
    return sorted(res, key=lambda r: (r["empresa"].lower(), r["codigo"].lower(), r["lote"].lower()))
