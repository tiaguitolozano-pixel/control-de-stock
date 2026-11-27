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

from . import correcciones as cr
from . import fotos as ft
from . import insumos as ins
from . import pallets as pl
from . import remitos as rm
from . import vacios
from .db import get_db
from .formato import formato_fecha, formato_numero
from .rutas import (
    _empresas,
    _excel,
    _fecha_valida,
    _int_o_none,
    _texto,
    _transportes_previos,
    items_de_movimiento,
)
from .servicios import CAMPOS_TRANSPORTE, buscar_lote, normalizar_transporte, parse_numero, registrar_movimiento

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


def _form(form, destino, origen, mostrar_repetido=False, textos_insumos=None, textos_envases=None, editando=None):
    """El formulario de un trasvaso nuevo o, con editando (el trasvaso guardado), el de una corrección."""
    remitos = _remitos(editando["id"]) if editando else {}
    return render_template(
        "trasvaso_form.html", editando_ingreso=remitos["INGRESO"]["id"] if remitos.get("INGRESO") else None,
        editando_egreso=remitos["EGRESO"]["id"] if remitos.get("EGRESO") else None,
        empresas=_empresas(solo_activas=not editando), form=form, renglones=destino,
        renglones_origen=origen, mostrar_repetido=mostrar_repetido, previos=_transportes_previos(),
        insumos=ins.lista(), campos_insumo=ins.campos, textos_insumos=textos_insumos or {},
        envases=vacios.tipos_para_cargar(), campo_envase=vacios.campo,
        columnas_envases=vacios.COLUMNAS["TRASVASO"], textos_envases=textos_envases or {}, editando=editando,
    )


def _con_prefijo(texto, errores):
    return [f"{texto}: {e}" for e in errores]


def _leer_trasvaso(t=None):
    """Lee y revisa el formulario de un trasvaso nuevo, o de uno guardado que se está corrigiendo (t).

    Devuelve (d, errores, mostrar_repetido).
    """
    db = get_db()
    remitos = _remitos(t["id"]) if t else {}
    ingreso, egreso = remitos.get("INGRESO"), remitos.get("EGRESO")
    empresa_id = t["empresa_id"] if t else _int_o_none(request.form.get("empresa_id"))
    origen = "DEPOSITO" if request.form.get("origen") == "DEPOSITO" else "AFUERA"
    kilos_texto = _texto("kilos_origen") if origen == "AFUERA" else ""
    d = {
        "empresa_id": empresa_id, "fecha": _texto("fecha"), "orden": _texto("orden"), "remito": _texto("remito"),
        "observaciones": _texto("observaciones"), "origen": origen, "kilos_texto": kilos_texto,
        "kilos_origen": parse_numero(kilos_texto) if kilos_texto else None,
        "transporte": normalizar_transporte({c: _texto(c) for c in CAMPOS_TRANSPORTE}),
        "destino": rm.leer_renglones(request.form),
        "sale": rm.leer_renglones(request.form, "o_") if origen == "DEPOSITO" else [],
    }
    d["insumos"], errores_insumos, d["textos_insumos"] = ins.leer_formulario(request.form)
    d["envases"], errores_envases, d["textos_envases"] = vacios.leer_formulario(request.form, "TRASVASO")
    d["fotos"], errores_fotos = ft.leer_subidas(request.files.getlist("fotos"))

    errores = []
    if not empresa_id:
        errores.append("Elegí la empresa.")
    if not _fecha_valida(d["fecha"]):
        errores.append("La fecha no es válida.")
    if not d["orden"]:
        errores.append("El número de orden es obligatorio.")
    if kilos_texto and (d["kilos_origen"] is None or d["kilos_origen"] < 0):
        errores.append("Los kilos que llegan no son un número válido.")
    if origen == "DEPOSITO" and not d["sale"]:
        errores.append("Origen: elegí qué producto del depósito se vacía y de qué lote sale.")
    if not d["destino"]:
        errores.append("Destino: cargá cómo queda envasado el producto (al menos un renglón).")
    errores += errores_insumos + errores_envases + errores_fotos

    d["productos"] = productos = rm.productos_de_empresa(empresa_id)
    d["items_salen"], _, errores_salen = rm.revisar_renglones("EGRESO", d["sale"], productos)
    d["items_entran"], d["vencimientos"], errores_entran = rm.revisar_renglones(
        "INGRESO", d["destino"], productos, corrigiendo=ingreso["id"] if ingreso else None)
    errores += _con_prefijo("Origen", errores_salen) + _con_prefijo("Destino", errores_entran)
    if not errores:
        excluir = [egreso["id"]] if egreso else []
        errores += _con_prefijo("Origen", rm.chequear_stock(d["items_salen"], productos, d["fecha"], excluir))
        if t:
            errores += vacios.problemas_al_cambiar(empresa_id, "TRASVASO", d["envases"], trasvaso_id=t["id"])
            if ingreso:
                errores += ins.problemas_al_cambiar(empresa_id, "INGRESO", d["insumos"], ingreso["id"])
        else:
            errores += vacios.chequear(empresa_id, "TRASVASO", d["envases"])

    mostrar_repetido = False
    cambia_orden = not t or d["orden"].lower() != t["orden"].lower()
    if empresa_id and d["orden"] and cambia_orden and not errores:
        repetido = db.execute(
            "SELECT id FROM trasvasos WHERE empresa_id = ? AND orden = ? AND anulado = 0 AND id <> ?",
            (empresa_id, d["orden"], t["id"] if t else 0),
        ).fetchone()
        if repetido and request.form.get("confirmar_repetido") != "1":
            mostrar_repetido = True
            errores.append("Ya hay un trasvaso con ese número de orden para esta empresa. "
                           "Si es correcto, marcá la casilla 'Cargar igual' y guardá de nuevo.")
    return d, errores, mostrar_repetido


