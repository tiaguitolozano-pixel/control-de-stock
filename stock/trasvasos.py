"""Trasvasos: pasar un producto de un envase a otro por orden del cliente.

Por ejemplo, un isotanque que llega del puerto se descarga en tambores o cisternas, o unas cisternas
que están en el depósito se pasan a tambores. Un trasvaso queda guardado así:
  - lo que queda envasado entra al stock de la empresa como un ingreso (con su lote, pallets, etc.);
  - si el producto ya estaba en el depósito, lo que se vacía sale del stock como un egreso;
  - los envases vacíos que se usan salen de los vacíos que la empresa tiene en el depósito (los manda
    el cliente y entran con un ingreso) y los envases que se vaciaron le quedan como sucios (ver vacios.py).
Los dos remitos llevan el número de orden y se anulan juntos, desde el trasvaso.
"""
from collections import defaultdict
from datetime import date

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

from . import fotos as ft
from . import insumos as ins
from . import pallets as pl
from . import remitos as rm
from . import vacios
from .db import get_db
from .formato import formato_numero
from .rutas import (
    _empresas,
    _excel,
    _fecha_valida,
    _int_o_none,
    _texto,
    _transportes_previos,
    items_de_movimiento,
)
from .servicios import CAMPOS_TRANSPORTE, normalizar_transporte, parse_numero, registrar_movimiento

bp = Blueprint("trasvasos", __name__)

ORIGENES = {"AFUERA": "Llega de afuera", "DEPOSITO": "Del stock del depósito"}
LIMITE_LISTA = 500


def remito_de_orden(orden):
    """El número que llevan los remitos del trasvaso en el historial de movimientos."""
    return orden if orden.lower().startswith("orden") else f"Orden {orden}"


def _trasvaso_o_404(trasvaso_id):
    t = get_db().execute(
        """SELECT t.*, e.nombre AS empresa FROM trasvasos t JOIN empresas e ON e.id = t.empresa_id
           WHERE t.id = ?""",
        (trasvaso_id,),
    ).fetchone()
    if t is None:
        abort(404)
    return t


def _remitos(trasvaso_id):
    """{'INGRESO': movimiento, 'EGRESO': movimiento} del trasvaso."""
    return {m["tipo"]: m for m in get_db().execute(
        "SELECT * FROM movimientos WHERE trasvaso_id = ? ORDER BY id", (trasvaso_id,))}


# --------------------------------------------------------------------- lista


def _filtros():
    return {
        "empresa_id": _int_o_none(request.args.get("empresa_id")),
        "desde": request.args.get("desde") if _fecha_valida(request.args.get("desde")) else "",
        "hasta": request.args.get("hasta") if _fecha_valida(request.args.get("hasta")) else "",
        "orden": (request.args.get("orden") or "").strip(),
        "anulados": request.args.get("anulados") == "1",
    }


def _resumen_productos(filas):
    """Por cada producto: '80 tambores de chapa' y su código."""
    return [{"texto": f"{formato_numero(r['cantidad'])} {r['unidad'].lower()}", "codigo": r["codigo"],
             "descripcion": r["descripcion"], "cantidad": r["cantidad"], "unidad": r["unidad"]} for r in filas]


