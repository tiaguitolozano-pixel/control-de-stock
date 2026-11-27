"""Corregir lo que ya está guardado: editar o borrar remitos, y el registro de cambios.

Al editar un remito, los renglones que no cambian quedan como estaban (con sus pallets). Los que cambian:
  - en un egreso, la mercadería vuelve a sus pallets y se vuelve a decidir de qué pallets sale;
  - en un ingreso, los pallets que armó el remito y que no se tocaron después se ajustan a la cantidad
    nueva (se llenan en orden; los que sobran se borran y si falta lugar se arman más). Los que ya tuvieron
    salidas u otros cambios quedan como están.
Después se revisa que ningún lote quede con saldo negativo en ninguna fecha (ver remitos.saldos_por_fecha) y
que en los pallets no haya más mercadería que en el stock. Si algo no cierra, no se guarda nada.

Cada corrección y cada remito borrado quedan anotados en la tabla cambios: qué cambió y cuándo.
"""
import os
from collections import defaultdict

from flask import current_app

from . import fotos as ft
from . import insumos as ins
from . import pallets as pl
from . import remitos as rm
from . import vacios
from .db import get_db, stock_por_lote_id
from .formato import formato_fecha, formato_numero

EPS = 1e-6
TIPOS = {"INGRESO": "Ingreso", "EGRESO": "Egreso"}
CAMPOS = [("fecha", "Fecha"), ("remito", "N° de remito"), ("chofer", "Chofer"), ("transporte", "Empresa de transporte"),
          ("patente_camion", "Patente del camión"), ("patente_semi", "Patente del semi"),
          ("contenedor", "Contenedor"), ("tipo_contenedor", "Tipo de contenedor"),
          ("observaciones", "Observación")]


def _n(valor):
    return formato_numero(valor)


def _marcas(valores):
    return ",".join("?" * len(valores))


# ------------------------------------------------------------- cómo era un remito


def foto(movimiento_id, producto_id=None):
    """Todo lo de un remito, para comparar antes y después de un cambio o anotar lo que tenía uno borrado.

    producto_id: solo los renglones de ese producto (el stock inicial desde la pantalla del producto).
    """
    db = get_db()
    m = db.execute(
        """SELECT m.*, e.nombre AS empresa FROM movimientos m JOIN empresas e ON e.id = m.empresa_id
           WHERE m.id = ?""",
        (movimiento_id,),
    ).fetchone()
    sql = """SELECT i.id, i.producto_id, i.lote_id, i.cantidad, p.codigo, p.descripcion, p.unidad,
                    l.lote, l.vencimiento
             FROM movimiento_items i JOIN productos p ON p.id = i.producto_id JOIN lotes l ON l.id = i.lote_id
             WHERE i.movimiento_id = ?"""
    params = [movimiento_id]
    if producto_id:
        sql += " AND i.producto_id = ?"
        params.append(producto_id)
    items = [dict(r) for r in db.execute(sql + " ORDER BY i.id", params)]
    if producto_id:
        return {"m": dict(m), "items": items, "insumos": [], "envases": [], "fotos": 0}
    return {
        "m": dict(m), "items": items,
        "insumos": [{"nombre": ins.nombre_renglon(r), "cantidad": r["cantidad"], "unidad": r["unidad"]}
                    for r in ins.de_movimiento(movimiento_id)],
        "envases": vacios.de_movimiento(movimiento_id),
        "fotos": len(ft.de_movimiento(movimiento_id)),
    }


def _nombre_item(i):
    return f"{i['codigo']} - {i['descripcion']} ({('lote ' + i['lote']) if i['lote'] else 'sin lote'})"


def _envases(lista):
    """{(nombre, 'vacíos'|'sucios'): cantidad}."""
    res = {}
    for e in lista:
        for clave, estado in (("vacios", "VACIO"), ("sucios", "SUCIO")):
            if e[clave]:
                res[(e["nombre"], vacios.palabras(e["nombre"], estado)[1])] = e[clave]
    return res


