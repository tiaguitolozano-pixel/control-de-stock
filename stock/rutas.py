"""Pantallas del programa."""
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import (
    Blueprint,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)

from .db import (
    get_db,
    proximo_vencimiento_por_producto,
    stock_de_lotes,
    stock_de_productos,
    stock_por_lote_id,
)
from .excel import armar_excel
from .servicios import (
    ENCABEZADOS_PLANTILLA,
    chequear_lote,
    importar_conteo,
    leer_planilla,
    obtener_o_crear_lote,
    parse_fecha,
    parse_numero,
    registrar_movimiento,
)

bp = Blueprint("stock", __name__)

TIPOS = {"INGRESO": "Ingreso", "EGRESO": "Egreso"}
DIAS_AVISO = int(os.environ.get("STOCK_DIAS_AVISO", "60"))


def _limite_aviso():
    """Fecha hasta la cual un vencimiento se considera 'por vencer'."""
    return (date.today() + timedelta(days=DIAS_AVISO)).isoformat()


@bp.app_context_processor
def _globales():
    return {"limite_aviso": _limite_aviso(), "hoy_iso": date.today().isoformat(), "dias_aviso": DIAS_AVISO}


# ---------------------------------------------------------------- utilidades


def _texto(nombre):
    return (request.form.get(nombre) or "").strip()


def _numero(texto):
    return parse_numero(texto)


def _fecha_valida(texto):
    try:
        datetime.strptime(texto, "%Y-%m-%d")
        return True
    except (TypeError, ValueError):
        return False


def _empresas(solo_activas=False):
    sql = "SELECT * FROM empresas"
    if solo_activas:
        sql += " WHERE activa = 1"
    return get_db().execute(sql + " ORDER BY nombre").fetchall()


def _empresa_o_404(empresa_id):
    fila = get_db().execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    if fila is None:
        abort(404)
    return fila


def _int_o_none(valor):
    try:
        return int(valor)
    except (TypeError, ValueError):
        return None