def lista_trasvasos(f, limite=LIMITE_LISTA):
    where, params = [], []
    if f["empresa_id"]:
        where.append("t.empresa_id = ?")
        params.append(f["empresa_id"])
    if f["desde"]:
        where.append("t.fecha >= ?")
        params.append(f["desde"])
    if f["hasta"]:
        where.append("t.fecha <= ?")
        params.append(f["hasta"])
    if f["orden"]:
        where.append("(t.orden LIKE ? OR t.remito LIKE ?)")
        params += [f"%{f['orden']}%"] * 2
    if not f["anulados"]:
        where.append("t.anulado = 0")
    db = get_db()
    filas = db.execute(
        """SELECT t.*, e.nombre AS empresa, mi.contenedor, mi.tipo_contenedor
           FROM trasvasos t JOIN empresas e ON e.id = t.empresa_id
           LEFT JOIN movimientos mi ON mi.trasvaso_id = t.id AND mi.tipo = 'INGRESO'"""
        + (" WHERE " + " AND ".join(where) if where else "")
        + " ORDER BY t.fecha DESC, t.id DESC LIMIT ?",
        params + [limite],
    ).fetchall()
    ids = [t["id"] for t in filas]
    productos = defaultdict(lambda: {"INGRESO": [], "EGRESO": []})
    kilos = defaultdict(lambda: {"INGRESO": 0.0, "EGRESO": 0.0})
    sin_kilos = defaultdict(lambda: {"INGRESO": False, "EGRESO": False})
    if ids:
        for r in db.execute(
            f"""SELECT m.trasvaso_id, m.tipo, p.codigo, p.descripcion, p.unidad, p.kilos_por_unidad,
                       SUM(i.cantidad) AS cantidad
                FROM movimientos m JOIN movimiento_items i ON i.movimiento_id = m.id
                JOIN productos p ON p.id = i.producto_id
                WHERE m.trasvaso_id IN ({','.join('?' * len(ids))})
                GROUP BY m.trasvaso_id, m.tipo, p.id ORDER BY p.codigo""",
            ids,
        ):
            productos[r["trasvaso_id"]][r["tipo"]].append(r)
            if r["kilos_por_unidad"]:
                kilos[r["trasvaso_id"]][r["tipo"]] += r["cantidad"] * r["kilos_por_unidad"]
            else:
                sin_kilos[r["trasvaso_id"]][r["tipo"]] = True
    envases = vacios.de_trasvasos(ids)
    res = []
    for t in filas:
        k = kilos[t["id"]]
        kilos_origen = round(k["EGRESO"], 3) if t["origen"] == "DEPOSITO" else t["kilos_origen"]
        kilos_destino = round(k["INGRESO"], 3)
        res.append({
            **dict(t),
            "sale": _resumen_productos(productos[t["id"]]["EGRESO"]),
            "envasado": _resumen_productos(productos[t["id"]]["INGRESO"]),
            "kilos_origen_calc": kilos_origen if (t["origen"] == "AFUERA" or not sin_kilos[t["id"]]["EGRESO"]) else None,
            "kilos_destino": None if sin_kilos[t["id"]]["INGRESO"] and not kilos_destino else kilos_destino,
            "envases": envases.get(t["id"], []),
        })
    for r in res:
        r["diferencia"] = (round(r["kilos_destino"] - r["kilos_origen_calc"], 3)
                           if r["kilos_destino"] is not None and r["kilos_origen_calc"] else None)
    return res


@bp.route("/trasvasos")
def trasvasos():
    f = _filtros()
    filas = lista_trasvasos(f)
    envases = vacios.resumen(f["empresa_id"]) if f["empresa_id"] else []
    return render_template("trasvasos.html", trasvasos=filas, f=f, empresas=_empresas(),
                           envases=envases, origenes=ORIGENES, limite=LIMITE_LISTA)


def _texto_envases(envases, clave):
    return ", ".join(f"{formato_numero(e[clave])} {e['nombre'].lower()}" for e in envases if e[clave])


@bp.route("/trasvasos/excel")
def trasvasos_excel():
    f = _filtros()
    filas = []
    for t in reversed(lista_trasvasos(f, limite=100000)):
        filas.append([
            t["fecha"], t["orden"], t["remito"] or "", t["empresa"], ORIGENES[t["origen"]],
            t["contenedor"] or "", t["tipo_contenedor"] or "",
            "; ".join(f"{p['texto']} ({p['codigo']})" for p in t["sale"]),
            t["kilos_origen_calc"],
            "; ".join(f"{p['texto']} ({p['codigo']})" for p in t["envasado"]),
            t["kilos_destino"], t["diferencia"],
            _texto_envases(t["envases"], "vacios"), _texto_envases(t["envases"], "sucios"),
            t["observaciones"] or "", "Sí" if t["anulado"] else "",
        ])
    encabezados = ["Fecha", "N° de orden", "Remito", "Empresa", "Origen", "Contenedor", "Tipo de contenedor",
                   "Sale del depósito", "Kilos del origen", "Queda envasado", "Kilos envasados",
                   "Diferencia de kilos", "Envases vacíos usados", "Envases sucios que quedan",
                   "Observaciones", "Anulado"]
    return _excel("trasvasos", "Trasvasos", encabezados, filas)


