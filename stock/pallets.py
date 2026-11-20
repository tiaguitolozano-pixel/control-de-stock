"""Pallets con etiqueta y ubicaciones del depósito (galpón, calle y estiba).

El contenido de cada pallet es la suma de lo que entró y salió de él (tabla pallet_movs),
sin contar los remitos anulados: si se anula un egreso, la mercadería vuelve sola a sus pallets.
Armar, juntar o desarmar pallets no cambia el stock: solo cambia en qué pallet está cada cosa.
"""
import math
import re
from collections import defaultdict

from .db import get_db, stock_por_lote_id

EPS = 1e-6
MAX_PALLETS_POR_RENGLON = 300
MAX_ESTIBAS_POR_VEZ = 200


# ------------------------------------------------------------------ códigos


def codigo_pallet(pallet_id):
    return f"P{int(pallet_id):06d}"


def codigo_ubicacion(galpon, calle, estiba):
    return f"G{galpon}-C{int(calle)}-E{int(estiba):02d}"


def corto_ubicacion(u):
    """'C2-E07': lo que se ve en grande en las etiquetas y listados."""
    if not u or u["calle"] is None:
        return ""
    return f"C{u['calle']}-E{int(u['estiba']):02d}"


def nombre_ubicacion(u):
    return f"Galpón {u['galpon']} · Calle {u['calle']} · Estiba {u['estiba']}"


_SEP = r"[^A-Z0-9]*"
_RE_PALLET = re.compile(rf"^P{_SEP}(\d{{1,9}})$")
_RE_UBICACION = re.compile(rf"^(?:G?{_SEP}(\d{{1,4}}){_SEP})?C{_SEP}(\d{{1,4}}){_SEP}E{_SEP}(\d{{1,4}})$")


def leer_codigo(texto):
    """Interpreta lo que escribió el lector de códigos (o la persona).

    Devuelve ('pallet', fila), ('ubicacion', fila) o (None, mensaje de error).
    Acepta cualquier separador ('G16-C2-E07', 'g16 c2 e7', 'C2/E07') porque algunos lectores
    cambian el guion por otro signo según cómo esté configurado el teclado.
    """
    limpio = (texto or "").strip().upper()
    if not limpio:
        return None, "No llegó ningún código."
    db = get_db()
    m = _RE_PALLET.match(limpio)
    if m:
        fila = db.execute("SELECT * FROM pallets WHERE id = ?", (int(m.group(1)),)).fetchone()
        if fila is None:
            return None, f"No existe el pallet {codigo_pallet(m.group(1))}."
        return "pallet", fila
    m = _RE_UBICACION.match(limpio)
    if m:
        galpon, calle, estiba = m.group(1), int(m.group(2)), int(m.group(3))
        if galpon:
            filas = db.execute(
                "SELECT * FROM ubicaciones WHERE galpon = ? AND calle = ? AND estiba = ?",
                (str(int(galpon)), calle, estiba),
            ).fetchall()
        else:
            filas = db.execute(
                "SELECT * FROM ubicaciones WHERE calle = ? AND estiba = ?", (calle, estiba)
            ).fetchall()
        if len(filas) == 1:
            return "ubicacion", filas[0]
        if len(filas) > 1:
            return None, f"Hay una calle {calle}, estiba {estiba} en más de un galpón: usá el código completo."
        return None, f"No existe la ubicación calle {calle}, estiba {estiba}. Creala en Depósito."
    return None, f"No reconozco el código '{texto.strip()}'."


# ---------------------------------------------------------------- contenido

SQL_CONTENIDO = """
SELECT pm.pallet_id, pm.producto_id, pm.lote_id, SUM(pm.cantidad) AS cantidad,
       p.codigo, p.descripcion, p.unidad, p.unidades_por_pallet, l.lote, l.vencimiento
FROM pallet_movs pm
JOIN pallets pa ON pa.id = pm.pallet_id
JOIN productos p ON p.id = pm.producto_id
JOIN lotes l ON l.id = pm.lote_id
LEFT JOIN movimiento_items i ON i.id = pm.item_id
LEFT JOIN movimientos m ON m.id = i.movimiento_id
WHERE (pm.item_id IS NULL OR m.anulado = 0) {filtros}
GROUP BY pm.pallet_id, pm.producto_id, pm.lote_id
HAVING SUM(pm.cantidad) > 0.000001
ORDER BY pm.pallet_id, l.vencimiento IS NULL, l.vencimiento, p.codigo, l.lote
"""