def texto_remito(f):
    """Lo que tenía un remito, un dato por renglón (para el registro de los borrados)."""
    m = f["m"]
    lineas = [f"{TIPOS.get(m['tipo'], m['tipo'])} del {formato_fecha(m['fecha'])}, remito {m['remito']}, "
              f"de {m['empresa']}."]
    for i in f["items"]:
        lineas.append(f"{_nombre_item(i)}: {_n(i['cantidad'])} {i['unidad'].lower()}.")
    for i in f["insumos"]:
        lineas.append(f"{i['nombre']}: {_n(i['cantidad'])} {i['unidad']}.")
    for (nombre, estado), cantidad in _envases(f["envases"]).items():
        lineas.append(f"{nombre} {estado}: {_n(cantidad)}.")
    transporte = [f"{nombre.lower()} {m[campo]}" for campo, nombre in CAMPOS[2:8] if m.get(campo)]
    if transporte:
        lineas.append("Transporte: " + ", ".join(transporte) + ".")
    if m.get("observaciones"):
        lineas.append(f"Observación: {m['observaciones']}")
    if f["fotos"]:
        lineas.append("Tenía 1 foto." if f["fotos"] == 1 else f"Tenía {f['fotos']} fotos.")
    if m.get("anulado"):
        lineas.append(f"Estaba anulado ({m['motivo_anulacion'] or 'sin motivo'}).")
    return "\n".join(lineas)


def diferencias(a, b):
    """Qué cambió entre dos fotos del mismo remito: una línea por dato."""
    lineas = []
    for campo, nombre in CAMPOS:
        va, vb = (a["m"].get(campo) or "").strip(), (b["m"].get(campo) or "").strip()
        if va != vb:
            if campo == "fecha":
                va, vb = formato_fecha(va), formato_fecha(vb)
            lineas.append(f"{nombre}: {va or '(vacío)'} → {vb or '(vacío)'}")
    ia = {(i["producto_id"], i["lote_id"]): i for i in a["items"]}
    ib = {(i["producto_id"], i["lote_id"]): i for i in b["items"]}
    orden = sorted(ia.keys() | ib.keys(), key=lambda k: (
        (ia.get(k) or ib.get(k))["codigo"].lower(), ((ia.get(k) or ib.get(k))["lote"] or "").lower()))
    for k in orden:
        x, y = ia.get(k), ib.get(k)
        if x and y:
            if abs(x["cantidad"] - y["cantidad"]) > EPS:
                lineas.append(f"{_nombre_item(x)}: {_n(x['cantidad'])} → {_n(y['cantidad'])} {y['unidad'].lower()}")
            if (x["vencimiento"] or "") != (y["vencimiento"] or ""):
                lineas.append(f"{_nombre_item(x)}: vencimiento {formato_fecha(x['vencimiento']) or '(sin)'} → "
                              f"{formato_fecha(y['vencimiento']) or '(sin)'}")
        elif x:
            lineas.append(f"Se quitó {_nombre_item(x)}: {_n(x['cantidad'])} {x['unidad'].lower()}")
        else:
            lineas.append(f"Se agregó {_nombre_item(y)}: {_n(y['cantidad'])} {y['unidad'].lower()}")
    for etiqueta, va, vb in (
        ("", {i["nombre"]: i["cantidad"] for i in a["insumos"]}, {i["nombre"]: i["cantidad"] for i in b["insumos"]}),
        ("", {f"{n} {e}": c for (n, e), c in _envases(a["envases"]).items()},
         {f"{n} {e}": c for (n, e), c in _envases(b["envases"]).items()}),
    ):
        for nombre in sorted(va.keys() | vb.keys()):
            x, y = va.get(nombre, 0), vb.get(nombre, 0)
            if abs(x - y) > EPS:
                lineas.append(f"{etiqueta}{nombre}: {_n(x)} → {_n(y)}")
    if b["fotos"] > a["fotos"]:
        nuevas = b["fotos"] - a["fotos"]
        lineas.append("Se agregó 1 foto" if nuevas == 1 else f"Se agregaron {nuevas} fotos")
    return lineas