# ---------------------------------------------------------------- cargar uno


def _form(form, destino, origen, mostrar_repetido=False, textos_insumos=None, textos_envases=None):
    return render_template(
        "trasvaso_form.html", empresas=_empresas(solo_activas=True), form=form, renglones=destino,
        renglones_origen=origen, mostrar_repetido=mostrar_repetido, previos=_transportes_previos(),
        insumos=ins.lista(), campos_insumo=ins.campos, textos_insumos=textos_insumos or {},
        envases=vacios.tipos_para_cargar(), campo_envase=vacios.campo,
        columnas_envases=vacios.COLUMNAS["TRASVASO"], textos_envases=textos_envases or {},
    )


def _con_prefijo(texto, errores):
    return [f"{texto}: {e}" for e in errores]


@bp.route("/trasvasos/nuevo", methods=["GET", "POST"])
def trasvaso_nuevo():
    if request.method == "GET":
        return _form({"empresa_id": _int_o_none(request.args.get("empresa_id")), "fecha": date.today().isoformat(),
                      "origen": "AFUERA", "crear_pallets": "1"}, [], [])

    db = get_db()
    empresa_id = _int_o_none(request.form.get("empresa_id"))
    fecha = _texto("fecha")
    orden = _texto("orden")
    remito = _texto("remito")
    observaciones = _texto("observaciones")
    origen = "DEPOSITO" if request.form.get("origen") == "DEPOSITO" else "AFUERA"
    kilos_texto = _texto("kilos_origen") if origen == "AFUERA" else ""
    kilos_origen = parse_numero(kilos_texto) if kilos_texto else None
    transporte = normalizar_transporte({c: _texto(c) for c in CAMPOS_TRANSPORTE})
    destino = rm.leer_renglones(request.form)
    sale = rm.leer_renglones(request.form, "o_") if origen == "DEPOSITO" else []
    insumos, errores_insumos, textos_insumos = ins.leer_formulario(request.form)
    envases, errores_envases, textos_envases = vacios.leer_formulario(request.form, "TRASVASO")
    fotos, errores_fotos = ft.leer_subidas(request.files.getlist("fotos"))

    errores = []
    if not empresa_id:
        errores.append("Elegí la empresa.")
    if not _fecha_valida(fecha):
        errores.append("La fecha no es válida.")
    if not orden:
        errores.append("El número de orden es obligatorio.")
    if kilos_texto and (kilos_origen is None or kilos_origen < 0):
        errores.append("Los kilos que llegan no son un número válido.")
    if origen == "DEPOSITO" and not sale:
        errores.append("Origen: elegí qué producto del depósito se vacía y de qué lote sale.")
    if not destino:
        errores.append("Destino: cargá cómo queda envasado el producto (al menos un renglón).")
    errores += errores_insumos + errores_envases + errores_fotos

    productos = rm.productos_de_empresa(empresa_id)
    items_salen, _, errores_salen = rm.revisar_renglones("EGRESO", sale, productos)
    items_entran, vencimientos, errores_entran = rm.revisar_renglones("INGRESO", destino, productos)
    errores += _con_prefijo("Origen", errores_salen) + _con_prefijo("Destino", errores_entran)
    if not errores:
        errores += _con_prefijo("Origen", rm.chequear_stock(items_salen, productos))
        errores += vacios.chequear(empresa_id, "TRASVASO", envases)

    mostrar_repetido = False
    if empresa_id and orden and not errores:
        repetido = db.execute(
            "SELECT id FROM trasvasos WHERE empresa_id = ? AND orden = ? AND anulado = 0", (empresa_id, orden)
        ).fetchone()
        if repetido and request.form.get("confirmar_repetido") != "1":
            mostrar_repetido = True
            errores.append("Ya hay un trasvaso con ese número de orden para esta empresa. "
                           "Si es correcto, marcá la casilla 'Cargar igual' y guardá de nuevo.")

    if errores:
        for e in dict.fromkeys(errores):  # sin repetidos
            flash(e, "error")
        if fotos:
            flash("Las fotos no se guardaron: volvé a elegirlas antes de guardar.", "aviso")
        form = {"empresa_id": empresa_id, "fecha": fecha, "orden": orden, "remito": remito,
                "observaciones": observaciones, "origen": origen, "kilos_origen": _texto("kilos_origen"),
                "crear_pallets": request.form.get("crear_pallets", ""), **transporte}
        return _form(form, destino, rm.leer_renglones(request.form, "o_"), mostrar_repetido,
                     textos_insumos, textos_envases)

    cur = db.execute(
        """INSERT INTO trasvasos (empresa_id, fecha, orden, remito, origen, kilos_origen, observaciones)
           VALUES (?,?,?,?,?,?,?)""",
        (empresa_id, fecha, orden, remito or None, origen, kilos_origen, observaciones or None),
    )
    trasvaso_id = cur.lastrowid
    numero = remito_de_orden(orden)
    remitos = []
    if origen == "DEPOSITO":
        lineas, plan = rm.lineas_de_egreso(items_salen, productos)
        egreso_id = registrar_movimiento("EGRESO", empresa_id, fecha, numero, observaciones, lineas)
        pl.registrar_salida_de_pallets(egreso_id, plan)
        remitos.append(egreso_id)
    lineas = rm.lineas_de_ingreso(items_entran, vencimientos)
    ingreso_id = registrar_movimiento("INGRESO", empresa_id, fecha, numero, observaciones, lineas, transporte, insumos)
    remitos.append(ingreso_id)
    db.execute(f"UPDATE movimientos SET trasvaso_id = ? WHERE id IN ({','.join('?' * len(remitos))})",
               [trasvaso_id, *remitos])
    vacios.registrar(empresa_id, fecha, "TRASVASO", envases, trasvaso_id=trasvaso_id)
    creados, avisos = [], []
    if request.form.get("crear_pallets") == "1":
        creados, avisos = pl.crear_pallets_de_ingreso(ingreso_id)
    cantidad_fotos = ft.guardar(ingreso_id, fotos)
    db.commit()
    mensaje = f"Trasvaso guardado (orden {orden})."
    if cantidad_fotos:
        mensaje += " Con 1 foto." if cantidad_fotos == 1 else f" Con {cantidad_fotos} fotos."
    if creados:
        mensaje += (" Se armó 1 pallet" if len(creados) == 1 else f" Se armaron {len(creados)} pallets") \
            + ": imprimí sus etiquetas con el botón de abajo."
    flash(mensaje, "ok")
    for aviso in avisos:
        flash(aviso, "aviso")
    return redirect(url_for("trasvasos.trasvaso_detalle", trasvaso_id=trasvaso_id))


