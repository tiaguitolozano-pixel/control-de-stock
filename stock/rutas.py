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
    asegurar_envase,
    envases,
    get_db,
    proximo_vencimiento_por_producto,
    stock_de_lotes,
    stock_de_productos,
    stock_por_lote_id,
)
from . import pallets as pl
from .excel import armar_excel
from .formato import cantidad_pallets
from .servicios import (
    CAMPOS_TRANSPORTE,
    ENCABEZADOS_PLANTILLA,
    chequear_lote,
    importar_conteo,
    leer_planilla,
    normalizar_patente,
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


def _excel(nombre, titulo, encabezados, filas, pie=None):
    archivo = armar_excel(titulo, encabezados, filas, pie)
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
        "lote": (request.args.get("lote") or "").strip(),
        "solo_con_stock": request.args.get("solo_con_stock") == "1",
        "ver_lotes": request.args.get("ver_lotes") == "1",
    }


def _stock_con_lotes(f):
    """Stock por código y, si se pidió, el detalle de sus lotes (solo lotes con stock)."""
    filas = stock_de_productos(f["empresa_id"], f["buscar"], f["solo_con_stock"])
    lotes = defaultdict(list)
    if f["ver_lotes"] or f["lote"]:
        for l in stock_de_lotes(empresa_id=f["empresa_id"], buscar=f["buscar"] or None, lote=f["lote"] or None):
            lotes[l["producto_id"]].append(l)
        if f["lote"]:
            filas = [r for r in filas if r["id"] in lotes]
    return filas, lotes


@bp.route("/")
def inicio():
    f = _filtros_stock()
    filas, lotes = _stock_con_lotes(f)
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
        "inicio.html", filas=filas, lotes=lotes, filtros=f, empresas=_empresas(), resumen=resumen,
        vtos=proximo_vencimiento_por_producto(f["empresa_id"]),
    )