def _en(columna, valores, params):
    params += list(valores)
    return f" AND {columna} IN ({','.join('?' * len(valores))})"


def contenido(pallet_ids=None, lote_ids=None, empresa_id=None, ubicacion_id=None):
    """Renglones (pallet, producto, lote, cantidad) con cantidad mayor a cero."""
    filtros, params = "", []
    for columna, valores in (("pm.pallet_id", pallet_ids), ("pm.lote_id", lote_ids)):
        if valores is not None:
            valores = list(valores)
            if not valores:
                return []
            filtros += _en(columna, valores, params)
    if empresa_id:
        filtros += " AND pa.empresa_id = ?"
        params.append(empresa_id)
    if ubicacion_id:
        filtros += " AND pa.ubicacion_id = ?"
        params.append(ubicacion_id)
    return get_db().execute(SQL_CONTENIDO.format(filtros=filtros), params).fetchall()


def contenido_por_pallet(pallet_ids):
    res = defaultdict(list)
    for r in contenido(pallet_ids=pallet_ids):
        res[r["pallet_id"]].append(r)
    return res


SQL_PALLETS = """
SELECT pa.*, e.nombre AS empresa, u.galpon, u.calle, u.estiba, u.codigo AS ubicacion_codigo,
       m.remito AS remito, m.fecha AS fecha_ingreso
FROM pallets pa
JOIN empresas e ON e.id = pa.empresa_id
LEFT JOIN ubicaciones u ON u.id = pa.ubicacion_id
LEFT JOIN movimientos m ON m.id = pa.movimiento_id
"""


def _armar(filas, lineas):
    res = []
    for f in filas:
        d = dict(f)
        d["lineas"] = lineas.get(f["id"], [])
        d["ubicacion"] = corto_ubicacion(f) if f["ubicacion_id"] else ""
        res.append(d)
    return res


def pallets(empresa_id=None, buscar=None, ubicacion_id=None, sin_ubicar=False, con_contenido=True,
            ids=None, calle=None, galpon=None, limite=3000):
    """Pallets con su contenido ('lineas'), del más nuevo al más viejo."""
    where, params = [], []
    if ids is not None:
        ids = list(ids)
        if not ids:
            return []
        where.append(f"pa.id IN ({','.join('?' * len(ids))})")
        params += ids
    if empresa_id:
        where.append("pa.empresa_id = ?")
        params.append(empresa_id)
    if ubicacion_id:
        where.append("pa.ubicacion_id = ?")
        params.append(ubicacion_id)
    if sin_ubicar:
        where.append("pa.ubicacion_id IS NULL")
    if galpon:
        where.append("u.galpon = ?")
        params.append(galpon)
    if calle:
        where.append("u.calle = ?")
        params.append(calle)
    sql = SQL_PALLETS + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY pa.id DESC"
    filas = get_db().execute(sql, params).fetchall()
    lineas = contenido_por_pallet([f["id"] for f in filas]) if filas else {}
    if con_contenido:
        filas = [f for f in filas if lineas.get(f["id"])]
    if buscar:
        b = buscar.strip().lower()
        filas = [
            f for f in filas
            if b in (f["codigo"] or "").lower()
            or any(b in (l["codigo"] or "").lower() or b in (l["descripcion"] or "").lower()
                   or b in (l["lote"] or "").lower() for l in lineas.get(f["id"], []))
        ]
    return _armar(filas[:limite], lineas)


def pallet(pallet_id):
    res = pallets(ids=[pallet_id], con_contenido=False)
    return res[0] if res else None