def _mostrar_de_nuevo(d, errores, mostrar_repetido, t=None):
    for e in dict.fromkeys(errores):  # sin repetidos
        flash(e, "error")
    if d["fotos"]:
        flash("Las fotos no se guardaron: volvé a elegirlas antes de guardar.", "aviso")
    form = {"empresa_id": d["empresa_id"], "fecha": d["fecha"], "orden": d["orden"], "remito": d["remito"],
            "observaciones": d["observaciones"], "origen": d["origen"], "kilos_origen": _texto("kilos_origen"),
            "crear_pallets": request.form.get("crear_pallets", ""), **d["transporte"]}
    return _form(form, d["destino"], rm.leer_renglones(request.form, "o_"), mostrar_repetido,
                 d["textos_insumos"], d["textos_envases"], editando=t)


@bp.route("/trasvasos/nuevo", methods=["GET", "POST"])
def trasvaso_nuevo():
    if request.method == "GET":
        return _form({"empresa_id": _int_o_none(request.args.get("empresa_id")), "fecha": date.today().isoformat(),
                      "origen": "AFUERA", "crear_pallets": "1"}, [], [])

    db = get_db()
    d, errores, mostrar_repetido = _leer_trasvaso()
    if errores:
        return _mostrar_de_nuevo(d, errores, mostrar_repetido)

    empresa_id, fecha, orden, observaciones = d["empresa_id"], d["fecha"], d["orden"], d["observaciones"]
    cur = db.execute(
        """INSERT INTO trasvasos (empresa_id, fecha, orden, remito, origen, kilos_origen, observaciones)
           VALUES (?,?,?,?,?,?,?)""",
        (empresa_id, fecha, orden, d["remito"] or None, d["origen"], d["kilos_origen"], observaciones or None),
    )
    trasvaso_id = cur.lastrowid
    numero = remito_de_orden(orden)
    remitos = []
    if d["origen"] == "DEPOSITO":
        lineas, plan = rm.lineas_de_egreso(d["items_salen"], d["productos"])
        egreso_id = registrar_movimiento("EGRESO", empresa_id, fecha, numero, observaciones, lineas)
        pl.registrar_salida_de_pallets(egreso_id, plan)
        remitos.append(egreso_id)
    lineas = rm.lineas_de_ingreso(d["items_entran"], d["vencimientos"])
    ingreso_id = registrar_movimiento("INGRESO", empresa_id, fecha, numero, observaciones, lineas, d["transporte"],
                                      d["insumos"])
    remitos.append(ingreso_id)
    db.execute(f"UPDATE movimientos SET trasvaso_id = ? WHERE id IN ({','.join('?' * len(remitos))})",
               [trasvaso_id, *remitos])
    vacios.registrar(empresa_id, fecha, "TRASVASO", d["envases"], trasvaso_id=trasvaso_id)
    creados, avisos = [], []
    if request.form.get("crear_pallets") == "1":
        creados, avisos = pl.crear_pallets_de_ingreso(ingreso_id)
    cantidad_fotos = ft.guardar(ingreso_id, d["fotos"])
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
        cambios=cr.cambios(trasvaso_id=trasvaso_id),
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
    if "INGRESO" in remitos:
        # Lo que salió del depósito vuelve al stock a la vez que se saca lo que entró envasado.
        problema = rm.problema_al_anular_ingreso(remitos["INGRESO"], [m["id"] for m in remitos.values()])
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


