"""Pantallas del depósito: ubicaciones, pallets con etiqueta QR y la pantalla para la terminal (celular)."""
from collections import defaultdict
from datetime import date

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from . import insumos as ins
from . import pallets as pl
from .db import get_db, stock_de_lotes, stock_por_lote_id
from .formato import cantidad_pallets
from .servicios import CAMPOS_TRANSPORTE, normalizar_transporte, parse_numero, registrar_movimiento

bp = Blueprint("deposito", __name__)

GALPON_INICIAL = "16"


def _texto(nombre, origen=None):
    return ((origen or request.form).get(nombre) or "").strip()


def _int(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _ids(texto=None):
    """Ids que vienen como '1,2,3' o repetidos (?ids=1&ids=2)."""
    if texto is None:
        texto = ",".join(request.args.getlist("ids"))
    return [int(x) for x in (texto or "").replace(" ", ",").split(",") if x.strip().isdigit()]


def _empresas():
    return get_db().execute("SELECT * FROM empresas ORDER BY nombre").fetchall()


@bp.app_context_processor
def _globales():
    return {"corto_ubicacion": pl.corto_ubicacion, "nombre_ubicacion": pl.nombre_ubicacion}


# ---------------------------------------------------------------- depósito


@bp.route("/deposito")
def deposito():
    galpones = pl.galpones()
    galpon = request.args.get("galpon") or (galpones[0] if galpones else GALPON_INICIAL)
    por_ubicacion, sin_ubicar = pl.ocupacion()
    calles = defaultdict(list)
    for u in pl.ubicaciones(galpon=galpon):
        calles[u["calle"]].append(u)
    return render_template(
        "deposito.html", galpones=galpones, galpon=galpon, calles=dict(sorted(calles.items())),
        ocupacion=por_ubicacion, sin_ubicar=sin_ubicar,
    )


@bp.route("/deposito/estibas", methods=["POST"])
def crear_estibas():
    galpon = _int(request.form.get("galpon"))
    calle = _int(request.form.get("calle"))
    desde = _int(request.form.get("desde")) or 1
    hasta = _int(request.form.get("hasta"))
    errores = []
    if not galpon or galpon <= 0:
        errores.append("Indicá el número de galpón.")
    if not calle or calle <= 0:
        errores.append("Indicá el número de calle.")
    if not hasta or hasta < desde or desde <= 0:
        errores.append("Indicá hasta qué número de estiba crear (mayor o igual que 'desde').")
    elif hasta - desde + 1 > pl.MAX_ESTIBAS_POR_VEZ:
        errores.append(f"Como máximo {pl.MAX_ESTIBAS_POR_VEZ} estibas por vez.")
    if errores:
        for e in errores:
            flash(e, "error")
        return redirect(url_for("deposito.deposito", galpon=galpon or None))
    creadas = pl.crear_estibas(str(galpon), calle, desde, hasta)
    get_db().commit()
    if creadas:
        flash(f"Se crearon {creadas} estibas en la calle {calle} del galpón {galpon}.", "ok")
    else:
        flash("Esas estibas ya existían.", "ok")
    return redirect(url_for("deposito.deposito", galpon=galpon))


def _ubicacion_o_404(ubicacion_id):
    u = get_db().execute("SELECT * FROM ubicaciones WHERE id = ?", (ubicacion_id,)).fetchone()
    if u is None:
        abort(404)
    return u


@bp.route("/deposito/ubicacion/<int:ubicacion_id>")
def ubicacion(ubicacion_id):
    u = _ubicacion_o_404(ubicacion_id)
    return render_template("ubicacion.html", u=u, pallets=pl.pallets(ubicacion_id=ubicacion_id))


@bp.route("/deposito/ubicacion/<int:ubicacion_id>/activar", methods=["POST"])
def ubicacion_activar(ubicacion_id):
    u = _ubicacion_o_404(ubicacion_id)
    if u["activa"] and pl.pallets(ubicacion_id=ubicacion_id):
        flash("La estiba tiene pallets: primero ubicalos en otro lugar.", "error")
    else:
        get_db().execute("UPDATE ubicaciones SET activa = 1 - activa WHERE id = ?", (ubicacion_id,))
        get_db().commit()
        flash("Estiba " + ("desactivada." if u["activa"] else "activada."), "ok")
    return redirect(url_for("deposito.ubicacion", ubicacion_id=ubicacion_id))


@bp.route("/deposito/etiquetas")
def etiquetas_ubicaciones():
    ids = _ids()
    if ids:
        filas = pl.ubicaciones(ids=ids)
    else:
        filas = pl.ubicaciones(galpon=request.args.get("galpon") or None, solo_activas=True)
        calle = _int(request.args.get("calle"))
        if calle:
            filas = [u for u in filas if u["calle"] == calle]
    por_hoja = 4 if request.args.get("por_hoja") == "4" else 2
    hojas = [filas[i:i + por_hoja] for i in range(0, len(filas), por_hoja)]
    parametros = {k: v for k, v in request.args.items() if k != "por_hoja"}
    if ids:
        parametros["ids"] = ",".join(map(str, ids))
    return render_template("etiquetas_ubicaciones.html", hojas=hojas, por_hoja=por_hoja, total=len(filas),
                           parametros=parametros)


# ------------------------------------------------------------------ pallets


def _filtros_pallets():
    return {
        "empresa_id": _int(request.args.get("empresa_id")),
        "buscar": (request.args.get("buscar") or "").strip(),
        "sin_ubicar": request.args.get("sin_ubicar") == "1",
        "ver_vacios": request.args.get("ver_vacios") == "1",
    }


def _pallets_filtrados(f):
    return pl.pallets(empresa_id=f["empresa_id"], buscar=f["buscar"] or None, sin_ubicar=f["sin_ubicar"],
                      con_contenido=not f["ver_vacios"])


@bp.route("/pallets")
def pallets_lista():
    f = _filtros_pallets()
    return render_template("pallets.html", pallets=_pallets_filtrados(f), filtros=f, empresas=_empresas())


@bp.route("/pallets/excel")
def pallets_excel():
    from .rutas import _excel

    filas = []
    for p in _pallets_filtrados(_filtros_pallets()):
        for l in p["lineas"] or [None]:
            filas.append((
                p["codigo"], p["empresa"], p["ubicacion_codigo"] or "",
                l["codigo"] if l else "", l["descripcion"] if l else "",
                (l["lote"] or "(sin lote)") if l else "", (l["vencimiento"] or "") if l else "",
                l["cantidad"] if l else 0, l["unidad"] if l else "",
                cantidad_pallets(l["cantidad"], l["unidades_por_pallet"]) if l else None,
                p["remito"] or "", p["creado_en"][:10],
            ))
    return _excel(
        "pallets", "Pallets",
        ["Pallet", "Empresa", "Ubicación", "Código", "Descripción", "Lote", "Vencimiento", "Cantidad", "Envase",
         "Pallets", "Remito de ingreso", "Creado"],
        filas,
    )


def _pallet_o_404(pallet_id):
    p = pl.pallet(pallet_id)
    if p is None:
        abort(404)
    return p


@bp.route("/pallets/<int:pallet_id>")
def pallet_ficha(pallet_id):
    p = _pallet_o_404(pallet_id)
    movs, lugares = pl.historial_pallet(pallet_id)
    lotes = stock_de_lotes(empresa_id=p["empresa_id"])
    sin_et = pl.sin_etiqueta_por_lote([l["id"] for l in lotes]) if lotes else {}
    para_sumar = [dict(l, sin_etiqueta=sin_et[l["id"]]) for l in lotes if sin_et.get(l["id"], 0) > pl.EPS]
    return render_template(
        "pallet.html", p=p, movs=movs, lugares=lugares, para_sumar=para_sumar,
        ubicaciones=pl.ubicaciones(solo_activas=True),
    )


@bp.route("/pallets/<int:pallet_id>/<accion>", methods=["POST"])
def pallet_accion(pallet_id, accion):
    p = _pallet_o_404(pallet_id)
    db = get_db()
    error = None
    if accion == "ubicar":
        ubicacion_id = _int(request.form.get("ubicacion_id"))
        codigo = _texto("codigo")
        if codigo:
            tipo, fila = pl.leer_codigo(codigo)
            if tipo != "ubicacion":
                error = fila if tipo is None else "Ese código es de un pallet, no de una ubicación."
            else:
                ubicacion_id = fila["id"]
        if not error:
            pl.ubicar(pallet_id, ubicacion_id or None)
            mensaje = "Ubicación guardada." if ubicacion_id else "El pallet quedó sin ubicación."
    elif accion == "juntar":
        tipo, fila = pl.leer_codigo(_texto("codigo"))
        if tipo != "pallet":
            error = fila if tipo is None else "Escribí o escaneá el código de otro pallet (P…)."
        else:
            error = pl.juntar_pallets(pallet_id, fila["id"])
            mensaje = f"Se pasó todo lo del pallet {fila['codigo']} a este."
    elif accion == "sumar":
        error = pl.sumar_a_pallet(pallet_id, _int(request.form.get("lote_id")), parse_numero(_texto("cantidad")))
        mensaje = "Mercadería agregada al pallet."
    elif accion == "sacar":
        error = pl.sacar_de_pallet(pallet_id, _int(request.form.get("lote_id")), parse_numero(_texto("cantidad")))
        mensaje = "Mercadería sacada del pallet (sigue en el depósito, sin etiqueta)."
    else:
        abort(404)
    if error:
        db.rollback()
        flash(error, "error")
    else:
        db.commit()
        flash(mensaje, "ok")
    return redirect(url_for("deposito.pallet_ficha", pallet_id=p["id"]))


@bp.route("/pallets/armar", methods=["GET", "POST"])
def pallets_armar():
    db = get_db()
    if request.method == "POST":
        lote_ids = [_int(x) for x in request.form.getlist("lote_id")]
        por_pallet = [parse_numero(x) for x in request.form.getlist("por_pallet")]
        solo = _int(request.form.get("solo"))
        pedidos = [(lid, por) for lid, por in zip(lote_ids, por_pallet) if (lid == solo if solo else por)]
        creados, errores = [], []
        if not pedidos:
            errores.append("Completá cuántas unidades van por pallet en los renglones que quieras armar.")
        for lote_id, por in pedidos:
            ids, error = pl.armar_pallets(lote_id, por)
            if error:
                errores.append(error)
            creados += ids
        if errores:
            db.rollback()
            for e in errores:
                flash(e, "error")
            return redirect(url_for("deposito.pallets_armar", empresa_id=request.form.get("empresa_id") or None))
        db.commit()
        n = len(creados)
        flash("Se armó 1 pallet." if n == 1 else f"Se armaron {n} pallets.", "ok")
        return redirect(url_for("deposito.pallets_armar", empresa_id=request.form.get("empresa_id") or None,
                                nuevos=",".join(map(str, creados))))
    empresa_id = _int(request.args.get("empresa_id"))
    buscar = (request.args.get("buscar") or "").strip()
    lotes = stock_de_lotes(empresa_id=empresa_id, buscar=buscar or None)
    sin_et = pl.sin_etiqueta_por_lote([l["id"] for l in lotes]) if lotes else {}
    filas = [dict(l, sin_etiqueta=sin_et[l["id"]]) for l in lotes if sin_et.get(l["id"], 0) > pl.EPS]
    return render_template(
        "pallets_armar.html", filas=filas, empresas=_empresas(), empresa_id=empresa_id, buscar=buscar,
        nuevos=request.args.get("nuevos", ""), cuantos=pl.cuantos_pallets,
    )


@bp.route("/pallets/etiquetas")
def etiquetas_pallets():
    ids = _ids()
    movimiento_id = _int(request.args.get("movimiento_id"))
    parametros = {"movimiento_id": movimiento_id} if movimiento_id else {"ids": ",".join(map(str, ids))}
    if movimiento_id:
        ids = [r[0] for r in get_db().execute(
            "SELECT id FROM pallets WHERE movimiento_id = ? ORDER BY id", (movimiento_id,))]
    lista = sorted(pl.pallets(ids=ids, con_contenido=False), key=lambda p: p["id"])
    # "Pallet 2 de 5": posición entre los pallets armados por el mismo remito.
    del_remito = defaultdict(list)
    movs = {p["movimiento_id"] for p in lista if p["movimiento_id"]}
    for mid in movs:
        del_remito[mid] = [r[0] for r in get_db().execute(
            "SELECT id FROM pallets WHERE movimiento_id = ? ORDER BY id", (mid,))]
    for p in lista:
        hermanos = del_remito.get(p["movimiento_id"]) or []
        p["numero"] = (hermanos.index(p["id"]) + 1, len(hermanos)) if p["id"] in hermanos else None
    por_hoja = {"1": 1, "2": 2}.get(request.args.get("por_hoja"), 4)
    hojas = [lista[i:i + por_hoja] for i in range(0, len(lista), por_hoja)]
    return render_template("etiquetas_pallets.html", hojas=hojas, por_hoja=por_hoja, total=len(lista),
                           parametros=parametros)


# ----------------------------------------------------------------- terminal


@bp.route("/terminal")
def terminal():
    return render_template("terminal_menu.html")


@bp.route("/terminal/consultar")
def terminal_consultar():
    codigo = (request.args.get("codigo") or "").strip()
    resultado, tipo, error = None, None, None
    if codigo:
        tipo, fila = pl.leer_codigo(codigo)
        if tipo == "pallet":
            resultado = pl.pallet(fila["id"])
        elif tipo == "ubicacion":
            resultado = {"u": fila, "pallets": pl.pallets(ubicacion_id=fila["id"])}
        else:
            error = fila
    return render_template("terminal_consultar.html", tipo=tipo, r=resultado, error=error)


@bp.route("/terminal/ubicar", methods=["GET", "POST"])
def terminal_ubicar():
    db = get_db()
    pallet_id = _int(request.values.get("pallet_id") or request.args.get("pallet"))
    ubicacion_id = _int(request.values.get("ubicacion_id"))
    accion = request.form.get("accion", "escanear")
    if request.method == "POST":
        if accion == "soltar":
            ubicacion_id = None
        elif accion == "cancelar":
            pallet_id = None
        elif (request.form.get("codigo") or "").strip():  # Enter sin código: no hace nada
            tipo, fila = pl.leer_codigo(request.form.get("codigo"))
            if tipo is None:
                flash(fila, "error")
            elif tipo == "pallet":
                if not pl.contenido(pallet_ids=[fila["id"]]):
                    flash(f"El pallet {fila['codigo']} está vacío (ya salió del depósito).", "error")
                elif ubicacion_id:
                    u = _ubicacion_o_404(ubicacion_id)
                    pl.ubicar(fila["id"], ubicacion_id)
                    db.commit()
                    flash(f"{fila['codigo']} quedó en {pl.corto_ubicacion(u)}.", "ok")
                    pallet_id = None
                else:
                    pallet_id = fila["id"]
            elif not fila["activa"]:
                flash(f"La estiba {pl.corto_ubicacion(fila)} está desactivada.", "error")
            elif pallet_id:
                pa = db.execute("SELECT codigo FROM pallets WHERE id = ?", (pallet_id,)).fetchone()
                pl.ubicar(pallet_id, fila["id"])
                db.commit()
                flash(f"{pa['codigo']} quedó en {pl.corto_ubicacion(fila)}.", "ok")
                pallet_id = None
            else:
                ubicacion_id = fila["id"]
    pendiente = pl.pallet(pallet_id) if pallet_id else None
    fija = db.execute("SELECT * FROM ubicaciones WHERE id = ?", (ubicacion_id,)).fetchone() if ubicacion_id else None
    return render_template("terminal_ubicar.html", pendiente=pendiente, fija=fija, ultimos=pl.ultimos_ubicados())


def _lineas_despacho():
    """Los pallets ya escaneados, tal como vienen en el formulario."""
    columnas = [request.form.getlist(c) for c in ("l_pallet", "l_producto", "l_lote", "l_cant")]
    lineas = []
    for pallet_id, producto_id, lote_id, cant in zip(*columnas):
        lineas.append({"pallet_id": _int(pallet_id), "producto_id": _int(producto_id), "lote_id": _int(lote_id),
                       "cant_texto": cant.strip(), "cantidad": parse_numero(cant)})
    return lineas


def _completar_lineas(lineas):
    """Agrega a cada línea los datos del pallet y lo que tiene hoy, para mostrarlas."""
    contenido = {(r["pallet_id"], r["lote_id"]): r for r in pl.contenido(pallet_ids={l["pallet_id"] for l in lineas})}
    info = {p["id"]: p for p in pl.pallets(ids={l["pallet_id"] for l in lineas}, con_contenido=False)}
    for l in lineas:
        l["actual"] = contenido.get((l["pallet_id"], l["lote_id"]))
        l["pallet"] = info.get(l["pallet_id"])
    return lineas


@bp.route("/terminal/despachar", methods=["GET", "POST"])
def terminal_despachar():
    from .rutas import _transportes_previos

    db = get_db()
    form = {c: _texto(c) for c in ("remito", "fecha", "observaciones", *CAMPOS_TRANSPORTE)}
    form["fecha"] = form["fecha"] or date.today().isoformat()
    lineas = _lineas_despacho() if request.method == "POST" else []
    accion = request.form.get("accion", "escanear")

    if request.method == "GET" and request.args.get("pallet"):
        tipo, fila = "pallet", db.execute("SELECT * FROM pallets WHERE id = ?",
                                          (_int(request.args.get("pallet")),)).fetchone()
        if fila is not None:
            lineas = [{"pallet_id": fila["id"], "producto_id": r["producto_id"], "lote_id": r["lote_id"],
                       "cantidad": r["cantidad"], "cant_texto": ""} for r in pl.contenido(pallet_ids=[fila["id"]])]

    if accion.startswith("quitar:"):
        quitar = _int(accion.split(":", 1)[1])
        lineas = [l for l in lineas if l["pallet_id"] != quitar]
    elif accion == "escanear" and (request.form.get("codigo") or "").strip():
        # Sin código (por ejemplo, Enter en una cantidad) solo se vuelve a mostrar la lista.
        tipo, fila = pl.leer_codigo(request.form.get("codigo"))
        if tipo is None:
            flash(fila, "error")
        elif tipo == "ubicacion":
            flash("Eso es la etiqueta de una estiba. Escaneá la etiqueta del pallet.", "error")
        elif any(l["pallet_id"] == fila["id"] for l in lineas):
            flash(f"El pallet {fila['codigo']} ya está en la lista.", "error")
        else:
            empresas = {p["empresa_id"] for p in pl.pallets(ids={l["pallet_id"] for l in lineas}, con_contenido=False)}
            contenido = pl.contenido(pallet_ids=[fila["id"]])
            if not contenido:
                flash(f"El pallet {fila['codigo']} está vacío (ya salió del depósito).", "error")
            elif empresas and fila["empresa_id"] not in empresas:
                flash(f"El pallet {fila['codigo']} es de otra empresa: cada egreso es de una sola empresa.", "error")
            else:
                for r in contenido:
                    lineas.append({"pallet_id": fila["id"], "producto_id": r["producto_id"], "lote_id": r["lote_id"],
                                   "cantidad": r["cantidad"], "cant_texto": ""})
    elif accion == "confirmar":
        resultado = _confirmar_despacho(form, lineas)
        if resultado:
            return redirect(url_for("deposito.terminal_despacho_listo", movimiento_id=resultado))

    lineas = _completar_lineas(lineas)
    empresa_id = next((l["pallet"]["empresa_id"] for l in lineas if l.get("pallet")), None)
    return render_template(
        "terminal_despachar.html", lineas=lineas, form=form, previos=_transportes_previos(),
        insumos=ins.lista(), campos_insumo=ins.campos,
        textos_insumos={k: v for k, v in request.form.items() if k.startswith("ins_")},
        saldos_cliente={iid: saldo for (_, iid), saldo in ins.saldos_cliente(empresa_id).items()} if empresa_id else {},
    )


def _confirmar_despacho(form, lineas):
    """Guarda el egreso con los pallets escaneados. Devuelve el id del remito o None (con mensajes)."""
    db = get_db()
    errores = []
    if not form["remito"]:
        errores.append("Escribí el número de remito.")
    try:
        date.fromisoformat(form["fecha"])
    except ValueError:
        errores.append("La fecha no es válida.")
    if not lineas:
        errores.append("Escaneá al menos un pallet.")
    _completar_lineas(lineas)
    plan, items, empresas = defaultdict(list), defaultdict(float), set()
    for l in lineas:
        actual, pa = l["actual"], l["pallet"]
        if pa is None or actual is None:
            errores.append("Un pallet de la lista ya no tiene esa mercadería. Quitalo y escanealo de nuevo.")
            continue
        if l["cantidad"] is None or l["cantidad"] < 0:
            errores.append(f"{pa['codigo']}: la cantidad de {actual['codigo']} no es válida.")
            continue
        if l["cantidad"] > actual["cantidad"] + pl.EPS:
            errores.append(f"{pa['codigo']}: tiene {actual['cantidad']:g} de {actual['codigo']}, "
                           f"no se pueden sacar {l['cantidad']:g}.")
            continue
        if l["cantidad"] <= pl.EPS:
            continue  # cero: ese lote del pallet no sale
        empresas.add(pa["empresa_id"])
        plan[(l["producto_id"], l["lote_id"])].append((l["pallet_id"], l["cantidad"]))
        items[(l["producto_id"], l["lote_id"])] += l["cantidad"]
    if len(empresas) > 1:
        errores.append("Hay pallets de más de una empresa.")
    if lineas and not items and not errores:
        errores.append("Todas las cantidades están en cero.")
    insumos, errores_insumos, _ = ins.leer_formulario(request.form)
    errores += errores_insumos
    if len(empresas) == 1 and insumos:
        errores += ins.chequear_egreso(next(iter(empresas)), insumos)
    stock = stock_por_lote_id({lid for _, lid in items})
    for (pid, lid), cant in items.items():
        if cant > stock.get(lid, 0) + pl.EPS:
            errores.append("No hay stock suficiente de uno de los lotes. Revisá los egresos cargados en la oficina.")
    if errores:
        for e in dict.fromkeys(errores):
            flash(e, "error")
        return None
    transporte = normalizar_transporte({c: form[c] for c in CAMPOS_TRANSPORTE})
    mid = registrar_movimiento(
        "EGRESO", empresas.pop(), form["fecha"], form["remito"], form["observaciones"] or None,
        [(pid, lid, round(cant, 6)) for (pid, lid), cant in items.items()], transporte, insumos,
    )
    pl.registrar_salida_de_pallets(mid, plan)
    db.commit()
    return mid


@bp.route("/terminal/despachar/listo/<int:movimiento_id>")
def terminal_despacho_listo(movimiento_id):
    m = get_db().execute(
        "SELECT m.*, e.nombre AS empresa FROM movimientos m JOIN empresas e ON e.id = m.empresa_id WHERE m.id = ?",
        (movimiento_id,),
    ).fetchone()
    if m is None:
        abort(404)
    usados = pl.pallets_de_movimiento(movimiento_id)
    return render_template("terminal_listo.html", m=m, cantidad=len({u["pallet_id"] for u in usados}))