def sin_etiqueta_por_lote(lote_ids):
    """{lote_id: cantidad en stock que todavía no está en ningún pallet}."""
    lote_ids = list(lote_ids)
    stock = stock_por_lote_id(lote_ids)
    en_pallets = defaultdict(float)
    for r in contenido(lote_ids=lote_ids):
        en_pallets[r["lote_id"]] += r["cantidad"]
    return {lid: max(0.0, round(stock.get(lid, 0) - en_pallets[lid], 6)) for lid in lote_ids}


def resumen_por_lote(lote_ids):
    """Para el listado de lotes: dónde está cada lote y cuánto falta etiquetar."""
    lote_ids = list(lote_ids)
    filas = contenido(lote_ids=lote_ids)
    ubic = {}
    if filas:
        ubic = {
            r["id"]: r for r in get_db().execute(
                f"""SELECT pa.id, u.galpon, u.calle, u.estiba FROM pallets pa
                    LEFT JOIN ubicaciones u ON u.id = pa.ubicacion_id
                    WHERE pa.id IN ({','.join('?' * len({f['pallet_id'] for f in filas}))})""",
                list({f["pallet_id"] for f in filas}),
            )
        }
    res = {lid: {"ubicaciones": set(), "pallets": set(), "sin_ubicar": set(), "en_pallets": 0.0} for lid in lote_ids}
    for f in filas:
        r = res[f["lote_id"]]
        r["pallets"].add(f["pallet_id"])
        r["en_pallets"] += f["cantidad"]
        u = ubic.get(f["pallet_id"])
        if u is not None and u["calle"] is not None:
            r["ubicaciones"].add((u["galpon"], u["calle"], u["estiba"]))
        else:
            r["sin_ubicar"].add(f["pallet_id"])
    sin_etiqueta = sin_etiqueta_por_lote(lote_ids)
    for lid, r in res.items():
        r["ubicaciones"] = [f"C{c}-E{int(e):02d}" for _, c, e in sorted(r["ubicaciones"])]
        r["pallets"] = len(r["pallets"])
        r["sin_ubicar"] = len(r["sin_ubicar"])
        r["sin_etiqueta"] = sin_etiqueta.get(lid, 0)
    return res


# --------------------------------------------------------- armar pallets


def repartir(cantidad, por_pallet):
    """100 con 40 por pallet -> [40, 40, 20]."""
    partes, restante = [], round(float(cantidad), 6)
    por_pallet = float(por_pallet)
    while restante > EPS:
        q = min(por_pallet, restante)
        partes.append(round(q, 6))
        restante = round(restante - q, 6)
    return partes


def cuantos_pallets(cantidad, por_pallet):
    if not por_pallet or por_pallet <= 0:
        return 0
    return math.ceil(float(cantidad) / float(por_pallet) - EPS)


def nuevo_pallet(empresa_id, movimiento_id=None, ubicacion_id=None):
    """Crea un pallet vacío con su código. No hace commit."""
    db = get_db()
    cur = db.execute(
        "INSERT INTO pallets (empresa_id, movimiento_id, ubicacion_id) VALUES (?,?,?)",
        (empresa_id, movimiento_id, ubicacion_id),
    )
    pid = cur.lastrowid
    db.execute("UPDATE pallets SET codigo = ? WHERE id = ?", (codigo_pallet(pid), pid))
    if ubicacion_id:
        db.execute("INSERT INTO pallet_ubicaciones (pallet_id, ubicacion_id) VALUES (?,?)", (pid, ubicacion_id))
    return pid


def _mov(pallet_id, producto_id, lote_id, cantidad, motivo, item_id=None):
    get_db().execute(
        """INSERT INTO pallet_movs (pallet_id, item_id, producto_id, lote_id, cantidad, motivo)
           VALUES (?,?,?,?,?,?)""",
        (pallet_id, item_id, producto_id, lote_id, round(cantidad, 6), motivo),
    )