@bp.route("/stock/excel")
def stock_excel():
    f = _filtros_stock()
    filas, lotes = _stock_con_lotes(f)
    if f["ver_lotes"] or f["lote"]:
        return _excel(
            "stock_por_lote",
            "Stock por lote",
            ["Empresa", "Código", "Descripción", "Lote", "Vencimiento", "Stock", "Envase", "Equivale a (pallets)"],
            [
                (l["empresa"], l["codigo"], l["descripcion"], l["lote"] or "(sin lote)", l["vencimiento"] or "",
                 l["stock"], l["unidad"], cantidad_pallets(l["stock"], l["unidades_por_pallet"]))
                for r in filas for l in lotes.get(r["id"], [])
            ],
        )
    vtos = proximo_vencimiento_por_producto(f["empresa_id"])
    return _excel(
        "stock",
        "Stock",
        ["Empresa", "Código", "Descripción", "Stock", "Envase", "Equivale a (pallets)", "Unidades por pallet",
         "Stock mínimo", "Próximo vencimiento", "Último movimiento"],
        [
            (r["empresa"], r["codigo"], r["descripcion"], r["stock"], r["unidad"],
             cantidad_pallets(r["stock"], r["unidades_por_pallet"]), r["unidades_por_pallet"],
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
    filas = _lotes_filtrados(f)
    return render_template("lotes.html", filas=filas, filtros=f, empresas=_empresas(),
                           donde=pl.resumen_por_lote([r["id"] for r in filas]) if filas else {})


@bp.route("/lotes/excel")
def lotes_excel():
    filas = _lotes_filtrados(_filtros_lotes())
    donde = pl.resumen_por_lote([r["id"] for r in filas]) if filas else {}
    return _excel(
        "stock_por_lote",
        "Stock por lote",
        ["Empresa", "Código", "Descripción", "Lote", "Vencimiento", "Stock", "Envase", "Equivale a (pallets)", "Ubicaciones",
         "Sin etiqueta"],
        [
            (r["empresa"], r["codigo"], r["descripcion"], r["lote"] or "(sin lote)",
             r["vencimiento"] or "", r["stock"], r["unidad"],
             cantidad_pallets(r["stock"], r["unidades_por_pallet"]),
             ", ".join(donde[r["id"]]["ubicaciones"]), donde[r["id"]]["sin_etiqueta"])
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
        unidad = _texto("unidad")
        if unidad == "__otro__":
            unidad = _texto("unidad_otra")
        por_pallet_texto = _texto("unidades_por_pallet")
        datos = {
            "empresa_id": producto["empresa_id"] if producto else _int_o_none(request.form.get("empresa_id")),
            "codigo": _texto("codigo"),
            "descripcion": _texto("descripcion"),
            "unidad": unidad,
            "stock_minimo": _numero(request.form.get("stock_minimo")) or 0,
            "activo": 1 if (request.form.get("activo") or not producto) else 0,
            "unidades_por_pallet": _numero(por_pallet_texto),
        }
        errores = []
        if not datos["unidad"]:
            errores.append("Elegí el tipo de envase.")
        if por_pallet_texto and (datos["unidades_por_pallet"] is None or datos["unidades_por_pallet"] <= 0):
            errores.append("Las unidades por pallet tienen que ser un número mayor a cero (o dejalo vacío).")
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
                        """UPDATE productos SET codigo=?, descripcion=?, unidad=?, stock_minimo=?, activo=?,
                                  unidades_por_pallet=?
                           WHERE id=?""",
                        (datos["codigo"], datos["descripcion"], datos["unidad"], datos["stock_minimo"],
                         datos["activo"], datos["unidades_por_pallet"], producto_id),
                    )
                else:
                    db.execute(
                        """INSERT INTO productos (empresa_id, codigo, descripcion, unidad, stock_minimo, activo,
                                                 unidades_por_pallet)
                           VALUES (?,?,?,?,?,?,?)""",
                        tuple(datos.values()),
                    )
                asegurar_envase(datos["unidad"])
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
            envases=envases(solo_activos=True),
        )
    inicial = producto or {
        "empresa_id": _int_o_none(request.args.get("empresa_id")), "unidad": "",
        "stock_minimo": 0, "activo": 1,
    }
    return render_template(
        "producto_form.html", producto=inicial, editando=bool(producto),
        empresas=_empresas(solo_activas=True), volver=request.args.get("volver", ""),
        envases=envases(solo_activos=True),
    )


# ------------------------------------------------------------ tipos de envase


@bp.route("/envases", methods=["GET", "POST"])
def envases_lista():
    db = get_db()
    if request.method == "POST":
        nombre = _texto("nombre")
        if not nombre:
            flash("Escribí el nombre del tipo de envase.", "error")
        elif db.execute("SELECT 1 FROM envases WHERE nombre = ?", (nombre,)).fetchone():
            flash(f"'{nombre}' ya está en la lista.", "error")
        else:
            asegurar_envase(nombre)
            db.commit()
            flash(f"Se agregó '{nombre}'.", "ok")
        return redirect(url_for("stock.envases_lista"))
    filas = db.execute(
        """SELECT v.*, (SELECT COUNT(*) FROM productos p WHERE v.nombre = p.unidad) AS productos
           FROM envases v ORDER BY v.activo DESC, v.nombre"""
    ).fetchall()
    return render_template("envases.html", envases=filas)


@bp.route("/envases/<int:envase_id>", methods=["POST"])
def envase_editar(envase_id):
    db = get_db()
    envase = db.execute("SELECT * FROM envases WHERE id = ?", (envase_id,)).fetchone()
    if envase is None:
        abort(404)
    if request.form.get("accion") == "activar":
        db.execute("UPDATE envases SET activo = 1 - activo WHERE id = ?", (envase_id,))
        db.commit()
        return redirect(url_for("stock.envases_lista"))
    nombre = _texto("nombre")
    if not nombre:
        flash("El nombre no puede quedar vacío.", "error")
        return redirect(url_for("stock.envases_lista"))
    otro = db.execute("SELECT id FROM envases WHERE nombre = ? AND id <> ?", (nombre, envase_id)).fetchone()
    if otro:
        flash(f"Ya existe un tipo de envase llamado '{nombre}'.", "error")
        return redirect(url_for("stock.envases_lista"))
    # Al cambiar el nombre, se actualizan también los productos que lo usan.
    db.execute("UPDATE productos SET unidad = ? WHERE unidad = ? COLLATE NOCASE", (nombre, envase["nombre"]))
    db.execute("UPDATE envases SET nombre = ? WHERE id = ?", (nombre, envase_id))
    db.commit()
    flash("Tipo de envase actualizado.", "ok")
    return redirect(url_for("stock.envases_lista"))


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
         "unidad": r["unidad"], "stock": r["stock"], "por_pallet": r["unidades_por_pallet"],
         "lotes": lotes.get(r["id"], [])}
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
        "transporte": (request.args.get("transporte") or "").strip(),
        "ver_anulados": request.args.get("ver_anulados") == "1",
    }


SQL_MOVS_DESDE = """
             FROM movimientos m
             JOIN empresas e ON e.id = m.empresa_id
             JOIN movimiento_items i ON i.movimiento_id = m.id
             JOIN productos p ON p.id = i.producto_id
             JOIN lotes l ON l.id = i.lote_id"""


def _where_movimientos(f, con_anulados):
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
    if f["transporte"]:
        texto = f"%{f['transporte']}%"
        condiciones = ["m.chofer LIKE ?", "m.transporte LIKE ?"]
        params += [texto, texto]
        patente = normalizar_patente(f["transporte"])
        if patente:  # 'ab 123' encuentra la patente AB123CD
            condiciones += ["m.patente_camion LIKE ?", "m.patente_semi LIKE ?"]
            params += [f"%{patente}%"] * 2
        where.append("(" + " OR ".join(condiciones) + ")")
    if not con_anulados:
        where.append("m.anulado = 0")
    return (" WHERE " + " AND ".join(where)) if where else "", params


def _renglones_movimientos(f, limite=5000):
    """Un renglón por producto de cada remito, según los filtros."""
    where, params = _where_movimientos(f, con_anulados=f["ver_anulados"])
    sql = ("""SELECT m.id, m.tipo, m.fecha, m.remito, m.observaciones, m.anulado,
                     m.chofer, m.transporte, m.patente_camion, m.patente_semi,
                     e.nombre AS empresa, p.id AS producto_id, p.codigo, p.descripcion,
                     p.unidad, p.unidades_por_pallet, i.cantidad, l.lote, l.vencimiento"""
           + SQL_MOVS_DESDE + where + " ORDER BY m.fecha DESC, m.id DESC, i.id")
    if limite:
        sql += f" LIMIT {int(limite)}"
    return get_db().execute(sql, params).fetchall()


def _totales_movimientos(f):
    """Total de ingresos y egresos por tipo de envase (nunca cuenta los remitos anulados)."""
    where, params = _where_movimientos(f, con_anulados=False)
    sql = ("""SELECT MIN(p.unidad) AS unidad,
                     SUM(CASE WHEN m.tipo = 'INGRESO' THEN i.cantidad ELSE 0 END) AS ingresos,
                     SUM(CASE WHEN m.tipo = 'EGRESO' THEN i.cantidad ELSE 0 END) AS egresos"""
           + SQL_MOVS_DESDE + where + " GROUP BY p.unidad COLLATE NOCASE ORDER BY MIN(p.unidad)")
    return [
        {**dict(r), "diferencia": r["ingresos"] - r["egresos"]}
        for r in get_db().execute(sql, params).fetchall()
    ]


@bp.route("/movimientos")
def movimientos():
    f = _filtros_movs()
    return render_template(
        "movimientos.html", filas=_renglones_movimientos(f), totales=_totales_movimientos(f),
        filtros=f, empresas=_empresas(), tipos=TIPOS,
    )


@bp.route("/movimientos/excel")
def movimientos_excel():
    f = _filtros_movs()
    filas = _renglones_movimientos(f, limite=None)
    encabezados = ["Fecha", "Remito", "Empresa", "Código", "Descripción", "Lote", "Vencimiento", "Envase",
                   "Ingreso (debe)", "Egreso (haber)", "Equivale a (pallets)", "Chofer", "Empresa de transporte",
                   "Patente camión", "Patente semi", "Observaciones", "Anulado"]
    col_ingreso = encabezados.index("Ingreso (debe)")
    pie = []
    for t in _totales_movimientos(f):
        vacio = [""] * col_ingreso
        pie.append([f"Total {t['unidad']}"] + vacio[1:] + [t["ingresos"], t["egresos"]])
        pie.append([f"Diferencia {t['unidad']} (ingresos - egresos)"] + vacio[1:] + [t["diferencia"]])
    return _excel(
        "movimientos",
        "Movimientos",
        encabezados,
        [
            (r["fecha"], r["remito"], r["empresa"], r["codigo"], r["descripcion"], r["lote"],
             r["vencimiento"] or "", r["unidad"],
             r["cantidad"] if r["tipo"] == "INGRESO" else None,
             r["cantidad"] if r["tipo"] == "EGRESO" else None,
             cantidad_pallets(r["cantidad"], r["unidades_por_pallet"]),
             r["chofer"] or "", r["transporte"] or "", r["patente_camion"] or "", r["patente_semi"] or "",
             r["observaciones"] or "", "Sí" if r["anulado"] else "")
            for r in filas
        ],
        pie=pie,
    )


def _distintos(columna):
    """Valores ya usados en una columna de movimientos, los más recientes primero."""
    return [
        r[0] for r in get_db().execute(
            f"""SELECT MIN({columna}) FROM movimientos WHERE COALESCE({columna}, '') <> ''
                GROUP BY {columna} COLLATE NOCASE ORDER BY MAX(id) DESC LIMIT 300"""
        )
    ]


def _transportes_previos():
    """Para autocompletar: cada chofer con el transporte y las patentes de su último viaje."""
    choferes = get_db().execute(
        """SELECT chofer, transporte, patente_camion, patente_semi, MAX(id) AS ultimo
           FROM movimientos WHERE COALESCE(chofer, '') <> ''
           GROUP BY chofer COLLATE NOCASE ORDER BY ultimo DESC LIMIT 300"""
    ).fetchall()
    return {
        "choferes": [
            {"chofer": r["chofer"], "transporte": r["transporte"] or "",
             "patente_camion": r["patente_camion"] or "", "patente_semi": r["patente_semi"] or ""}
            for r in choferes
        ],
        "transportes": _distintos("transporte"),
        "patentes_camion": _distintos("patente_camion"),
        "patentes_semi": _distintos("patente_semi"),
    }


def _form_movimiento(tipo, form, renglones, mostrar_repetido=False):
    return render_template(
        "movimiento_form.html", tipo=tipo, tipos=TIPOS, empresas=_empresas(solo_activas=True),
        form=form, renglones=renglones, mostrar_repetido=mostrar_repetido,
        previos=_transportes_previos(),
    )


@bp.route("/movimientos/nuevo/<tipo>", methods=["GET", "POST"])
def movimiento_nuevo(tipo):
    tipo = tipo.upper()
    if tipo not in TIPOS:
        abort(404)
    if request.method == "GET":
        return _form_movimiento(
            tipo,
            {"empresa_id": _int_o_none(request.args.get("empresa_id")), "fecha": date.today().isoformat(),
             "crear_pallets": "1"},
            [],
        )

    db = get_db()
    empresa_id = _int_o_none(request.form.get("empresa_id"))
    fecha = _texto("fecha")
    remito = _texto("remito")
    observaciones = _texto("observaciones")
    transporte = {c: _texto(c) for c in CAMPOS_TRANSPORTE}
    for c in ("patente_camion", "patente_semi"):
        transporte[c] = normalizar_patente(transporte[c])
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
        form = {"empresa_id": empresa_id, "fecha": fecha, "remito": remito, "observaciones": observaciones,
                "crear_pallets": request.form.get("crear_pallets", ""), **transporte}
        return _form_movimiento(tipo, form, renglones, mostrar_repetido)

    plan_pallets = {}
    if tipo == "INGRESO":
        lineas = []
        for (pid, clave_lote), cant in items.items():
            lote, vto = vencimientos[(pid, clave_lote)]
            lineas.append((pid, obtener_o_crear_lote(pid, lote, vto), cant))
    else:
        lineas = [(pid, lid, cant) for (pid, lid), cant in items.items()]
        # Sin pallets escaneados: el programa decide de qué pallets sale (ver pallets.planear_salida).
        for pid, lid, cant in lineas:
            plan_pallets[(pid, lid)] = pl.planear_salida(lid, cant, productos[pid]["unidades_por_pallet"])
    mid = registrar_movimiento(tipo, empresa_id, fecha, remito, observaciones, lineas, transporte)
    avisos, creados = [], []
    if tipo == "INGRESO" and request.form.get("crear_pallets") == "1":
        creados, avisos = pl.crear_pallets_de_ingreso(mid)
    elif plan_pallets:
        pl.registrar_salida_de_pallets(mid, plan_pallets)
    db.commit()
    mensaje = f"{TIPOS[tipo]} guardado (remito {remito})."
    if creados:
        mensaje += (" Se armó 1 pallet" if len(creados) == 1 else f" Se armaron {len(creados)} pallets") \
            + ": imprimí sus etiquetas con el botón de abajo."
    flash(mensaje, "ok")
    for aviso in avisos:
        flash(aviso, "aviso")
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
                  p.unidades_por_pallet, l.lote, l.vencimiento
           FROM movimiento_items i JOIN productos p ON p.id = i.producto_id
           JOIN lotes l ON l.id = i.lote_id
           WHERE i.movimiento_id = ? ORDER BY i.id""",
        (movimiento_id,),
    ).fetchall()
    usados = pl.pallets_de_movimiento(movimiento_id)
    armados = []
    if m["tipo"] == "INGRESO":
        armados = pl.pallets(ids=[r[0] for r in get_db().execute(
            "SELECT id FROM pallets WHERE movimiento_id = ?", (movimiento_id,))], con_contenido=False)
    return render_template("movimiento_detalle.html", m=m, items=items, tipos=TIPOS, usados=usados,
                           armados=sorted(armados, key=lambda p: p["id"]))


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
        problemas = pl.problemas_al_anular(movimiento_id)
        if problemas:
            flash("No se puede anular: " + "; ".join(problemas)
                  + ". Primero anulá esos egresos o desarmá esos pallets.", "error")
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
                                                      "remito": "STOCK INICIAL", "crear_pallets": "1"})
    fecha = _texto("fecha")
    remito = _texto("remito") or "STOCK INICIAL"
    form = {"fecha": fecha, "remito": remito, "crear_pallets": request.form.get("crear_pallets", "")}
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
    if form["crear_pallets"] == "1":
        resumen["pallets"], resumen["avisos"] = [], []
        for mid in resumen["movimientos"]:
            creados, avisos = pl.crear_pallets_de_ingreso(mid)
            resumen["pallets"] += creados
            resumen["avisos"] += avisos
        get_db().commit()
    return render_template("importar.html", form=form, resumen=resumen)


@bp.route("/importar/plantilla")
def importar_plantilla():
    ejemplo = [
        ("Empresa Ejemplo SA", "ABC-001", "Producto de ejemplo x 25kg", "Bolsas", 40, "L2026-15", "31/12/2027", 120),
        ("Empresa Ejemplo SA", "ABC-001", "Producto de ejemplo x 25kg", "Bolsas", 40, "L2026-22", "30/06/2028", 40),
        ("Otra Empresa SRL", "XY-10", "Solvente x 200 L", "Tambores de chapa", 4, "", "", 15),
    ]
    archivo = armar_excel("Conteo", ENCABEZADOS_PLANTILLA, ejemplo)
    return send_file(
        archivo, as_attachment=True, download_name="plantilla_conteo_stock.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
