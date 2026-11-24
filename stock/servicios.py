"""Reglas de negocio compartidas: lotes, registro de movimientos e importación desde Excel."""
import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime, timedelta

from openpyxl import load_workbook

from .db import asegurar_envase, get_db


# ------------------------------------------------------------- conversiones


# '1.000', '12.500', '1.000.000': el punto separa miles (no es una coma decimal).
_CON_PUNTO_DE_MILES = re.compile(r"^-?[1-9]\d{0,2}(\.\d{3})+$")


def parse_numero(valor):
    """Entiende números escritos a la argentina. Devuelve None si no se entiende.

    '1.234,5' -> 1234.5 ; '1234,5' -> 1234.5 ; '1.000' -> 1000 ; '2.5' -> 2.5
    """
    if valor is None:
        return None
    if isinstance(valor, (int, float)):
        return float(valor)
    texto = str(valor).strip().replace(" ", "")
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif _CON_PUNTO_DE_MILES.match(texto):
        texto = texto.replace(".", "")
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


def normalizar_patente(texto):
    """'ab 123 cd' -> 'AB123CD' (sin espacios ni guiones, en mayúsculas)."""
    return "".join(c for c in str(texto or "").upper() if c.isalnum())


_CONTENEDOR_ISO = re.compile(r"^[A-Z]{4}\d{6,7}$")


def normalizar_contenedor(texto):
    """'mscu 123456-7' -> 'MSCU1234567'. Lo que no tiene forma de contenedor queda como se escribió."""
    compacto = normalizar_patente(texto)
    if _CONTENEDOR_ISO.match(compacto):
        return compacto
    return " ".join(str(texto or "").split())


def normalizar_transporte(datos):
    """Deja las patentes y el contenedor escritos siempre igual (para buscarlos después)."""
    datos = dict(datos)
    for campo in ("patente_camion", "patente_semi"):
        datos[campo] = normalizar_patente(datos.get(campo))
    datos["contenedor"] = normalizar_contenedor(datos.get("contenedor"))
    return datos


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


CAMPOS_TRANSPORTE = ("chofer", "transporte", "patente_camion", "patente_semi", "contenedor", "tipo_contenedor")


def registrar_movimiento(tipo, empresa_id, fecha, remito, observaciones, items, transporte=None, insumos=None):
    """items: lista de (producto_id, lote_id, cantidad).

    transporte: dict opcional con los CAMPOS_TRANSPORTE (chofer, empresa de transporte, patentes y contenedor).
    insumos: lista opcional de (insumo_id, propietario o None, cantidad).
    No hace commit.
    """
    db = get_db()
    t = {c: ((transporte or {}).get(c) or "").strip() or None for c in CAMPOS_TRANSPORTE}
    cur = db.execute(
        f"""INSERT INTO movimientos (tipo, fecha, remito, empresa_id, observaciones, {", ".join(CAMPOS_TRANSPORTE)})
            VALUES (?,?,?,?,?{",?" * len(CAMPOS_TRANSPORTE)})""",
        (tipo, fecha, remito, empresa_id, observaciones, *(t[c] for c in CAMPOS_TRANSPORTE)),
    )
    mid = cur.lastrowid
    db.executemany(
        "INSERT INTO movimiento_items (movimiento_id, producto_id, lote_id, cantidad) VALUES (?,?,?,?)",
        [(mid, pid, lid, cant) for pid, lid, cant in items],
    )
    db.executemany(
        "INSERT INTO movimiento_insumos (movimiento_id, insumo_id, propietario, cantidad) VALUES (?,?,?,?)",
        [(mid, iid, propietario, cant) for iid, propietario, cant in (insumos or [])],
    )
    return mid


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
    "unidad": ["unidad", "unidad de medida", "um", "u.m.", "envase", "tipo de envase", "presentacion"],
    "por_pallet": ["por pallet", "unidades por pallet", "x pallet", "cant x pallet", "cantidad por pallet",
                   "por palet", "unidades por palet", "x palet", "cantidad por palet"],
    # Solo nombres que dicen "por unidad": una columna "Kilos" a secas puede ser el total del renglón.
    "kilos": ["kilos por unidad", "kg por unidad", "kilos x unidad", "kg x unidad", "kilos/unidad", "kg/unidad",
              "kg/u", "kg x u", "peso unitario", "peso por unidad", "kilos por envase", "kg por envase"],
    "lote": ["lote", "nro lote", "n lote", "numero de lote", "partida"],
    "vencimiento": ["vencimiento", "vto", "vto.", "fecha vencimiento", "fecha de vencimiento", "vence"],
    "cantidad": ["cantidad", "cant", "cant.", "stock", "existencia", "conteo"],
}
OBLIGATORIAS = ("empresa", "codigo", "cantidad")
ENCABEZADOS_PLANTILLA = ["Empresa", "Código", "Descripción", "Envase", "Por pallet", "Kilos por unidad", "Lote",
                         "Vencimiento", "Cantidad"]


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
            "por_pallet": parse_numero(celda("por_pallet")),
            "kilos": parse_numero(celda("kilos")),
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
        if fila["por_pallet"] is None and celda("por_pallet") not in (None, ""):
            problemas.append(f"'por pallet' tiene que ser un número, no '{celda('por_pallet')}'")
        elif fila["por_pallet"] is not None and fila["por_pallet"] <= 0:
            fila["por_pallet"] = None
        if fila["kilos"] is None and celda("kilos") not in (None, ""):
            problemas.append(f"'kilos por unidad' tiene que ser un número, no '{celda('kilos')}'")
        elif fila["kilos"] is not None and fila["kilos"] <= 0:
            fila["kilos"] = None
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
    descripciones, unidades, por_pallet, kilos = {}, {}, {}, {}
    for f in filas:
        clave = (_normalizar(f["empresa"]), f["codigo"].lower())
        if f["descripcion"]:
            descripciones.setdefault(clave, f["descripcion"])
        if f["unidad"]:
            unidades.setdefault(clave, f["unidad"])
        if f["por_pallet"]:
            por_pallet.setdefault(clave, f["por_pallet"])
        if f.get("kilos"):
            kilos.setdefault(clave, f["kilos"])
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
                unidad = unidades.get(clave) or "Unidades"
                cur = db.execute(
                    """INSERT INTO productos (empresa_id, codigo, descripcion, unidad, unidades_por_pallet,
                                              kilos_por_unidad)
                       VALUES (?,?,?,?,?,?)""",
                    (empresas[clave[0]]["id"], f["codigo"], descripciones[clave], unidad,
                     por_pallet.get(clave), kilos.get(clave)),
                )
                asegurar_envase(unidad)
                productos_existentes[clave] = {"id": cur.lastrowid, "unidades_por_pallet": por_pallet.get(clave),
                                               "kilos_por_unidad": kilos.get(clave)}
                resumen["productos_nuevos"] += 1
            else:
                # Producto que ya existía sin dato de pallet o de kilos: se completa con el de la planilla.
                existente = dict(productos_existentes[clave])
                for columna, valores in (("unidades_por_pallet", por_pallet), ("kilos_por_unidad", kilos)):
                    if valores.get(clave) and not existente.get(columna):
                        db.execute(f"UPDATE productos SET {columna} = ? WHERE id = ?",
                                   (valores[clave], existente["id"]))
                        existente[columna] = valores[clave]
                productos_existentes[clave] = existente
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