def crear_pallets_de_ingreso(movimiento_id):
    """Arma los pallets de un ingreso: los completos que entren y uno más con lo que sobra.

    Solo para productos con unidades por pallet cargadas. Devuelve (ids creados, avisos). No hace commit.
    """
    items = get_db().execute(
        """SELECT i.id, i.producto_id, i.lote_id, i.cantidad, p.codigo, p.unidades_por_pallet, m.empresa_id
           FROM movimiento_items i JOIN productos p ON p.id = i.producto_id
           JOIN movimientos m ON m.id = i.movimiento_id
           WHERE i.movimiento_id = ? ORDER BY i.id""",
        (movimiento_id,),
    ).fetchall()
    creados, avisos = [], []
    for it in items:
        por = it["unidades_por_pallet"]
        if not por:
            avisos.append(f"{it['codigo']}: no tiene cargadas las unidades por pallet, así que no se armaron "
                          "sus pallets (podés armarlos después desde Pallets → Armar pallets).")
            continue
        partes = repartir(it["cantidad"], por)
        if len(partes) > MAX_PALLETS_POR_RENGLON:
            avisos.append(f"{it['codigo']}: serían {len(partes)} pallets; revisá las unidades por pallet. "
                          "No se armaron.")
            continue
        for q in partes:
            pid = nuevo_pallet(it["empresa_id"], movimiento_id)
            _mov(pid, it["producto_id"], it["lote_id"], q, "INGRESO", it["id"])
            creados.append(pid)
    return creados, avisos


def armar_pallets(lote_id, por_pallet, cantidad=None, ubicacion_id=None):
    """Arma pallets con mercadería del lote que todavía no tiene etiqueta (por ejemplo, el stock inicial).

    Devuelve (ids creados, error). No hace commit.
    """
    db = get_db()
    lote = db.execute(
        """SELECT l.id, l.producto_id, p.empresa_id, p.codigo FROM lotes l
           JOIN productos p ON p.id = l.producto_id WHERE l.id = ?""",
        (lote_id,),
    ).fetchone()
    if lote is None:
        return [], "No existe ese lote."
    if not por_pallet or por_pallet <= 0:
        return [], f"{lote['codigo']}: indicá cuántas unidades van en cada pallet."
    disponible = sin_etiqueta_por_lote([lote_id])[lote_id]
    total = disponible if cantidad is None else float(cantidad)
    if total <= EPS:
        return [], f"{lote['codigo']}: no queda mercadería sin etiqueta en ese lote."
    if total > disponible + EPS:
        return [], (f"{lote['codigo']}: solo hay {disponible:g} sin etiqueta en ese lote "
                    f"y querés armar pallets con {total:g}.")
    partes = repartir(total, por_pallet)
    if len(partes) > MAX_PALLETS_POR_RENGLON:
        return [], f"{lote['codigo']}: serían {len(partes)} pallets; revisá las unidades por pallet."
    creados = []
    for q in partes:
        pid = nuevo_pallet(lote["empresa_id"], None, ubicacion_id)
        _mov(pid, lote["producto_id"], lote_id, q, "ARMADO")
        creados.append(pid)
    return creados, None


def sumar_a_pallet(pallet_id, lote_id, cantidad):
    """Agrega a un pallet mercadería sin etiqueta (por ejemplo, un segundo lote). No hace commit."""
    db = get_db()
    pa = db.execute("SELECT * FROM pallets WHERE id = ?", (pallet_id,)).fetchone()
    lote = db.execute(
        "SELECT l.id, l.producto_id, p.empresa_id, p.codigo FROM lotes l JOIN productos p ON p.id = l.producto_id "
        "WHERE l.id = ?",
        (lote_id,),
    ).fetchone()
    if pa is None or lote is None:
        return "No existe el pallet o el lote."
    if lote["empresa_id"] != pa["empresa_id"]:
        return "Ese producto es de otra empresa: no puede ir en este pallet."
    if cantidad is None or cantidad <= 0:
        return "La cantidad tiene que ser mayor a cero."
    disponible = sin_etiqueta_por_lote([lote_id])[lote_id]
    if cantidad > disponible + EPS:
        return f"{lote['codigo']}: solo hay {disponible:g} sin etiqueta en ese lote."
    _mov(pallet_id, lote["producto_id"], lote_id, cantidad, "ARMADO")
    return None