# -------------------------------------------------------------------- detalle


@bp.route("/trasvasos/<int:trasvaso_id>")
def trasvaso_detalle(trasvaso_id):
    t = _trasvaso_o_404(trasvaso_id)
    remitos = _remitos(trasvaso_id)
    ingreso, egreso = remitos.get("INGRESO"), remitos.get("EGRESO")
    entra, totales_entra = items_de_movimiento(ingreso["id"]) if ingreso else ([], None)
    sale, totales_sale = items_de_movimiento(egreso["id"]) if egreso else ([], None)
    if t["origen"] == "DEPOSITO":
        kilos_origen = None if totales_sale is None or totales_sale["sin_kilos"] else totales_sale["kilos"]
    else:
        kilos_origen = t["kilos_origen"]
    kilos_destino = None if totales_entra is None or (totales_entra["sin_kilos"] and not totales_entra["kilos"]) \
        else totales_entra["kilos"]
    diferencia = round(kilos_destino - kilos_origen, 3) if kilos_destino is not None and kilos_origen else None
    armados = []
    if ingreso:
        armados = pl.pallets(ids=[r[0] for r in get_db().execute(
            "SELECT id FROM pallets WHERE movimiento_id = ?", (ingreso["id"],))], con_contenido=False)
    return render_template(
        "trasvaso_detalle.html", t=t, ingreso=ingreso, egreso=egreso, entra=entra, totales_entra=totales_entra,
        sale=sale, totales_sale=totales_sale, kilos_origen=kilos_origen, kilos_destino=kilos_destino,
        diferencia=diferencia, envases=vacios.de_trasvaso(trasvaso_id), origenes=ORIGENES,
        insumos=ins.de_movimiento(ingreso["id"]) if ingreso else [], nombre_insumo=ins.nombre_renglon,
        fotos=ft.de_movimiento(ingreso["id"]) if ingreso else [],
        armados=sorted(armados, key=lambda p: p["id"]),
    )