# --------------------------------------------------------- corregir o borrar


def _foto_trasvaso(t):
    """Todo lo de un trasvaso: sus datos, sus dos remitos y sus envases (para comparar o anotar)."""
    t = dict(get_db().execute(
        "SELECT t.*, e.nombre AS empresa FROM trasvasos t JOIN empresas e ON e.id = t.empresa_id WHERE t.id = ?",
        (t["id"],)).fetchone())
    remitos = _remitos(t["id"])
    return {"t": t, **{tipo: cr.foto(m["id"]) for tipo, m in remitos.items()},
            "envases": vacios.de_trasvaso(t["id"])}


def _texto_envases_trasvaso(envases):
    partes = []
    for e in envases:
        if e["vacios"]:
            partes.append(f"{formato_numero(e['vacios'])} {e['nombre'].lower()} {vacios.palabras(e['nombre'], 'VACIO')[1]} usados")
        if e["sucios"]:
            partes.append(f"{formato_numero(e['sucios'])} {e['nombre'].lower()} {vacios.palabras(e['nombre'], 'SUCIO')[1]} que quedan")
    return ", ".join(partes)


def _texto_trasvaso(f):
    t = f["t"]
    lineas = [f"Trasvaso del {formato_fecha(t['fecha'])}, orden {t['orden']}, de {t['empresa']}. "
              f"Origen: {ORIGENES[t['origen']].lower()}."]
    for tipo, titulo in (("EGRESO", "Salió del depósito"), ("INGRESO", "Quedó envasado")):
        for i in f.get(tipo, {}).get("items", []):
            lineas.append(f"{titulo}: {i['codigo']} - {i['descripcion']} "
                          f"({('lote ' + i['lote']) if i['lote'] else 'sin lote'}): {formato_numero(i['cantidad'])} "
                          f"{i['unidad'].lower()}.")
    if t["kilos_origen"]:
        lineas.append(f"Kilos que llegaron: {formato_numero(t['kilos_origen'])}.")
    envases = _texto_envases_trasvaso(f["envases"])
    if envases:
        lineas.append(f"Envases: {envases}.")
    for i in f.get("INGRESO", {}).get("insumos", []):
        lineas.append(f"{i['nombre']}: {formato_numero(i['cantidad'])} {i['unidad']}.")
    if t["observaciones"]:
        lineas.append(f"Observación: {t['observaciones']}")
    if t["anulado"]:
        lineas.append(f"Estaba anulado ({t['motivo_anulacion'] or 'sin motivo'}).")
    return "\n".join(lineas)