def sacar_de_pallet(pallet_id, lote_id, cantidad):
    """Saca mercadería del pallet sin que salga del depósito (queda sin etiqueta). No hace commit."""
    linea = next((r for r in contenido(pallet_ids=[pallet_id]) if r["lote_id"] == lote_id), None)
    if linea is None:
        return "Ese lote no está en el pallet."
    if cantidad is None or cantidad <= 0:
        return "La cantidad tiene que ser mayor a cero."
    if cantidad > linea["cantidad"] + EPS:
        return f"En el pallet hay {linea['cantidad']:g}, no se pueden sacar {cantidad:g}."
    _mov(pallet_id, linea["producto_id"], lote_id, -cantidad, "DESARMADO")
    return None


def juntar_pallets(destino_id, origen_id):
    """Pasa todo lo del pallet origen al destino (por ejemplo, para un pallet con dos lotes).

    El pallet origen queda vacío. Devuelve un mensaje de error o None. No hace commit.
    """
    db = get_db()
    if destino_id == origen_id:
        return "Es el mismo pallet."
    destino = db.execute("SELECT * FROM pallets WHERE id = ?", (destino_id,)).fetchone()
    origen = db.execute("SELECT * FROM pallets WHERE id = ?", (origen_id,)).fetchone()
    if destino is None or origen is None:
        return "No existe el pallet."
    if destino["empresa_id"] != origen["empresa_id"]:
        return "Los pallets son de empresas distintas: no se pueden juntar."
    lineas = contenido(pallet_ids=[origen_id])
    if not lineas:
        return f"El pallet {origen['codigo']} está vacío."
    for l in lineas:
        _mov(origen_id, l["producto_id"], l["lote_id"], -l["cantidad"], "JUNTADO")
        _mov(destino_id, l["producto_id"], l["lote_id"], l["cantidad"], "JUNTADO")
    return None


# ------------------------------------------------------------- salidas


def _pallets_del_lote(lote_id):
    return [(r["pallet_id"], r["cantidad"]) for r in contenido(lote_ids=[lote_id])]


def planear_salida(lote_id, cantidad, por_pallet):
    """Decide de qué pallets sale `cantidad` de un lote cuando no se escanearon los pallets.

    Primero la mercadería sin etiqueta, después pallets completos enteros, y lo que sobra de un
    pallet empezado (si no alcanza, se empieza uno completo). Hay que llamarla antes de guardar el egreso.
    Devuelve lista de (pallet_id, cantidad).
    """
    disponibles = _pallets_del_lote(lote_id)
    sin_etiqueta = round(stock_por_lote_id([lote_id]).get(lote_id, 0) - sum(c for _, c in disponibles), 6)
    restante = round(float(cantidad) - min(float(cantidad), max(0.0, sin_etiqueta)), 6)
    plan, usados = [], set()
    if restante <= EPS:
        return plan
    completo = (lambda c: c >= por_pallet - EPS) if por_pallet else (lambda c: False)
    completos = [p for p in disponibles if completo(p[1])]
    empezados = [p for p in disponibles if not completo(p[1])]

    def tomar(pid, q):
        nonlocal restante
        plan.append((pid, round(q, 6)))
        usados.add(pid)
        restante = round(restante - q, 6)

    for pid, cant in completos:  # pallets completos que salen enteros
        if restante + EPS >= cant:
            tomar(pid, cant)
        if restante <= EPS:
            return plan
    alcanzan = sorted((p for p in empezados if p[1] + EPS >= restante), key=lambda p: p[1])
    if alcanzan:  # el pallet empezado más chico que alcance
        tomar(alcanzan[0][0], restante)
        return plan
    for pid, cant in sorted(empezados, key=lambda p: p[1]):
        tomar(pid, min(cant, restante))
        if restante <= EPS:
            return plan
    for pid, cant in completos:  # si todavía falta, se empieza un pallet completo
        if pid not in usados:
            tomar(pid, min(cant, restante))
            if restante <= EPS:
                break
    return plan


def registrar_salida_de_pallets(movimiento_id, plan):
    """plan: {(producto_id, lote_id): [(pallet_id, cantidad), ...]}. No hace commit."""
    items = {
        (r["producto_id"], r["lote_id"]): r["id"]
        for r in get_db().execute(
            "SELECT id, producto_id, lote_id FROM movimiento_items WHERE movimiento_id = ?", (movimiento_id,)
        )
    }
    for (producto_id, lote_id), partes in plan.items():
        for pallet_id, q in partes:
            if q > EPS:
                _mov(pallet_id, producto_id, lote_id, -q, "EGRESO", items[(producto_id, lote_id)])