# ----------------------------------------------------------- registro de cambios


def anotar(accion, detalle, movimiento_id=None, trasvaso_id=None, empresa_id=None, tipo=None, fecha=None,
           remito=None):
    """Anota una corrección (EDITADO) o un borrado (BORRADO). No hace commit."""
    get_db().execute(
        """INSERT INTO cambios (accion, movimiento_id, trasvaso_id, empresa_id, tipo, fecha, remito, detalle)
           VALUES (?,?,?,?,?,?,?,?)""",
        (accion, movimiento_id, trasvaso_id, empresa_id, tipo, fecha, remito, detalle),
    )


def anotar_remito(accion, f, detalle):
    m = f["m"]
    anotar(accion, detalle, movimiento_id=m["id"], trasvaso_id=m.get("trasvaso_id"), empresa_id=m["empresa_id"],
           tipo=m["tipo"], fecha=m["fecha"], remito=m["remito"])


def cambios(movimiento_id=None, trasvaso_id=None, empresa_id=None, accion=None, limite=300):
    """Los cambios anotados, los más nuevos primero. 'existe' dice si el remito o el trasvaso sigue estando."""
    where, params = [], []
    if movimiento_id:
        where.append("c.movimiento_id = ?")
        params.append(movimiento_id)
    if trasvaso_id:
        where.append("c.trasvaso_id = ?")
        params.append(trasvaso_id)
    if empresa_id:
        where.append("c.empresa_id = ?")
        params.append(empresa_id)
    if accion:
        where.append("c.accion = ?")
        params.append(accion)
    return [
        {**dict(r), "lineas": r["detalle"].split("\n")}
        for r in get_db().execute(
            """SELECT c.*, e.nombre AS empresa,
                      EXISTS (SELECT 1 FROM movimientos m WHERE m.id = c.movimiento_id) AS existe_remito,
                      EXISTS (SELECT 1 FROM trasvasos t WHERE t.id = c.trasvaso_id) AS existe_trasvaso
               FROM cambios c LEFT JOIN empresas e ON e.id = c.empresa_id"""
            + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY c.id DESC LIMIT ?",
            params + [limite],
        )
    ]


# ------------------------------------------------------------------ revisiones


def lotes_de(movimiento_ids):
    ids = [x for x in movimiento_ids if x]
    if not ids:
        return set()
    return {r[0] for r in get_db().execute(
        f"SELECT DISTINCT lote_id FROM movimiento_items WHERE movimiento_id IN ({_marcas(ids)})", ids)}


def exceso_en_pallets(lote_ids):
    """{lote_id: cuánto más hay en pallets con etiqueta que en el stock} (solo los que tienen de más)."""
    ids = list(lote_ids)
    if not ids:
        return {}
    stock = stock_por_lote_id(ids)
    en_pallets = defaultdict(float)
    for r in pl.contenido(lote_ids=ids):
        en_pallets[r["lote_id"]] += r["cantidad"]
    return {lid: round(en_pallets[lid] - stock.get(lid, 0), 6) for lid in ids
            if en_pallets[lid] - stock.get(lid, 0) > EPS}


