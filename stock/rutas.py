"""Pantallas del programa."""
import sqlite3
from collections import defaultdict
from datetime import date, datetime

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

from .db import get_db, stock_de_productos, stock_por_id
from .excel import armar_excel

bp = Blueprint("stock", __name__)

TIPOS = {"INGRESO": "Ingreso", "EGRESO": "Egreso"}


# ---------------------------------------------------------------- utilidades


def _texto(nombre):
    return (request.form.get(nombre) or "").strip()


def _numero(texto):
    """Acepta '1.234,5', '1234,5' o '1234.5'."""
    texto = (texto or "").strip().replace(" ", "")
    if not texto:
        return None
    if "," in texto:
        texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


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
    }
    return render_template(
        "inicio.html", filas=filas, filtros=f, empresas=_empresas(), resumen=resumen
    )


@bp.route("/stock/excel")
def stock_excel():
    f = _filtros_stock()
    filas = stock_de_productos(f["empresa_id"], f["buscar"], f["solo_con_stock"])
    return _excel(
        "stock",
        "Stock",
        ["Empresa", "Código", "Descripción", "Unidad", "Stock", "Stock mínimo", "Último movimiento"],
        [
            (r["empresa"], r["codigo"], r["descripcion"], r["unidad"], r["stock"],
             r["stock_minimo"], r["ultimo_mov"] or "")
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
        """SELECT m.id, m.tipo, m.fecha, m.remito, m.observaciones, i.cantidad
           FROM movimiento_items i JOIN movimientos m ON m.id = i.movimiento_id
           WHERE i.producto_id = ? AND m.anulado = 0
           ORDER BY m.fecha, m.id""",
        (producto_id,),
    ).fetchall()
    saldo, lineas = 0, []
    for m in movs:
        saldo += m["cantidad"] if m["tipo"] == "INGRESO" else -m["cantidad"]
        lineas.append({**dict(m), "saldo": saldo})
    lineas.reverse()  # lo más nuevo arriba
    return render_template("producto_ficha.html", p=filas[0], lineas=lineas)


@bp.route("/api/productos")
def api_productos():
    empresa_id = _int_o_none(request.args.get("empresa_id"))
    if not empresa_id:
        return jsonify([])
    filas = stock_de_productos(empresa_id)
    return jsonify([
        {"id": r["id"], "codigo": r["codigo"], "descripcion": r["descripcion"],
         "unidad": r["unidad"], "stock": r["stock"]}
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
        where.append("(p.codigo LIKE ? OR p.descripcion LIKE ?)")
        params += [f"%{f['producto']}%"] * 2
    if not f["ver_anulados"]:
        where.append("m.anulado = 0")
    sql = """SELECT m.id, m.tipo, m.fecha, m.remito, m.observaciones, m.anulado,
                    e.nombre AS empresa, p.id AS producto_id, p.codigo, p.descripcion,
                    p.unidad, i.cantidad
             FROM movimientos m
             JOIN empresas e ON e.id = m.empresa_id
             JOIN movimiento_items i ON i.movimiento_id = m.id
             JOIN productos p ON p.id = i.producto_id"""
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
        ["Fecha", "Tipo", "Remito", "Empresa", "Código", "Descripción", "Cantidad", "Unidad",
         "Observaciones", "Anulado"],
        [
            (r["fecha"], TIPOS[r["tipo"]], r["remito"], r["empresa"], r["codigo"], r["descripcion"],
             r["cantidad"], r["unidad"], r["observaciones"] or "", "Sí" if r["anulado"] else "")
            for r in filas
        ],
    )


@bp.route("/movimientos/nuevo/<tipo>", methods=["GET", "POST"])
def movimiento_nuevo(tipo):
    tipo = tipo.upper()
    if tipo not in TIPOS:
        abort(404)
    db = get_db()
    if request.method == "POST":
        empresa_id = _int_o_none(request.form.get("empresa_id"))
        fecha = _texto("fecha")
        remito = _texto("remito")
        observaciones = _texto("observaciones")
        producto_ids = request.form.getlist("producto_id")
        cantidades = request.form.getlist("cantidad")

        errores = []
        if not empresa_id:
            errores.append("Elegí la empresa.")
        if not _fecha_valida(fecha):
            errores.append("La fecha no es válida.")
        if not remito:
            errores.append("El número de remito es obligatorio.")

        # Juntar renglones (si se repite un producto, se suman las cantidades).
        items = defaultdict(float)
        for pid, cant in zip(producto_ids, cantidades):
            pid = _int_o_none(pid)
            if not pid and not (cant or "").strip():
                continue  # renglón vacío
            numero = _numero(cant)
            if not pid:
                errores.append("Hay un renglón sin producto.")
            elif numero is None or numero <= 0:
                errores.append("Todas las cantidades tienen que ser mayores a cero.")
            else:
                items[pid] += numero
        if not items and not errores:
            errores.append("Cargá al menos un producto.")

        if items and empresa_id:
            validos = {
                r["id"]: r for r in db.execute(
                    f"SELECT * FROM productos WHERE empresa_id = ? AND id IN ({','.join('?' * len(items))})",
                    (empresa_id, *items.keys()),
                )
            }
            if len(validos) != len(items):
                errores.append("Hay productos que no son de la empresa elegida.")
            elif tipo == "EGRESO":
                stock = stock_por_id(items.keys())
                for pid, cant in items.items():
                    if cant > stock.get(pid, 0) + 1e-9:
                        p = validos[pid]
                        errores.append(
                            f"No hay stock suficiente de {p['codigo']} - {p['descripcion']}: "
                            f"hay {stock.get(pid, 0):g} y querés sacar {cant:g}."
                        )

        if tipo == "INGRESO" and empresa_id and remito and not errores:
            repetido = db.execute(
                "SELECT id FROM movimientos WHERE tipo = ? AND empresa_id = ? AND remito = ? AND anulado = 0",
                (tipo, empresa_id, remito),
            ).fetchone()
            if repetido and request.form.get("confirmar_repetido") != "1":
                errores.append(
                    "Ya hay un ingreso con ese número de remito para esta empresa. "
                    "Si es correcto, marcá la casilla 'Cargar igual' y guardá de nuevo."
                )

        if errores:
            for e in errores:
                flash(e, "error")
            renglones = [
                {"producto_id": _int_o_none(p), "cantidad": c}
                for p, c in zip(producto_ids, cantidades) if p or c
            ]
            return render_template(
                "movimiento_form.html", tipo=tipo, tipos=TIPOS, empresas=_empresas(solo_activas=True),
                form={"empresa_id": empresa_id, "fecha": fecha, "remito": remito,
                      "observaciones": observaciones}, renglones=renglones,
                mostrar_repetido=any("remito" in e and "Cargar igual" in e for e in errores),
            )

        cur = db.execute(
            "INSERT INTO movimientos (tipo, fecha, remito, empresa_id, observaciones) VALUES (?,?,?,?,?)",
            (tipo, fecha, remito, empresa_id, observaciones),
        )
        db.executemany(
            "INSERT INTO movimiento_items (movimiento_id, producto_id, cantidad) VALUES (?,?,?)",
            [(cur.lastrowid, pid, cant) for pid, cant in items.items()],
        )
        db.commit()
        flash(f"{TIPOS[tipo]} guardado (remito {remito}).", "ok")
        return redirect(url_for("stock.movimiento_detalle", movimiento_id=cur.lastrowid))

    return render_template(
        "movimiento_form.html", tipo=tipo, tipos=TIPOS, empresas=_empresas(solo_activas=True),
        form={"empresa_id": _int_o_none(request.args.get("empresa_id")),
              "fecha": date.today().isoformat()},
        renglones=[], mostrar_repetido=False,
    )


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
        """SELECT i.cantidad, p.id AS producto_id, p.codigo, p.descripcion, p.unidad
           FROM movimiento_items i JOIN productos p ON p.id = i.producto_id
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
            """SELECT i.producto_id, i.cantidad, p.codigo FROM movimiento_items i
               JOIN productos p ON p.id = i.producto_id WHERE i.movimiento_id = ?""",
            (movimiento_id,),
        ).fetchall()
        stock = stock_por_id({i["producto_id"] for i in items})
        a_restar = defaultdict(float)
        for i in items:
            a_restar[i["producto_id"]] += i["cantidad"]
        problemas = [
            i["codigo"] for i in items
            if stock.get(i["producto_id"], 0) - a_restar[i["producto_id"]] < -1e-9
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