def pallets_de_movimiento(movimiento_id):
    """Qué pallets tocó un remito: los que armó (ingreso) o de los que sacó (egreso)."""
    return get_db().execute(
        """SELECT pm.pallet_id, pa.codigo AS pallet, pm.producto_id, pm.lote_id, pm.cantidad, pm.motivo,
                  p.codigo, p.descripcion, p.unidad, l.lote, l.vencimiento,
                  u.galpon, u.calle, u.estiba
           FROM pallet_movs pm
           JOIN movimiento_items i ON i.id = pm.item_id
           JOIN pallets pa ON pa.id = pm.pallet_id
           JOIN productos p ON p.id = pm.producto_id
           JOIN lotes l ON l.id = pm.lote_id
           LEFT JOIN ubicaciones u ON u.id = pa.ubicacion_id
           WHERE i.movimiento_id = ? ORDER BY pm.pallet_id, pm.id""",
        (movimiento_id,),
    ).fetchall()


def problemas_al_anular(movimiento_id):
    """Lo que quedaría mal en los pallets si se anula un ingreso. Lista vacía = se puede anular.

    Anular un egreso nunca es problema: la mercadería vuelve a sus pallets.
    """
    db = get_db()
    m = db.execute("SELECT tipo FROM movimientos WHERE id = ?", (movimiento_id,)).fetchone()
    if m is None or m["tipo"] != "INGRESO":
        return []
    problemas = []
    movidos = db.execute(
        """SELECT DISTINCT pa.codigo FROM pallet_movs pm JOIN pallets pa ON pa.id = pm.pallet_id
           LEFT JOIN movimiento_items i ON i.id = pm.item_id
           WHERE pm.pallet_id IN (SELECT pm2.pallet_id FROM pallet_movs pm2
                                  JOIN movimiento_items i2 ON i2.id = pm2.item_id
                                  WHERE i2.movimiento_id = ?)
             AND (i.movimiento_id IS NULL OR i.movimiento_id <> ?)
           ORDER BY pa.codigo""",
        (movimiento_id, movimiento_id),
    ).fetchall()
    if movidos:
        problemas.append(
            "los pallets " + ", ".join(r["codigo"] for r in movidos)
            + " de este remito ya tuvieron salidas o se juntaron con otros"
        )
    # Pallets armados después con mercadería de este remito.
    items = db.execute(
        """SELECT i.lote_id, SUM(i.cantidad) AS cantidad, p.codigo, l.lote FROM movimiento_items i
           JOIN productos p ON p.id = i.producto_id JOIN lotes l ON l.id = i.lote_id
           WHERE i.movimiento_id = ? GROUP BY i.lote_id""",
        (movimiento_id,),
    ).fetchall()
    stock = stock_por_lote_id([i["lote_id"] for i in items])
    propios = defaultdict(float)
    for r in db.execute(
        """SELECT pm.lote_id, SUM(pm.cantidad) AS cantidad FROM pallet_movs pm
           JOIN movimiento_items i ON i.id = pm.item_id WHERE i.movimiento_id = ? GROUP BY pm.lote_id""",
        (movimiento_id,),
    ):
        propios[r["lote_id"]] = r["cantidad"]
    en_pallets = defaultdict(float)
    for r in contenido(lote_ids=[i["lote_id"] for i in items]):
        en_pallets[r["lote_id"]] += r["cantidad"]
    for i in items:
        queda_stock = stock.get(i["lote_id"], 0) - i["cantidad"]
        queda_en_pallets = en_pallets[i["lote_id"]] - propios[i["lote_id"]]
        if queda_en_pallets > queda_stock + EPS:
            nombre = i["codigo"] + (f" (lote {i['lote']})" if i["lote"] else "")
            problemas.append(f"hay pallets armados con mercadería de {nombre}")
    return problemas


# ---------------------------------------------------------- ubicaciones