class Estado:
    """Cómo estaban los lotes antes de un cambio, para revisar después que nada quede peor."""

    def __init__(self, lote_ids):
        self.lote_ids = set(lote_ids)
        self.saldos = rm.saldos_por_fecha(self.lote_ids)
        self.exceso = exceso_en_pallets(self.lote_ids)

    def problemas(self, lote_ids=(), inicio="No se puede guardar: ",
                  final=", porque ya salió mercadería de ese lote con esa fecha o antes", fecha=None):
        """Lo que quedó mal después del cambio (hay que llamarla antes del commit).

        fecha: la fecha del ingreso que se corrige. Si un lote queda negativo antes de esa fecha, seguramente la
        fecha está mal: se dice desde qué día hacía falta la mercadería.
        """
        ids = self.lote_ids | set(lote_ids)
        negativos = rm.negativos_nuevos(self.saldos, rm.saldos_por_fecha(ids))
        errores = [t + "." for t in rm.textos_negativos(negativos, inicio, final)]
        antes = sorted(f for f, _ in negativos.values() if fecha and f < fecha)
        if antes:
            errores.append(f"{inicio}si la mercadería ya estaba en el depósito el {formato_fecha(antes[0])}, poné esa "
                           "fecha o una anterior.")
        lotes = rm.datos_lotes(ids)
        for lid, exceso in exceso_en_pallets(ids).items():
            if exceso > self.exceso.get(lid, 0) + EPS and lid in lotes:
                errores.append(
                    f"{inicio}en los pallets con etiqueta de {rm.nombre_lote(lotes[lid])} quedarían {_n(exceso)} "
                    f"{lotes[lid]['unidad'].lower()} más de lo que hay en el stock. Primero sacá esa mercadería de "
                    "los pallets (en Pallets, abrí el pallet y usá Sacar).")
        return errores


# ------------------------------------------------------ renglones de un remito


def _borrar_pallet(pallet_id):
    db = get_db()
    db.execute("DELETE FROM pallet_movs WHERE pallet_id = ?", (pallet_id,))
    db.execute("DELETE FROM pallet_ubicaciones WHERE pallet_id = ?", (pallet_id,))
    db.execute("DELETE FROM pallets WHERE id = ?", (pallet_id,))


def _ajustar_pallets(m, item, nueva):
    """Los pallets que armó un renglón de un ingreso, con la cantidad nueva del renglón (0 si se quita).

    Devuelve (error, pallets nuevos, pallets borrados). No hace commit.
    """
    db = get_db()
    propios = db.execute(
        """SELECT pallet_id, SUM(cantidad) AS cantidad FROM pallet_movs WHERE item_id = ?
           GROUP BY pallet_id ORDER BY pallet_id""",
        (item["id"],),
    ).fetchall()
    if not propios:
        return None, [], []
    ids = [r["pallet_id"] for r in propios]
    # Los pallets que tuvieron otros movimientos (salidas, juntados, mercadería agregada) quedan como están.
    tocados = {r[0] for r in db.execute(
        f"""SELECT DISTINCT pallet_id FROM pallet_movs
            WHERE pallet_id IN ({_marcas(ids)}) AND (item_id IS NULL OR item_id <> ?)""",
        ids + [item["id"]],
    )}
    fijo = round(sum(r["cantidad"] for r in propios if r["pallet_id"] in tocados), 6)
    libres = [r["pallet_id"] for r in propios if r["pallet_id"] not in tocados]
    resto = round(nueva - fijo, 6)
    if resto < -EPS:
        codigos = ", ".join(pl.codigo_pallet(p) for p in sorted(tocados))
        if nueva <= EPS:
            return (f"{_nombre_item(item)}: no se puede quitar ese renglón porque sus pallets ({codigos}) ya "
                    "tuvieron salidas u otros movimientos. Corregí la diferencia con un egreso."), [], []
        return (f"{_nombre_item(item)}: no puede quedar en {_n(nueva)} porque {_n(fijo)} ya están en pallets que "
                f"tuvieron salidas u otros movimientos ({codigos}). Corregí la diferencia con un egreso."), [], []
    por_pallet = item["unidades_por_pallet"]
    partes = pl.repartir(resto, por_pallet) if por_pallet else ([resto] if resto > EPS else [])
    creados, borrados = [], []
    for n, pallet_id in enumerate(libres):
        if n < len(partes):
            db.execute("DELETE FROM pallet_movs WHERE pallet_id = ? AND item_id = ?", (pallet_id, item["id"]))
            pl._mov(pallet_id, item["producto_id"], item["lote_id"], partes[n], "INGRESO", item["id"])
        else:
            _borrar_pallet(pallet_id)
            borrados.append(pallet_id)
    for q in partes[len(libres):]:
        pallet_id = pl.nuevo_pallet(m["empresa_id"], m["id"])
        pl._mov(pallet_id, item["producto_id"], item["lote_id"], q, "INGRESO", item["id"])
        creados.append(pallet_id)
    return None, creados, borrados