def _excel(nombre, titulo, encabezados, filas):
    archivo = armar_excel(titulo, encabezados, filas)
    return send_file(
        archivo,
        as_attachment=True,
        download_name=f"{nombre}_{date.today().isoformat()}.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


# ------------------------------------------------------------- inicio / stock


def _filtros_stock():
    return {
        "empresa_id": _int_o_none(request.args.get("empresa_id")),
        "buscar": (request.args.get("buscar") or "").strip(),
        "solo_con_stock": request.args.get("solo_con_stock") == "1",
    }


@bp.route("/")
def inicio():
    f = _filtros_stock()
    filas = stock_de_productos(f["empresa_id"], f["buscar"], f["solo_con_stock"])
    db = get_db()
    resumen = {
        "empresas": db.execute("SELECT COUNT(*) FROM empresas WHERE activa = 1").fetchone()[0],
        "productos": db.execute("SELECT COUNT(*) FROM productos WHERE activo = 1").fetchone()[0],
        "movs_hoy": db.execute(
            "SELECT COUNT(*) FROM movimientos WHERE fecha = ? AND anulado = 0",
            (date.today().isoformat(),),
        ).fetchone()[0],
        "bajo_minimo": sum(
            1 for r in filas if r["activo"] and r["stock_minimo"] > 0 and r["stock"] < r["stock_minimo"]
        ),
        "por_vencer": len(stock_de_lotes(empresa_id=f["empresa_id"], vence_hasta=_limite_aviso())),
    }
    return render_template(
        "inicio.html", filas=filas, filtros=f, empresas=_empresas(), resumen=resumen,
        vtos=proximo_vencimiento_por_producto(f["empresa_id"]),
    )


@bp.route("/stock/excel")
def stock_excel():
    f = _filtros_stock()
    filas = stock_de_productos(f["empresa_id"], f["buscar"], f["solo_con_stock"])
    vtos = proximo_vencimiento_por_producto(f["empresa_id"])
    return _excel(
        "stock",
        "Stock",
        ["Empresa", "Código", "Descripción", "Unidad", "Stock", "Stock mínimo", "Próximo vencimiento",
         "Último movimiento"],
        [
            (r["empresa"], r["codigo"], r["descripcion"], r["unidad"], r["stock"],
             r["stock_minimo"], vtos.get(r["id"], ""), r["ultimo_mov"] or "")
            for r in filas
        ],
    )


# --------------------------------------------------------------- stock por lote


def _filtros_lotes():
    vence = request.args.get("vence")
    return {
        "empresa_id": _int_o_none(request.args.get("empresa_id")),
        "buscar": (request.args.get("buscar") or "").strip(),
        "vence": vence if vence in ("vencidos", "por_vencer") else "",
    }


def _lotes_filtrados(f):
    hasta = None
    if f["vence"] == "vencidos":
        hasta = (date.today() - timedelta(days=1)).isoformat()
    elif f["vence"] == "por_vencer":
        hasta = _limite_aviso()
    return stock_de_lotes(empresa_id=f["empresa_id"], buscar=f["buscar"], vence_hasta=hasta)


@bp.route("/lotes")
def lotes():
    f = _filtros_lotes()
    return render_template("lotes.html", filas=_lotes_filtrados(f), filtros=f, empresas=_empresas())


@bp.route("/lotes/excel")
def lotes_excel():
    filas = _lotes_filtrados(_filtros_lotes())
    return _excel(
        "stock_por_lote",
        "Stock por lote",
        ["Empresa", "Código", "Descripción", "Lote", "Vencimiento", "Stock", "Unidad"],
        [
            (r["empresa"], r["codigo"], r["descripcion"], r["lote"] or "(sin lote)",
             r["vencimiento"] or "", r["stock"], r["unidad"])
            for r in filas
        ],
    )


# ------------------------------------------------------------------ empresas


@bp.route("/empresas")
def empresas():
    filas = get_db().execute(
        """SELECT e.*, COUNT(p.id) AS productos
           FROM empresas e LEFT JOIN productos p ON p.empresa_id = e.id
           GROUP BY e.id ORDER BY e.nombre"""
    ).fetchall()
    return render_template("empresas.html", empresas=filas)


@bp.route("/empresas/nueva", methods=["GET", "POST"])
@bp.route("/empresas/<int:empresa_id>/editar", methods=["GET", "POST"])
def empresa_form(empresa_id=None):
    empresa = _empresa_o_404(empresa_id) if empresa_id else None
    if request.method == "POST":
        datos = {
            "nombre": _texto("nombre"),
            "cuit": _texto("cuit"),
            "contacto": _texto("contacto"),
            "telefono": _texto("telefono"),
            "activa": 1 if request.form.get("activa") else 0,
        }
        if not empresa:
            datos["activa"] = 1
        if not datos["nombre"]:
            flash("El nombre de la empresa es obligatorio.", "error")
            return render_template("empresa_form.html", empresa=datos, editando=bool(empresa))
        db = get_db()
        try:
            if empresa:
                db.execute(
                    "UPDATE empresas SET nombre=?, cuit=?, contacto=?, telefono=?, activa=? WHERE id=?",
                    (*datos.values(), empresa_id),
                )
            else:
                db.execute(
                    "INSERT INTO empresas (nombre, cuit, contacto, telefono, activa) VALUES (?,?,?,?,?)",
                    tuple(datos.values()),
                )
            db.commit()
        except sqlite3.IntegrityError:
            flash("Ya existe una empresa con ese nombre.", "error")
            return render_template("empresa_form.html", empresa=datos, editando=bool(empresa))
        flash("Empresa guardada.", "ok")
        return redirect(url_for("stock.empresas"))
    return render_template("empresa_form.html", empresa=empresa or {"activa": 1}, editando=bool(empresa))


# ----------------------------------------------------------------- productos


@bp.route("/productos")
def productos():
    empresa_id = _int_o_none(request.args.get("empresa_id"))
    buscar = (request.args.get("buscar") or "").strip()
    filas = stock_de_productos(empresa_id, buscar)
    return render_template(
        "productos.html", productos=filas, empresas=_empresas(), empresa_id=empresa_id, buscar=buscar
    )


@bp.route("/productos/nuevo", methods=["GET", "POST"])
@bp.route("/productos/<int:producto_id>/editar", methods=["GET", "POST"])
def producto_form(producto_id=None):
    db = get_db()
    producto = None
    if producto_id:
        producto = db.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
        if producto is None:
            abort(404)
    if request.method == "POST":
        datos = {
            "empresa_id": producto["empresa_id"] if producto else _int_o_none(request.form.get("empresa_id")),
            "codigo": _texto("codigo"),
            "descripcion": _texto("descripcion"),
            "unidad": _texto("unidad") or "unidades",
            "stock_minimo": _numero(request.form.get("stock_minimo")) or 0,
            "activo": 1 if (request.form.get("activo") or not producto) else 0,
        }
        errores = []
        if not datos["empresa_id"]:
            errores.append("Elegí la empresa.")
        if not datos["codigo"]:
            errores.append("El código es obligatorio.")
        if not datos["descripcion"]:
            errores.append("La descripción es obligatoria.")
        if datos["stock_minimo"] < 0:
            errores.append("El stock mínimo no puede ser negativo.")
        if not errores:
            try:
                if producto:
                    db.execute(
                        """UPDATE productos SET codigo=?, descripcion=?, unidad=?, stock_minimo=?, activo=?
                           WHERE id=?""",
                        (datos["codigo"], datos["descripcion"], datos["unidad"],
                         datos["stock_minimo"], datos["activo"], producto_id),
                    )
                else:
                    db.execute(
                        """INSERT INTO productos (empresa_id, codigo, descripcion, unidad, stock_minimo, activo)
                           VALUES (?,?,?,?,?,?)""",
                        tuple(datos.values()),
                    )
                db.commit()
                flash("Producto guardado.", "ok")
                destino = request.form.get("volver") or url_for(
                    "stock.productos", empresa_id=datos["empresa_id"]
                )
                if not destino.startswith("/"):
                    destino = url_for("stock.productos")
                return redirect(destino)
            except sqlite3.IntegrityError:
                errores.append("Esa empresa ya tiene un producto con ese código.")
        for e in errores:
            flash(e, "error")
        return render_template(
            "producto_form.html", producto=datos, editando=bool(producto),
            empresas=_empresas(solo_activas=True), volver=request.form.get("volver", ""),
        )
    inicial = producto or {
        "empresa_id": _int_o_none(request.args.get("empresa_id")), "unidad": "unidades",
        "stock_minimo": 0, "activo": 1,
    }
    return render_template(
        "producto_form.html", producto=inicial, editando=bool(producto),
        empresas=_empresas(solo_activas=True), volver=request.args.get("volver", ""),
    )


@bp.route("/productos/<int:producto_id>")
def producto_ficha(producto_id):
    filas = stock_de_productos(producto_ids=[producto_id])
    if not filas:
        abort(404)
    movs = get_db().execute(
        """SELECT m.id, m.tipo, m.fecha, m.remito, m.observaciones, i.cantidad,
                  l.lote, l.vencimiento
           FROM movimiento_items i JOIN movimientos m ON m.id = i.movimiento_id
           JOIN lotes l ON l.id = i.lote_id
           WHERE i.producto_id = ? AND m.anulado = 0
           ORDER BY m.fecha, m.id""",
        (producto_id,),
    ).fetchall()
    saldo, lineas = 0, []
    for m in movs:
        saldo += m["cantidad"] if m["tipo"] == "INGRESO" else -m["cantidad"]
        lineas.append({**dict(m), "saldo": saldo})
    lineas.reverse()  # lo más nuevo arriba
    return render_template(
        "producto_ficha.html", p=filas[0], lineas=lineas, lotes=stock_de_lotes(producto_id=producto_id)
    )


@bp.route("/api/productos")
def api_productos():
    empresa_id = _int_o_none(request.args.get("empresa_id"))
    if not empresa_id:
        return jsonify([])
    filas = stock_de_productos(empresa_id)
    lotes = defaultdict(list)
    for l in stock_de_lotes(empresa_id=empresa_id):
        lotes[l["producto_id"]].append(
            {"id": l["id"], "lote": l["lote"], "vencimiento": l["vencimiento"], "stock": l["stock"]}
        )
    return jsonify([
        {"id": r["id"], "codigo": r["codigo"], "descripcion": r["descripcion"],
         "unidad": r["unidad"], "stock": r["stock"], "lotes": lotes.get(r["id"], [])}
        for r in filas if r["activo"]
    ])


# --------------------------------------------------------------- movimientos


def _filtros_movs():
    return {
        "empresa_id": _int_o_none(request.args.get("empresa_id")),
        "tipo": request.args.get("tipo") if request.args.get("tipo") in TIPOS else "",
        "desde": request.args.get("desde") if _fecha_valida(request.args.get("desde")) else "",
        "hasta": request.args.get("hasta") if _fecha_valida(request.args.get("hasta")) else "",
        "remito": (request.args.get("remito") or "").strip(),
        "producto": (request.args.get("producto") or "").strip(),
        "ver_anulados": request.args.get("ver_anulados") == "1",
    }


def _renglones_movimientos(f):
    """Un renglón por producto de cada remito, según los filtros."""
    where, params = [], []
    if f["empresa_id"]:
        where.append("m.empresa_id = ?")
        params.append(f["empresa_id"])
    if f["tipo"]:
        where.append("m.tipo = ?")
        params.append(f["tipo"])
    if f["desde"]:
        where.append("m.fecha >= ?")
        params.append(f["desde"])
    if f["hasta"]:
        where.append("m.fecha <= ?")
        params.append(f["hasta"])
    if f["remito"]:
        where.append("m.remito LIKE ?")
        params.append(f"%{f['remito']}%")
    if f["producto"]:
        where.append("(p.codigo LIKE ? OR p.descripcion LIKE ? OR l.lote LIKE ?)")
        params += [f"%{f['producto']}%"] * 3
    if not f["ver_anulados"]:
        where.append("m.anulado = 0")
    sql = """SELECT m.id, m.tipo, m.fecha, m.remito, m.observaciones, m.anulado,
                    e.nombre AS empresa, p.id AS producto_id, p.codigo, p.descripcion,
                    p.unidad, i.cantidad, l.lote, l.vencimiento
             FROM movimientos m
             JOIN empresas e ON e.id = m.empresa_id
             JOIN movimiento_items i ON i.movimiento_id = m.id
             JOIN productos p ON p.id = i.producto_id
             JOIN lotes l ON l.id = i.lote_id"""
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY m.fecha DESC, m.id DESC, i.id LIMIT 5000"
    return get_db().execute(sql, params).fetchall()


@bp.route("/movimientos")
def movimientos():
    f = _filtros_movs()
    return render_template(
        "movimientos.html", filas=_renglones_movimientos(f), filtros=f,
        empresas=_empresas(), tipos=TIPOS,
    )


@bp.route("/movimientos/excel")
def movimientos_excel():
    f = _filtros_movs()
    filas = _renglones_movimientos(f)
    return _excel(
        "movimientos",
        "Movimientos",
        ["Fecha", "Tipo", "Remito", "Empresa", "Código", "Descripción", "Lote", "Vencimiento",
         "Cantidad", "Unidad", "Observaciones", "Anulado"],
        [
            (r["fecha"], TIPOS[r["tipo"]], r["remito"], r["empresa"], r["codigo"], r["descripcion"],
             r["lote"], r["vencimiento"] or "", r["cantidad"], r["unidad"], r["observaciones"] or "",
             "Sí" if r["anulado"] else "")
            for r in filas
        ],
    )


def _form_movimiento(tipo, form, renglones, mostrar_repetido=False):
    return render_template(
        "movimiento_form.html", tipo=tipo, tipos=TIPOS, empresas=_empresas(solo_activas=True),
        form=form, renglones=renglones, mostrar_repetido=mostrar_repetido,
    )


@bp.route("/movimientos/nuevo/<tipo>", methods=["GET", "POST"])
def movimiento_nuevo(tipo):
    tipo = tipo.upper()
    if tipo not in TIPOS:
        abort(404)
    if request.method == "GET":
        return _form_movimiento(
            tipo,
            {"empresa_id": _int_o_none(request.args.get("empresa_id")), "fecha": date.today().isoformat()},
            [],
        )

    db = get_db()
    empresa_id = _int_o_none(request.form.get("empresa_id"))
    fecha = _texto("fecha")
    remito = _texto("remito")
    observaciones = _texto("observaciones")
    columnas = {c: request.form.getlist(c) for c in ("producto_id", "cantidad", "lote", "vencimiento", "lote_id")}
    largo = len(columnas["producto_id"])
    renglones = []
    for n in range(largo):
        r = {c: (v[n] if n < len(v) else "") for c, v in columnas.items()}
        r["producto_id"] = _int_o_none(r["producto_id"])
        r["lote_id"] = _int_o_none(r["lote_id"])
        r["lote"] = (r["lote"] or "").strip()
        if r["producto_id"] or (r["cantidad"] or "").strip():
            renglones.append(r)

    errores = []
    if not empresa_id:
        errores.append("Elegí la empresa.")
    if not _fecha_valida(fecha):
        errores.append("La fecha no es válida.")
    if not remito:
        errores.append("El número de remito es obligatorio.")
    if not renglones:
        errores.append("Cargá al menos un producto.")

    productos = {}
    if empresa_id:
        productos = {
            r["id"]: r for r in db.execute("SELECT * FROM productos WHERE empresa_id = ?", (empresa_id,))
        }

    # Juntar renglones: si se repite el mismo producto y lote, se suman las cantidades.
    items = defaultdict(float)
    vencimientos = {}
    for r in renglones:
        cant = _numero(r["cantidad"])
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

    if tipo == "EGRESO" and items and not errores:
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

    mostrar_repetido = False
    if tipo == "INGRESO" and empresa_id and remito and not errores:
        repetido = db.execute(
            "SELECT id FROM movimientos WHERE tipo = ? AND empresa_id = ? AND remito = ? AND anulado = 0",
            (tipo, empresa_id, remito),
        ).fetchone()
        if repetido and request.form.get("confirmar_repetido") != "1":
            mostrar_repetido = True
            errores.append(
                "Ya hay un ingreso con ese número de remito para esta empresa. "
                "Si es correcto, marcá la casilla 'Cargar igual' y guardá de nuevo."
            )

    if errores:
        for e in dict.fromkeys(errores):  # sin repetidos
            flash(e, "error")
        form = {"empresa_id": empresa_id, "fecha": fecha, "remito": remito, "observaciones": observaciones}
        return _form_movimiento(tipo, form, renglones, mostrar_repetido)

    if tipo == "INGRESO":
        lineas = []
        for (pid, clave_lote), cant in items.items():
            lote, vto = vencimientos[(pid, clave_lote)]
            lineas.append((pid, obtener_o_crear_lote(pid, lote, vto), cant))
    else:
        lineas = [(pid, lid, cant) for (pid, lid), cant in items.items()]
    mid = registrar_movimiento(tipo, empresa_id, fecha, remito, observaciones, lineas)
    db.commit()
    flash(f"{TIPOS[tipo]} guardado (remito {remito}).", "ok")
    return redirect(url_for("stock.movimiento_detalle", movimiento_id=mid))


def _movimiento_o_404(movimiento_id):
    m = get_db().execute(
        """SELECT m.*, e.nombre AS empresa FROM movimientos m
           JOIN empresas e ON e.id = m.empresa_id WHERE m.id = ?""",
        (movimiento_id,),
    ).fetchone()
    if m is None:
        abort(404)
    return m


@bp.route("/movimientos/<int:movimiento_id>")
def movimiento_detalle(movimiento_id):
    m = _movimiento_o_404(movimiento_id)
    items = get_db().execute(
        """SELECT i.cantidad, p.id AS producto_id, p.codigo, p.descripcion, p.unidad,
                  l.lote, l.vencimiento
           FROM movimiento_items i JOIN productos p ON p.id = i.producto_id
           JOIN lotes l ON l.id = i.lote_id
           WHERE i.movimiento_id = ? ORDER BY i.id""",
        (movimiento_id,),
    ).fetchall()
    return render_template("movimiento_detalle.html", m=m, items=items, tipos=TIPOS)


@bp.route("/movimientos/<int:movimiento_id>/anular", methods=["POST"])
def movimiento_anular(movimiento_id):
    m = _movimiento_o_404(movimiento_id)
    db = get_db()
    if m["anulado"]:
        flash("Ese remito ya estaba anulado.", "error")
        return redirect(url_for("stock.movimiento_detalle", movimiento_id=movimiento_id))
    motivo = _texto("motivo")
    if not motivo:
        flash("Escribí el motivo de la anulación.", "error")
        return redirect(url_for("stock.movimiento_detalle", movimiento_id=movimiento_id))
    if m["tipo"] == "INGRESO":
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
        problemas = [
            i["codigo"] + (f" (lote {i['lote']})" if i["lote"] else "") for i in items
            if stock.get(i["lote_id"], 0) - a_restar[i["lote_id"]] < -1e-9
        ]
        if problemas:
            flash(
                "No se puede anular: el stock quedaría negativo para "
                + ", ".join(sorted(set(problemas)))
                + ". Primero anulá los egresos que usaron esa mercadería.",
                "error",
            )
            return redirect(url_for("stock.movimiento_detalle", movimiento_id=movimiento_id))
    db.execute(
        "UPDATE movimientos SET anulado = 1, motivo_anulacion = ? WHERE id = ?", (motivo, movimiento_id)
    )
    db.commit()
    flash("Remito anulado. El stock se recalculó.", "ok")
    return redirect(url_for("stock.movimiento_detalle", movimiento_id=movimiento_id))


# ------------------------------------------------------- importar planilla


@bp.route("/importar", methods=["GET", "POST"])
def importar():
    if request.method == "GET":
        return render_template("importar.html", form={"fecha": date.today().isoformat(),
                                                      "remito": "STOCK INICIAL"})
    fecha = _texto("fecha")
    remito = _texto("remito") or "STOCK INICIAL"
    form = {"fecha": fecha, "remito": remito}
    archivo = request.files.get("archivo")
    errores = []
    if not _fecha_valida(fecha):
        errores.append("La fecha no es válida.")
    if not archivo or not archivo.filename:
        errores.append("Elegí el archivo de Excel.")
    if errores:
        return render_template("importar.html", form=form, errores=errores)
    filas, errores = leer_planilla(archivo.stream)
    if not errores:
        resumen, errores = importar_conteo(filas, fecha, remito)
    if errores:
        return render_template("importar.html", form=form, errores=errores)
    return render_template("importar.html", form=form, resumen=resumen)


@bp.route("/importar/plantilla")
def importar_plantilla():
    ejemplo = [
        ("Empresa Ejemplo SA", "ABC-001", "Producto de ejemplo x 20kg", "bolsas", "L2026-15", "31/12/2027", 120),
        ("Empresa Ejemplo SA", "ABC-001", "Producto de ejemplo x 20kg", "bolsas", "L2026-22", "30/06/2028", 40),
        ("Otra Empresa SRL", "XY-10", "Otro producto", "cajas", "", "", 15),
    ]
    archivo = armar_excel("Conteo", ENCABEZADOS_PLANTILLA, ejemplo)
    return send_file(
        archivo, as_attachment=True, download_name="plantilla_conteo_stock.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