def ubicar(pallet_id, ubicacion_id):
    """Guarda dónde quedó el pallet (y lo anota en su historial). No hace commit."""
    db = get_db()
    db.execute("UPDATE pallets SET ubicacion_id = ? WHERE id = ?", (ubicacion_id, pallet_id))
    db.execute("INSERT INTO pallet_ubicaciones (pallet_id, ubicacion_id) VALUES (?,?)", (pallet_id, ubicacion_id))


def crear_estibas(galpon, calle, desde, hasta):
    """Crea las estibas desde..hasta de una calle (las que ya existen se dejan). Devuelve cuántas creó."""
    db = get_db()
    creadas = 0
    for n in range(desde, hasta + 1):
        cur = db.execute(
            "INSERT OR IGNORE INTO ubicaciones (galpon, calle, estiba, codigo) VALUES (?,?,?,?)",
            (galpon, calle, n, codigo_ubicacion(galpon, calle, n)),
        )
        creadas += cur.rowcount
    return creadas


def ubicaciones(galpon=None, solo_activas=False, ids=None):
    where, params = [], []
    if galpon:
        where.append("galpon = ?")
        params.append(galpon)
    if solo_activas:
        where.append("activa = 1")
    if ids is not None:
        ids = list(ids)
        if not ids:
            return []
        where.append(f"id IN ({','.join('?' * len(ids))})")
        params += ids
    sql = "SELECT * FROM ubicaciones" + (" WHERE " + " AND ".join(where) if where else "")
    return get_db().execute(sql + " ORDER BY CAST(galpon AS INTEGER), galpon, calle, estiba", params).fetchall()


def galpones():
    return [r[0] for r in get_db().execute(
        "SELECT DISTINCT galpon FROM ubicaciones ORDER BY CAST(galpon AS INTEGER), galpon"
    )]


def ocupacion():
    """{ubicacion_id: cantidad de pallets con mercadería} y cuántos pallets con mercadería no tienen ubicación."""
    db = get_db()
    con_mercaderia = {r["pallet_id"] for r in contenido()}
    por_ubicacion, sin_ubicar = defaultdict(int), 0
    if con_mercaderia:
        for r in db.execute("SELECT id, ubicacion_id FROM pallets"):
            if r["id"] not in con_mercaderia:
                continue
            if r["ubicacion_id"]:
                por_ubicacion[r["ubicacion_id"]] += 1
            else:
                sin_ubicar += 1
    return por_ubicacion, sin_ubicar


def ultimos_ubicados(cantidad=8):
    return get_db().execute(
        """SELECT pu.creado_en, pa.id AS pallet_id, pa.codigo AS pallet, u.galpon, u.calle, u.estiba
           FROM pallet_ubicaciones pu JOIN pallets pa ON pa.id = pu.pallet_id
           LEFT JOIN ubicaciones u ON u.id = pu.ubicacion_id
           ORDER BY pu.id DESC LIMIT ?""",
        (cantidad,),
    ).fetchall()


def historial_pallet(pallet_id):
    """Movimientos de un pallet (entradas, salidas, armados, juntados) y sus cambios de ubicación."""
    db = get_db()
    movs = db.execute(
        """SELECT pm.creado_en, pm.motivo, pm.cantidad, p.codigo, p.unidad, l.lote,
                  m.id AS movimiento_id, m.remito, m.fecha, m.tipo, m.anulado
           FROM pallet_movs pm JOIN productos p ON p.id = pm.producto_id JOIN lotes l ON l.id = pm.lote_id
           LEFT JOIN movimiento_items i ON i.id = pm.item_id LEFT JOIN movimientos m ON m.id = i.movimiento_id
           WHERE pm.pallet_id = ? ORDER BY pm.id DESC""",
        (pallet_id,),
    ).fetchall()
    lugares = db.execute(
        """SELECT pu.creado_en, u.galpon, u.calle, u.estiba FROM pallet_ubicaciones pu
           LEFT JOIN ubicaciones u ON u.id = pu.ubicacion_id WHERE pu.pallet_id = ? ORDER BY pu.id DESC""",
        (pallet_id,),
    ).fetchall()
    return movs, lugares