def _diferencias_trasvaso(a, b):
    lineas = []
    for campo, nombre in (("fecha", "Fecha"), ("orden", "N° de orden"), ("remito", "N° de remito"),
                          ("origen", "Origen"), ("kilos_origen", "Kilos que llegaron"),
                          ("observaciones", "Observación")):
        va, vb = a["t"].get(campo), b["t"].get(campo)
        if (va or "") != (vb or ""):
            if campo == "fecha":
                va, vb = formato_fecha(va), formato_fecha(vb)
            elif campo == "origen":
                va, vb = ORIGENES.get(va, va), ORIGENES.get(vb, vb)
            elif campo == "kilos_origen":
                va, vb = formato_numero(va), formato_numero(vb)
            lineas.append(f"{nombre}: {va or '(vacío)'} → {vb or '(vacío)'}")
    for tipo, titulo in (("EGRESO", "Lo que sale"), ("INGRESO", "Lo envasado")):
        vacio = {"m": {}, "items": [], "insumos": [], "envases": [], "fotos": 0}
        x, y = a.get(tipo) or vacio, b.get(tipo) or vacio
        # Los datos del remito (fecha, número, observación) ya están en los del trasvaso.
        quitar = ("Fecha:", "N° de remito:", "Observación:")
        lineas += [f"{titulo}: {l}" if not l.startswith(("Chofer", "Empresa de transporte", "Patente", "Contenedor",
                                                          "Tipo de contenedor", "Se agregó 1 foto", "Se agregaron"))
                   else l
                   for l in cr.diferencias({**x, "m": x["m"] or y["m"]}, {**y, "m": y["m"] or x["m"]})
                   if not l.startswith(quitar)]
    va, vb = _texto_envases_trasvaso(a["envases"]), _texto_envases_trasvaso(b["envases"])
    if va != vb:
        lineas.append(f"Envases: {va or '(ninguno)'} → {vb or '(ninguno)'}")
    return lineas


def _datos_para_editar(t):
    db = get_db()
    remitos = _remitos(t["id"])
    ingreso, egreso = remitos.get("INGRESO"), remitos.get("EGRESO")
    form = {"empresa_id": t["empresa_id"], "fecha": t["fecha"], "orden": t["orden"], "remito": t["remito"] or "",
            "observaciones": t["observaciones"] or "", "origen": t["origen"],
            "kilos_origen": formato_numero(t["kilos_origen"], 6) if t["kilos_origen"] is not None else "",
            **{c: (ingreso[c] if ingreso else "") or "" for c in CAMPOS_TRANSPORTE}}

    def renglones(m):
        if not m:
            return []
        return [{"producto_id": r["producto_id"], "lote_id": r["lote_id"], "lote": r["lote"],
                 "vencimiento": r["vencimiento"] or "", "cantidad": formato_numero(r["cantidad"], 6)}
                for r in db.execute(
                    """SELECT i.producto_id, i.lote_id, i.cantidad, l.lote, l.vencimiento FROM movimiento_items i
                       JOIN lotes l ON l.id = i.lote_id WHERE i.movimiento_id = ? ORDER BY i.id""", (m["id"],))]

    textos_insumos = defaultdict(float)
    for r in (ins.de_movimiento(ingreso["id"]) if ingreso else []):
        textos_insumos[f"ins_{r['insumo_id']}_{r['propietario']}" if r["propietario"] else f"ins_{r['insumo_id']}"] \
            += r["cantidad"]
    textos_envases = {
        vacios.campo(r["envase_id"], r["estado"]): formato_numero(abs(r["cantidad"]), 6)
        for r in db.execute(
            """SELECT envase_id, estado, SUM(cantidad) AS cantidad FROM envase_movs
               WHERE trasvaso_id = ? AND anulado = 0 GROUP BY envase_id, estado""", (t["id"],))
        if abs(r["cantidad"]) > 1e-9
    }
    return (form, renglones(ingreso), renglones(egreso), {k: formato_numero(v, 6) for k, v in textos_insumos.items()},
            textos_envases)


