"""Lo que comparten las pantallas que cargan mercadería (ingreso, egreso y trasvaso).

Leer los renglones de productos del formulario, revisarlos, chequear el stock de lo que sale y
revisar si un ingreso se puede anular.

El stock se revisa por fecha: no alcanza con que hoy haya mercadería, también tiene que haber habido el
día del remito. Por ejemplo, si un lote entró el 27/11, no puede salir nada de ese lote el 05/11. Se cuenta
el saldo al terminar cada día (lo que entra un día se puede sacar ese mismo día).
"""
from collections import defaultdict

from . import insumos as ins
from . import pallets as pl
from . import vacios
from .db import get_db, stock_de_lotes
from .formato import formato_fecha, formato_numero
from .servicios import chequear_lote, obtener_o_crear_lote, parse_fecha, parse_numero

CAMPOS_RENGLON = ("producto_id", "cantidad", "lote", "vencimiento", "lote_id")
EPS = 1e-6


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


def revisar_renglones(tipo, renglones, productos, corrigiendo=None):
    """Revisa los renglones y junta los repetidos (mismo producto y lote se suman).

    tipo 'INGRESO': el lote se escribe y lleva vencimiento. tipo 'EGRESO': se elige un lote que ya existe.
    Devuelve (items, vencimientos, errores): items = {(producto_id, lote o lote_id): cantidad} y
    vencimientos = {(producto_id, LOTE): (lote, vencimiento)} (solo en el ingreso).
    corrigiendo: el remito que se está corrigiendo (si es el único ingreso de un lote, su vencimiento se
    puede cambiar).
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
            error = chequear_lote(p["id"], r["lote"], vto, p["codigo"], corrigiendo)
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


# ------------------------------------------------------------ saldos por fecha


def saldos_por_fecha(lote_ids, excluir=(), agregar=()):
    """El saldo de cada lote al terminar cada día con movimientos: {lote_id: [(fecha, saldo), ...]}, en orden.

    Cuenta los remitos que no están anulados. excluir: ids de remitos que no se cuentan (por ejemplo, el que
    se está corrigiendo). agregar: [(lote_id, fecha, cantidad con signo)] que se suman como si ya estuvieran.
    """
    ids = sorted({lid for lid in lote_ids if lid})
    if not ids:
        return {}
    sql = f"""SELECT i.lote_id, m.fecha,
                     SUM(CASE WHEN m.tipo = 'INGRESO' THEN i.cantidad ELSE -i.cantidad END) AS neto
              FROM movimiento_items i JOIN movimientos m ON m.id = i.movimiento_id
              WHERE m.anulado = 0 AND i.lote_id IN ({','.join('?' * len(ids))})"""
    params = list(ids)
    excluir = [x for x in excluir if x]
    if excluir:
        sql += f" AND m.id NOT IN ({','.join('?' * len(excluir))})"
        params += excluir
    por_dia = defaultdict(lambda: defaultdict(float))
    for r in get_db().execute(sql + " GROUP BY i.lote_id, m.fecha", params):
        por_dia[r["lote_id"]][r["fecha"]] += r["neto"]
    for lid, fecha, cantidad in agregar:
        por_dia[lid][fecha] += cantidad
    res = {}
    for lid in ids:
        acumulado, lista = 0.0, []
        for fecha in sorted(por_dia[lid]):
            acumulado = round(acumulado + por_dia[lid][fecha], 6)
            lista.append((fecha, acumulado))
        res[lid] = lista
    return res


def saldo_al(lista, fecha):
    """El saldo al terminar el día `fecha`. lista: [(fecha, saldo)] en orden (ver saldos_por_fecha)."""
    saldo = 0.0
    for f, s in lista:
        if f > fecha:
            break
        saldo = s
    return saldo


def disponible_desde(lista, fecha):
    """Cuánto se puede sacar con esa fecha sin que el saldo quede negativo ese día ni ningún día después."""
    minimo = saldo_al(lista, fecha)
    for f, s in lista:
        if f > fecha:
            minimo = min(minimo, s)
    return max(0.0, round(minimo, 6))


def negativos_nuevos(antes, despues):
    """Compara los saldos por fecha antes y después de un cambio.

    Devuelve {lote_id: (fecha, saldo)} con el primer día en que el saldo queda negativo y más bajo que antes.
    Lo que ya estaba negativo y no empeora no cuenta (así un error viejo no traba lo demás).
    """
    res = {}
    for lid, lista in despues.items():
        previo = antes.get(lid, [])
        for f in sorted({f for f, _ in lista} | {f for f, _ in previo}):
            d, a = saldo_al(lista, f), saldo_al(previo, f)
            if d < -EPS and d < a - EPS:
                res[lid] = (f, d)
                break
    return res


def nombre_lote(l):
    """'A1 - Caja chica (lote L-1)' o '(sin lote)'."""
    return f"{l['codigo']} - {l['descripcion']} ({('lote ' + l['lote']) if l['lote'] else 'sin lote'})"


def datos_lotes(lote_ids):
    """{lote_id: fila con lote, vencimiento, codigo, descripcion, unidad del producto}."""
    ids = list({lid for lid in lote_ids if lid})
    if not ids:
        return {}
    return {r["id"]: r for r in get_db().execute(
        f"""SELECT l.id, l.lote, l.vencimiento, l.producto_id, p.codigo, p.descripcion, p.unidad
            FROM lotes l JOIN productos p ON p.id = l.producto_id WHERE l.id IN ({','.join('?' * len(ids))})""",
        ids,
    )}


def textos_negativos(negativos, inicio="", final=""):
    """Un mensaje por lote que quedaría con saldo negativo: '{inicio}el saldo de X (lote L) quedaría en -5 cajas
    el 05/11/2026{final}'."""
    lotes = datos_lotes(negativos)
    res = []
    for lid, (fecha, saldo) in sorted(negativos.items(), key=lambda x: x[1][0]):
        l = lotes.get(lid)
        if l is None:
            continue
        res.append(f"{inicio}el saldo de {nombre_lote(l)} quedaría en {formato_numero(saldo)} {l['unidad'].lower()} "
                   f"el {formato_fecha(fecha)}{final}")
    return res


