"""Reglas de negocio compartidas: lotes, registro de movimientos e importación desde Excel."""
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta

from openpyxl import load_workbook

from .db import get_db


# ------------------------------------------------------------- conversiones


def parse_numero(valor):
    """Acepta números o textos como '1.234,5', '1234,5' o '1234.5'. Devuelve None si no se entiende."""
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace(" ", "")
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def parse_fecha(valor):
    """Devuelve la fecha como 'AAAA-MM-DD', '' si está vacía, o None si no se entiende.

    Acepta fechas de Excel, '31/12/2026', '31-12-2026', '31/12/26' y '2026-12-31'.
    """
    if valor is None:
        return ""
    if isinstance(valor, datetime):
        return valor.date().isoformat()
    if isinstance(valor, date):
        return valor.isoformat()
    if isinstance(valor, (int, float)):
        # Fecha de Excel guardada como número de serie.
        if 20000 < valor < 80000:
            return (date(1899, 12, 30) + timedelta(days=int(valor))).isoformat()
        return None
    texto = str(valor).strip()
    if not texto:
        return ""
    for formato in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d-%m-%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(texto[:10] if formato == "%Y-%m-%d" else texto, formato).date().isoformat()
        except ValueError:
            continue
    return None


def _normalizar(texto):
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return " ".join(texto.lower().replace("°", "").replace("º", "").split())


# -------------------------------------------------------------------- lotes


def buscar_lote(producto_id, lote):
    return get_db().execute(
        "SELECT * FROM lotes WHERE producto_id = ? AND lote = ?", (producto_id, lote)
    ).fetchone()


def chequear_lote(producto_id, lote, vencimiento, etiqueta):
    """Devuelve un mensaje de error si el vencimiento no coincide con el ya cargado para ese lote."""
    existente = buscar_lote(producto_id, lote)
    if existente and vencimiento and existente["vencimiento"] and existente["vencimiento"] != vencimiento:
        nombre = f"el lote {lote}" if lote else "la mercadería sin lote"
        return (
            f"{etiqueta}: {nombre} ya está cargado con vencimiento "
            f"{_dmy(existente['vencimiento'])}, no {_dmy(vencimiento)}."
        )
    return None


def obtener_o_crear_lote(producto_id, lote, vencimiento):
    db = get_db()
    existente = buscar_lote(producto_id, lote)
    if existente:
        if vencimiento and not existente["vencimiento"]:
            db.execute("UPDATE lotes SET vencimiento = ? WHERE id = ?", (vencimiento, existente["id"]))
        return existente["id"]
    cur = db.execute(
        "INSERT INTO lotes (producto_id, lote, vencimiento) VALUES (?,?,?)",
        (producto_id, lote, vencimiento or None),
    )
    return cur.lastrowid


def registrar_movimiento(tipo, empresa_id, fecha, remito, observaciones, items):
    """items: lista de (producto_id, lote_id, cantidad). No hace commit."""
    db = get_db()
    cur = db.execute(
        "INSERT INTO movimientos (tipo, fecha, remito, empresa_id, observaciones) VALUES (?,?,?,?,?)",
        (tipo, fecha, remito, empresa_id, observaciones),
    )
    db.executemany(
        "INSERT INTO movimiento_items (movimiento_id, producto_id, lote_id, cantidad) VALUES (?,?,?,?)",
        [(cur.lastrowid, pid, lid, cant) for pid, lid, cant in items],
    )
    return cur.lastrowid


def _dmy(iso):
    if not iso:
        return ""
    a, m, d = iso.split("-")
    return f"{d}/{m}/{a}"


# --------------------------------------------------------- importar Excel

COLUMNAS = {
    "empresa": ["empresa", "cliente", "razon social"],
    "codigo": ["codigo", "cod", "codigo producto", "articulo", "cod articulo"],
    "descripcion": ["descripcion", "producto", "detalle", "nombre"],
    "unidad": ["unidad", "unidad de medida", "um", "u.m."],
    "lote": ["lote", "nro lote", "n lote", "numero de lote", "partida"],
    "vencimiento": ["vencimiento", "vto", "vto.", "fecha vencimiento", "fecha de vencimiento", "vence"],
    "cantidad": ["cantidad", "cant", "cant.", "stock", "existencia", "conteo"],
}
OBLIGATORIAS = ("empresa", "codigo", "cantidad")
ENCABEZADOS_PLANTILLA = ["Empresa", "Código", "Descripción", "Unidad", "Lote", "Vencimiento", "Cantidad"]