@bp.route("/trasvasos/<int:trasvaso_id>/editar", methods=["GET", "POST"])
def trasvaso_editar(trasvaso_id):
    t = _trasvaso_o_404(trasvaso_id)
    volver = redirect(url_for("trasvasos.trasvaso_detalle", trasvaso_id=trasvaso_id))
    remitos = _remitos(trasvaso_id)
    ingreso, egreso = remitos.get("INGRESO"), remitos.get("EGRESO")
    if t["anulado"]:
        flash("Este trasvaso está anulado: no se puede editar. Si se cargó por error, podés borrarlo.", "error")
        return volver
    if ingreso is None:
        flash("Este trasvaso no tiene el remito de lo envasado: no se puede editar. Borralo y cargalo de nuevo.", "error")
        return volver
    if request.method == "GET":
        form, destino, origen, textos_insumos, textos_envases = _datos_para_editar(t)
        return _form(form, destino, origen, False, textos_insumos, textos_envases, editando=t)

    d, errores, mostrar_repetido = _leer_trasvaso(t)
    if errores:
        return _mostrar_de_nuevo(d, errores, mostrar_repetido, t)
    db = get_db()
    empresa_id, fecha, observaciones = t["empresa_id"], d["fecha"], d["observaciones"]
    antes = _foto_trasvaso(t)
    lotes_viejos = cr.lotes_de([m["id"] for m in remitos.values()])
    existentes = {l["id"] for l in (buscar_lote(pid, lote) for (pid, _), (lote, _) in d["vencimientos"].items()) if l}
    estado = cr.Estado(lotes_viejos | existentes | {lid for _, lid in d["items_salen"]})
    numero = remito_de_orden(d["orden"])
    creados, borrados = [], []
    try:
        db.execute(
            """UPDATE trasvasos SET fecha = ?, orden = ?, remito = ?, origen = ?, kilos_origen = ?, observaciones = ?
               WHERE id = ?""",
            (fecha, d["orden"], d["remito"] or None, d["origen"], d["kilos_origen"], observaciones or None, trasvaso_id),
        )
        # Lo que sale del depósito (el egreso del trasvaso).
        if d["origen"] == "DEPOSITO":
            lineas_salen = [(pid, lid, cant) for (pid, lid), cant in d["items_salen"].items()]
            if egreso:
                db.execute("UPDATE movimientos SET fecha = ?, remito = ?, observaciones = ? WHERE id = ?",
                           (fecha, numero, observaciones, egreso["id"]))
                errores += cr.aplicar_items(egreso, lineas_salen)[0]
            else:
                lineas, plan = rm.lineas_de_egreso(d["items_salen"], d["productos"])
                egreso_id = registrar_movimiento("EGRESO", empresa_id, fecha, numero, observaciones, lineas)
                pl.registrar_salida_de_pallets(egreso_id, plan)
                db.execute("UPDATE movimientos SET trasvaso_id = ? WHERE id = ?", (trasvaso_id, egreso_id))
        elif egreso:
            cr.borrar_movimiento(egreso)  # ahora llega de afuera: lo que salía del depósito vuelve
        # Lo que queda envasado (el ingreso del trasvaso).
        tr = {c: d["transporte"].get(c) or None for c in CAMPOS_TRANSPORTE}
        db.execute(
            f"""UPDATE movimientos SET fecha = ?, remito = ?, observaciones = ?,
                       {", ".join(c + " = ?" for c in CAMPOS_TRANSPORTE)} WHERE id = ?""",
            (fecha, numero, observaciones, *(tr[c] for c in CAMPOS_TRANSPORTE), ingreso["id"]),
        )
        lineas_entran = rm.lineas_de_ingreso(d["items_entran"], d["vencimientos"], corrigiendo=ingreso["id"])
        errores_entran, creados, borrados = cr.aplicar_items(ingreso, lineas_entran)
        errores += errores_entran
        activos = [i["id"] for i in ins.lista()]
        if activos:
            db.execute(f"DELETE FROM movimiento_insumos WHERE movimiento_id = ? AND insumo_id IN ({','.join('?' * len(activos))})",
                       [ingreso["id"], *activos])
        db.executemany(
            "INSERT INTO movimiento_insumos (movimiento_id, insumo_id, propietario, cantidad) VALUES (?,?,?,?)",
            [(ingreso["id"], iid, propietario, cant) for iid, propietario, cant in d["insumos"]],
        )
        tipos_envase = [e["id"] for e in vacios.tipos_para_cargar()]
        if tipos_envase:
            db.execute(f"DELETE FROM envase_movs WHERE trasvaso_id = ? AND envase_id IN ({','.join('?' * len(tipos_envase))})",
                       [trasvaso_id, *tipos_envase])
        db.execute("UPDATE envase_movs SET fecha = ? WHERE trasvaso_id = ?", (fecha, trasvaso_id))
        vacios.registrar(empresa_id, fecha, "TRASVASO", d["envases"], trasvaso_id=trasvaso_id)
        if not errores:
            errores = estado.problemas({lid for _, lid, _ in lineas_entran}, fecha=fecha)
        if errores:
            db.rollback()
            return _mostrar_de_nuevo(d, errores, mostrar_repetido, t)
        cr.limpiar_lotes(lotes_viejos)
        cantidad_fotos = ft.guardar(ingreso["id"], d["fotos"])
        despues = _foto_trasvaso(t)
        cambios = _diferencias_trasvaso(antes, despues)
        if cantidad_fotos:
            cambios.append("Se agregó 1 foto" if cantidad_fotos == 1 else f"Se agregaron {cantidad_fotos} fotos")
        if creados:
            cambios.append(("Se armó el pallet " if len(creados) == 1 else "Se armaron los pallets ")
                           + ", ".join(pl.codigo_pallet(p) for p in creados))
        if borrados:
            cambios.append(("Se borró el pallet " if len(borrados) == 1 else "Se borraron los pallets ")
                           + ", ".join(pl.codigo_pallet(p) for p in borrados) + " (ya no hacía falta)")
        if cambios:
            cr.anotar("EDITADO", "\n".join(cambios), trasvaso_id=trasvaso_id, empresa_id=empresa_id,
                      tipo="TRASVASO", fecha=fecha, remito=numero)
        db.commit()
    except Exception:
        db.rollback()
        raise
    flash("Trasvaso corregido. Se recalcularon el stock y los envases." if cambios else "No había nada para cambiar.",
          "ok")
    if creados:
        flash("Hay pallets nuevos: imprimí sus etiquetas con el botón de abajo.", "aviso")
    if borrados:
        flash(f"Se borraron los pallets que ya no hacían falta ({', '.join(pl.codigo_pallet(p) for p in borrados)}): "
              "si tenían la etiqueta pegada, sacala.", "aviso")
    return volver