def primer_egreso(lote_ids):
    """{lote_id: fecha del primer egreso que no está anulado}."""
    ids = list({lid for lid in lote_ids if lid})
    if not ids:
        return {}
    return {r[0]: r[1] for r in get_db().execute(
        f"""SELECT i.lote_id, MIN(m.fecha) FROM movimiento_items i JOIN movimientos m ON m.id = i.movimiento_id
            WHERE m.anulado = 0 AND m.tipo = 'EGRESO' AND i.lote_id IN ({','.join('?' * len(ids))})
            GROUP BY i.lote_id""",
        ids,
    )}


def chequear_stock(items, productos, fecha=None, excluir=()):
    """Errores si algún lote no tiene stock suficiente para lo que sale. items = {(producto_id, lote_id): cantidad}.

    fecha: la del remito. Tiene que haber stock ese día y no puede quedar negativo ningún día después
    (por ejemplo, por egresos que ya están cargados con fechas posteriores). Sin fecha, se mira el stock de hoy.
    excluir: remitos que no se cuentan (al corregir un egreso, el mismo egreso).
    """
    errores = []
    if not items:
        return errores
    lotes = {l["id"]: l for l in stock_de_lotes(lote_ids=[lid for _, lid in items], solo_con_stock=False)}
    por_fecha = saldos_por_fecha(lotes, excluir=excluir)
    dia = fecha or "9999-12-31"
    for (pid, lid), cant in items.items():
        l = lotes.get(lid)
        if l is None or l["producto_id"] != pid:
            errores.append(f"{productos[pid]['codigo']}: el lote elegido no corresponde al producto.")
            continue
        lista = por_fecha.get(lid, [])
        hay = disponible_desde(lista, dia)
        if cant <= hay + 1e-9:
            continue
        nombre, unidad = nombre_lote(l), l["unidad"].lower()
        if not lista or dia >= lista[-1][0]:
            # Lo de siempre: no alcanza lo que hay hoy.
            errores.append(f"No hay stock suficiente de {nombre}: hay {formato_numero(hay)} y querés sacar "
                           f"{formato_numero(cant)}.")
            continue
        entra = next((f for f, s in lista if f > dia and s > EPS), None)
        if hay <= EPS and saldo_al(lista, dia) <= EPS and entra:
            errores.append(
                f"No hay stock de {nombre} el {formato_fecha(dia)}: ese lote entró recién el {formato_fecha(entra)}. "
                "Revisá la fecha de este remito, o la del ingreso de ese lote (por ejemplo, la del stock inicial)."
            )
        else:
            errores.append(
                f"No hay stock suficiente de {nombre} el {formato_fecha(dia)}: contando los remitos de ese día y de "
                f"después, con esa fecha se pueden sacar como máximo {formato_numero(hay)} {unidad} y querés sacar "
                f"{formato_numero(cant)}."
            )
    return errores