def leer_planilla(archivo):
    """Lee la planilla de conteo. Devuelve (filas, errores).

    Cada fila es un dict con: fila (n° de renglón en Excel), empresa, codigo, descripcion,
    unidad, lote, vencimiento ('AAAA-MM-DD' o ''), cantidad (float).
    """
    try:
        wb = load_workbook(archivo, data_only=True, read_only=True)
    except Exception:  # archivo dañado o que no es Excel
        return [], ["No se pudo abrir el archivo. Tiene que ser una planilla de Excel (.xlsx)."]
    ws = wb.worksheets[0]
    renglones = ws.iter_rows(values_only=True)

    # Buscar la fila de encabezados entre las primeras 10.
    mapa, nro_encabezado = None, 0
    for nro, valores in enumerate(renglones, start=1):
        posibles = {}
        for idx, valor in enumerate(valores or ()):
            nombre = _normalizar(valor)
            for clave, alias in COLUMNAS.items():
                if nombre in alias and clave not in posibles:
                    posibles[clave] = idx
        if all(c in posibles for c in OBLIGATORIAS):
            mapa, nro_encabezado = posibles, nro
            break
        if nro >= 10:
            break
    if mapa is None:
        return [], [
            "No encontré los encabezados. La planilla tiene que tener al menos las columnas "
            "Empresa, Código y Cantidad (podés bajar la plantilla de ejemplo)."
        ]

    filas, errores = [], []
    for nro, valores in enumerate(renglones, start=nro_encabezado + 1):
        valores = list(valores or ())

        def celda(clave):
            idx = mapa.get(clave)
            return valores[idx] if idx is not None and idx < len(valores) else None

        def texto(clave):
            v = celda(clave)
            if isinstance(v, float) and v == int(v):
                v = int(v)  # códigos/lotes numéricos: 1234.0 -> '1234'
            return str(v).strip() if v is not None else ""

        if not any(v not in (None, "") for v in valores):
            continue  # renglón vacío
        fila = {
            "fila": nro,
            "empresa": texto("empresa"),
            "codigo": texto("codigo"),
            "descripcion": texto("descripcion"),
            "unidad": texto("unidad"),
            "lote": texto("lote"),
            "vencimiento": parse_fecha(celda("vencimiento")),
            "cantidad": parse_numero(celda("cantidad")),
        }
        problemas = []
        if not fila["empresa"]:
            problemas.append("falta la empresa")
        if not fila["codigo"]:
            problemas.append("falta el código")
        if fila["cantidad"] is None:
            if celda("cantidad") in (None, ""):
                fila["cantidad"] = 0.0
            else:
                problemas.append(f"la cantidad '{celda('cantidad')}' no es un número")
        elif fila["cantidad"] < 0:
            problemas.append("la cantidad no puede ser negativa")
        if fila["vencimiento"] is None:
            problemas.append(f"no entiendo la fecha de vencimiento '{celda('vencimiento')}'")
        if problemas:
            errores.append(f"Fila {nro}: " + ", ".join(problemas) + ".")
        else:
            filas.append(fila)
    if not filas and not errores:
        errores.append("La planilla no tiene datos debajo de los encabezados.")
    return filas, errores


def importar_conteo(filas, fecha, remito):
    """Crea empresas, productos y lotes que falten, y un ingreso por empresa con el conteo.

    Si hay errores no guarda nada. Devuelve (resumen, errores).
    """
    db = get_db()
    errores = []
    empresas = {_normalizar(r["nombre"]): r for r in db.execute("SELECT * FROM empresas")}

    # Productos nuevos necesitan descripción.
    productos_existentes = {}
    for r in db.execute("SELECT p.*, e.nombre AS empresa FROM productos p JOIN empresas e ON e.id = p.empresa_id"):
        productos_existentes[(_normalizar(r["empresa"]), r["codigo"].lower())] = r
    descripciones = {}
    for f in filas:
        clave = (_normalizar(f["empresa"]), f["codigo"].lower())
        if f["descripcion"]:
            descripciones.setdefault(clave, f["descripcion"])
    for f in filas:
        clave = (_normalizar(f["empresa"]), f["codigo"].lower())
        if clave not in productos_existentes and clave not in descripciones:
            errores.append(f"Fila {f['fila']}: el producto {f['codigo']} es nuevo y le falta la descripción.")

    # Un mismo lote no puede venir con dos vencimientos distintos.
    vtos = {}
    for f in filas:
        clave = (_normalizar(f["empresa"]), f["codigo"].lower(), f["lote"].lower())
        if f["vencimiento"]:
            if clave in vtos and vtos[clave][0] != f["vencimiento"]:
                errores.append(
                    f"Fila {f['fila']}: el lote '{f['lote']}' de {f['codigo']} tiene otro vencimiento "
                    f"en la fila {vtos[clave][1]}."
                )
            vtos.setdefault(clave, (f["vencimiento"], f["fila"]))
    if errores:
        return None, errores

    resumen = {"empresas_nuevas": 0, "productos_nuevos": 0, "renglones": 0, "movimientos": []}
    try:
        # Empresas
        for f in filas:
            clave = _normalizar(f["empresa"])
            if clave not in empresas:
                cur = db.execute("INSERT INTO empresas (nombre) VALUES (?)", (f["empresa"],))
                empresas[clave] = {"id": cur.lastrowid, "nombre": f["empresa"]}
                resumen["empresas_nuevas"] += 1
        # Productos
        for f in filas:
            clave = (_normalizar(f["empresa"]), f["codigo"].lower())
            if clave not in productos_existentes:
                cur = db.execute(
                    "INSERT INTO productos (empresa_id, codigo, descripcion, unidad) VALUES (?,?,?,?)",
                    (empresas[clave[0]]["id"], f["codigo"], descripciones[clave], f["unidad"] or "unidades"),
                )
                productos_existentes[clave] = {"id": cur.lastrowid}
                resumen["productos_nuevos"] += 1
        # Lotes y cantidades por empresa
        por_empresa = defaultdict(lambda: defaultdict(float))
        for f in filas:
            pid = productos_existentes[(_normalizar(f["empresa"]), f["codigo"].lower())]["id"]
            error = chequear_lote(pid, f["lote"], f["vencimiento"], f"Fila {f['fila']}")
            if error:
                errores.append(error)
                continue
            lid = obtener_o_crear_lote(pid, f["lote"], f["vencimiento"])
            if f["cantidad"] > 0:
                por_empresa[empresas[_normalizar(f["empresa"])]["id"]][(pid, lid)] += f["cantidad"]
        if errores:
            db.rollback()
            return None, errores
        for empresa_id, items in por_empresa.items():
            mid = registrar_movimiento(
                "INGRESO", empresa_id, fecha, remito, "Importado desde planilla de conteo",
                [(pid, lid, cant) for (pid, lid), cant in items.items()],
            )
            resumen["movimientos"].append(mid)
            resumen["renglones"] += len(items)
        db.commit()
    except Exception:
        db.rollback()
        raise
    return resumen, []