def aplicar_items(m, lineas, solo_producto=None):
    """Deja los renglones del remito m como `lineas` [(producto_id, lote_id, cantidad)], con sus pallets.

    solo_producto: solo se tocan los renglones de ese producto (el stock inicial desde el producto).
    Devuelve (errores, pallets nuevos, pallets borrados). No hace commit: si hay errores, hay que deshacer
    (rollback).
    """
    db = get_db()
    sql = """SELECT i.*, p.codigo, p.descripcion, p.unidades_por_pallet, l.lote
             FROM movimiento_items i JOIN productos p ON p.id = i.producto_id JOIN lotes l ON l.id = i.lote_id
             WHERE i.movimiento_id = ?"""
    params = [m["id"]]
    if solo_producto:
        sql += " AND i.producto_id = ?"
        params.append(solo_producto)
    viejos = defaultdict(list)
    for r in db.execute(sql + " ORDER BY i.id", params):
        viejos[(r["producto_id"], r["lote_id"])].append(r)
    nuevos = defaultdict(float)
    for pid, lid, cant in lineas:
        nuevos[(pid, lid)] += cant
    por_pallet = {r["id"]: r["unidades_por_pallet"] for r in db.execute(
        "SELECT id, unidades_por_pallet FROM productos WHERE empresa_id = ?", (m["empresa_id"],))}

    def igual(k):
        return len(viejos.get(k, [])) == 1 and k in nuevos and abs(viejos[k][0]["cantidad"] - nuevos[k]) <= EPS

    cambian = sorted(k for k in viejos.keys() | nuevos.keys() if not igual(k))
    errores, creados, borrados = [], [], []
    if m["tipo"] == "EGRESO":
        # Lo que salía vuelve a sus pallets y se decide de nuevo de cuáles sale (como en un egreso nuevo).
        for k in cambian:
            for viejo in viejos.get(k, []):
                db.execute("DELETE FROM pallet_movs WHERE item_id = ?", (viejo["id"],))
                db.execute("DELETE FROM movimiento_items WHERE id = ?", (viejo["id"],))
        plan = {k: pl.planear_salida(k[1], nuevos[k], por_pallet.get(k[0])) for k in cambian if k in nuevos}
        db.executemany(
            "INSERT INTO movimiento_items (movimiento_id, producto_id, lote_id, cantidad) VALUES (?,?,?,?)",
            [(m["id"], pid, lid, nuevos[(pid, lid)]) for pid, lid in cambian if (pid, lid) in nuevos],
        )
        pl.registrar_salida_de_pallets(m["id"], plan)
        return errores, creados, borrados

    arma_pallets = db.execute("SELECT 1 FROM pallets WHERE movimiento_id = ? LIMIT 1", (m["id"],)).fetchone()
    for k in cambian:
        lista, cant = viejos.get(k, []), nuevos.get(k)
        for n, viejo in enumerate(lista):
            # Si un renglón estaba repetido, el primero se queda con la cantidad nueva y los demás se quitan.
            nueva = cant if (n == 0 and cant is not None) else 0
            error, mas, menos = _ajustar_pallets(m, viejo, nueva)
            if error:
                errores.append(error)
                continue
            creados += mas
            borrados += menos
            if nueva > EPS:
                db.execute("UPDATE movimiento_items SET cantidad = ? WHERE id = ?", (nueva, viejo["id"]))
            else:
                db.execute("DELETE FROM movimiento_items WHERE id = ?", (viejo["id"],))
        if not lista and cant:
            item_id = db.execute(
                "INSERT INTO movimiento_items (movimiento_id, producto_id, lote_id, cantidad) VALUES (?,?,?,?)",
                (m["id"], k[0], k[1], cant),
            ).lastrowid
            if arma_pallets and por_pallet.get(k[0]):
                # El remito armó pallets con etiqueta: lo que se agrega también va en pallets.
                for q in pl.repartir(cant, por_pallet[k[0]])[:pl.MAX_PALLETS_POR_RENGLON]:
                    pallet_id = pl.nuevo_pallet(m["empresa_id"], m["id"])
                    pl._mov(pallet_id, k[0], k[1], q, "INGRESO", item_id)
                    creados.append(pallet_id)
    return errores, creados, borrados