def lineas_de_ingreso(items, vencimientos, corrigiendo=None):
    """Crea los lotes nuevos y devuelve [(producto_id, lote_id, cantidad)]. No hace commit.

    corrigiendo: el remito que se está corrigiendo (ver revisar_renglones).
    """
    lineas = []
    for (pid, clave_lote), cant in items.items():
        lote, vto = vencimientos[(pid, clave_lote)]
        lineas.append((pid, obtener_o_crear_lote(pid, lote, vto, corrigiendo), cant))
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


def problema_al_anular_ingreso(movimiento, tambien=(), verbo="anular"):
    """Por qué no se puede anular (o borrar) un ingreso (texto para mostrar), o None si se puede.

    tambien: otros remitos que se anulan a la vez (en un trasvaso, lo que salió vuelve al stock).
    verbo: 'anular' o 'borrar' (para los mensajes).
    """
    db = get_db()
    hace = {"anular": "anulá", "borrar": "borrá"}.get(verbo, verbo)
    movimiento_id = movimiento["id"]
    lote_ids = [r[0] for r in db.execute(
        "SELECT DISTINCT lote_id FROM movimiento_items WHERE movimiento_id = ?", (movimiento_id,))]
    # Si ya salió mercadería de este ingreso, sacarlo dejaría el saldo negativo (hoy o en alguna fecha).
    antes = saldos_por_fecha(lote_ids)
    despues = saldos_por_fecha(lote_ids, excluir=[movimiento_id, *tambien])
    negativos = negativos_nuevos(antes, despues)
    if negativos:
        lotes = datos_lotes(negativos)
        hoy = sorted({lotes[lid]["codigo"] + (f" (lote {lotes[lid]['lote']})" if lotes[lid]["lote"] else "")
                      for lid in negativos if lid in lotes and despues[lid] and despues[lid][-1][1] < -EPS})
        if hoy:
            return (f"No se puede {verbo}: el stock quedaría negativo para " + ", ".join(hoy)
                    + f". Primero {hace} los egresos que usaron esa mercadería.")
        return (f"No se puede {verbo}: " + "; ".join(textos_negativos(negativos))
                + f", porque hay egresos de ese lote con fecha anterior a los otros ingresos. Primero {hace} o "
                "corregí esos egresos.")
    problemas = pl.problemas_al_anular(movimiento_id)
    if problemas:
        return (f"No se puede {verbo}: " + "; ".join(problemas)
                + f". Primero {hace} esos egresos o desarmá esos pallets.")
    problemas = ins.problemas_al_anular(movimiento)
    if problemas:
        return (f"No se puede {verbo}: " + "; ".join(problemas)
                + f". Primero {hace} los egresos que sacaron esos pallets.")
    problemas = vacios.problemas_al_anular(movimiento_id=movimiento_id)
    if problemas:
        return (f"No se puede {verbo}: " + "; ".join(problemas)
                + f". Primero {hace} los trasvasos o egresos que los usaron, o corregí los envases en "
                "Trasvasos → Envases vacíos y sucios.")
    return None