@bp.route("/trasvasos/<int:trasvaso_id>/borrar", methods=["GET", "POST"])
def trasvaso_borrar(trasvaso_id):
    t = _trasvaso_o_404(trasvaso_id)
    remitos = _remitos(trasvaso_id)
    problema = None
    if not t["anulado"]:
        activos = {tipo: m for tipo, m in remitos.items() if not m["anulado"]}
        if "INGRESO" in activos:
            # Lo que salió del depósito vuelve al stock a la vez que se saca lo que entró envasado.
            problema = cr.problema_al_borrar(activos["INGRESO"], [m["id"] for m in activos.values()])
        if not problema:
            problemas = vacios.problemas_al_anular(trasvaso_id=trasvaso_id)
            if problemas:
                problema = ("No se puede borrar: " + "; ".join(problemas)
                            + ". Primero corregí los envases en Trasvasos → Envases vacíos y sucios.")
    if request.method == "GET" or problema:
        if problema and request.method == "POST":
            flash(problema, "error")
        ingreso = remitos.get("INGRESO")
        return render_template(
            "trasvaso_borrar.html", t=t, problema=problema, texto=_texto_trasvaso(_foto_trasvaso(t)).split("\n"),
            armados=pl.pallets(ids=[r[0] for r in get_db().execute(
                "SELECT id FROM pallets WHERE movimiento_id = ?", (ingreso["id"],))], con_contenido=False)
            if ingreso else [],
            fotos=ft.de_movimiento(ingreso["id"]) if ingreso else [],
        )
    db = get_db()
    archivos = []
    try:
        cr.anotar("BORRADO", _texto_trasvaso(_foto_trasvaso(t)), trasvaso_id=trasvaso_id, empresa_id=t["empresa_id"],
                  tipo="TRASVASO", fecha=t["fecha"], remito=remito_de_orden(t["orden"]))
        for m in sorted(remitos.values(), key=lambda m: m["tipo"]):  # primero el egreso
            archivos += cr.borrar_movimiento(m)
        db.execute("DELETE FROM envase_movs WHERE trasvaso_id = ?", (trasvaso_id,))
        db.execute("DELETE FROM trasvasos WHERE id = ?", (trasvaso_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    cr.archivar_fotos(archivos)
    flash(f"Trasvaso borrado (orden {t['orden']} del {formato_fecha(t['fecha'])}). Se recalcularon el stock y los "
          "envases. Queda anotado en Movimientos → Cambios y borrados.", "ok")
    return redirect(url_for("trasvasos.trasvasos", empresa_id=t["empresa_id"]))


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
    saldos = vacios.saldos_de(empresa_id, _int_o_none(request.args.get("excluir")),
                              _int_o_none(request.args.get("excluir_trasvaso"))) if empresa_id else {}
    return jsonify({str(eid): s for eid, s in saldos.items()})