def limpiar_lotes(lote_ids):
    """Borra los lotes que quedaron sin ningún remito ni pallet (por ejemplo, un lote mal escrito que se corrigió)."""
    ids = [x for x in lote_ids if x]
    if not ids:
        return
    get_db().execute(
        f"""DELETE FROM lotes WHERE id IN ({_marcas(ids)})
            AND NOT EXISTS (SELECT 1 FROM movimiento_items i WHERE i.lote_id = lotes.id)
            AND NOT EXISTS (SELECT 1 FROM pallet_movs pm WHERE pm.lote_id = lotes.id)""",
        ids,
    )


# ------------------------------------------------------------------- borrar


def problema_al_borrar(m, tambien=()):
    """Por qué no se puede borrar un remito (texto), o None. Un egreso o un remito anulado siempre se pueden.

    tambien: otros remitos que se borran a la vez (los dos remitos de un trasvaso).
    """
    if m["anulado"] or m["tipo"] != "INGRESO":
        return None
    return rm.problema_al_anular_ingreso(m, tambien, verbo="borrar")


def borrar_movimiento(m):
    """Borra un remito con sus renglones, insumos, envases, fotos y los pallets que armó.

    Devuelve los archivos de fotos para guardar aparte después del commit (ver archivar_fotos). No revisa
    nada (ver problema_al_borrar) ni hace commit.
    """
    db = get_db()
    mid = m["id"]
    items = [r[0] for r in db.execute("SELECT id FROM movimiento_items WHERE movimiento_id = ?", (mid,))]
    lotes = lotes_de([mid])
    if items:
        db.execute(f"DELETE FROM pallet_movs WHERE item_id IN ({_marcas(items)})", items)
    for (pallet_id,) in db.execute("SELECT id FROM pallets WHERE movimiento_id = ?", (mid,)).fetchall():
        if db.execute("SELECT 1 FROM pallet_movs WHERE pallet_id = ? LIMIT 1", (pallet_id,)).fetchone():
            db.execute("UPDATE pallets SET movimiento_id = NULL WHERE id = ?", (pallet_id,))
        else:
            _borrar_pallet(pallet_id)
    db.execute("DELETE FROM envase_movs WHERE movimiento_id = ?", (mid,))
    db.execute("DELETE FROM movimiento_insumos WHERE movimiento_id = ?", (mid,))
    archivos = [r["archivo"] for r in ft.de_movimiento(mid)]
    db.execute("DELETE FROM movimiento_fotos WHERE movimiento_id = ?", (mid,))
    db.execute("DELETE FROM movimiento_items WHERE movimiento_id = ?", (mid,))
    db.execute("DELETE FROM movimientos WHERE id = ?", (mid,))
    limpiar_lotes(lotes)
    return archivos


def archivar_fotos(archivos):
    """Las fotos de un remito borrado no se pierden: pasan a la carpeta datos/fotos/borradas."""
    base = current_app.config["FOTOS"]
    for archivo in archivos:
        origen = os.path.join(base, *archivo.split("/"))
        destino = os.path.join(base, "borradas", *archivo.split("/"))
        try:
            os.makedirs(os.path.dirname(destino), exist_ok=True)
            os.replace(origen, destino)
        except OSError:
            pass