@bp.route("/trasvasos/<int:trasvaso_id>/anular", methods=["POST"])
def trasvaso_anular(trasvaso_id):
    t = _trasvaso_o_404(trasvaso_id)
    volver = redirect(url_for("trasvasos.trasvaso_detalle", trasvaso_id=trasvaso_id))
    if t["anulado"]:
        flash("Ese trasvaso ya estaba anulado.", "error")
        return volver
    motivo = _texto("motivo")
    if not motivo:
        flash("Escribí el motivo de la anulación.", "error")
        return volver
    db = get_db()
    remitos = {tipo: m for tipo, m in _remitos(trasvaso_id).items() if not m["anulado"]}
    # Lo que salió del depósito vuelve al stock a la vez que se saca lo que entró envasado.
    vuelve = defaultdict(float)
    if "EGRESO" in remitos:
        for r in db.execute("SELECT lote_id, cantidad FROM movimiento_items WHERE movimiento_id = ?",
                            (remitos["EGRESO"]["id"],)):
            vuelve[r["lote_id"]] += r["cantidad"]
    if "INGRESO" in remitos:
        problema = rm.problema_al_anular_ingreso(remitos["INGRESO"], vuelve)
        if problema:
            flash(problema, "error")
            return volver
    problemas = vacios.problemas_al_anular(trasvaso_id=trasvaso_id)
    if problemas:
        flash("No se puede anular: " + "; ".join(problemas)
              + ". Primero corregí los envases en Trasvasos → Envases vacíos y sucios.", "error")
        return volver
    db.execute("UPDATE movimientos SET anulado = 1, motivo_anulacion = ? WHERE trasvaso_id = ? AND anulado = 0",
               (f"Trasvaso anulado: {motivo}", trasvaso_id))
    vacios.anular(trasvaso_id=trasvaso_id)
    db.execute("UPDATE trasvasos SET anulado = 1, motivo_anulacion = ? WHERE id = ?", (motivo, trasvaso_id))
    db.commit()
    flash("Trasvaso anulado. Se recalcularon el stock de la mercadería y el de los envases.", "ok")
    return volver


# ------------------------------------------------------ envases vacíos y sucios


@bp.route("/trasvasos/envases", methods=["GET", "POST"])
def envases_vacios():
    if request.method == "POST":
        empresa_id = _int_o_none(request.form.get("empresa_id"))
        fecha = _texto("fecha") or date.today().isoformat()
        texto = _texto("cantidad")
        cantidad = parse_numero(texto) if texto else None
        if not _fecha_valida(fecha):
            error = "La fecha no es válida."
        else:
            error = vacios.registrar_accion(empresa_id, _int_o_none(request.form.get("envase_id")),
                                            request.form.get("accion"), cantidad, fecha, _texto("observaciones"))
        if error:
            flash(error, "error")
        else:
            get_db().commit()
            flash("Listo, quedó anotado.", "ok")
        return redirect(url_for("trasvasos.envases_vacios", empresa_id=empresa_id or None))
    empresa_id = _int_o_none(request.args.get("empresa_id"))
    return render_template(
        "envases_vacios.html", empresa_id=empresa_id, empresas=_empresas(),
        empresas_activas=_empresas(solo_activas=True), resumen=vacios.resumen(empresa_id),
        tipos=vacios.tipos_para_anotar(), lleva_alguno=bool(vacios.tipos_para_cargar()),
        historial=vacios.historial(empresa_id), acciones=vacios.ACCIONES, motivos=vacios.MOTIVOS,
    )


@bp.route("/api/envases")
def api_envases():
    """Los envases vacíos y sucios que la empresa tiene en el depósito: {envase_id: {'VACIO': x, 'SUCIO': y}}."""
    empresa_id = _int_o_none(request.args.get("empresa_id"))
    return jsonify({str(eid): s for eid, s in vacios.saldos_de(empresa_id).items()} if empresa_id else {})
