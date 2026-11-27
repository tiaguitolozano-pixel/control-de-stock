"""Pantallas del programa."""
import os
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_file,
    send_from_directory,
    url_for,
)

from .db import (
    asegurar_envase,
    envases,
    get_db,
    proximo_vencimiento_por_producto,
    stock_de_lotes,
    stock_de_productos,
)
from . import correcciones as cr
from . import fotos as ft
from . import insumos as ins
from . import pallets as pl
from . import remitos as rm
from . import vacios
from .excel import armar_excel
from .formato import cantidad_pallets, formato_fecha, formato_numero, singular
from .historial import libro_por_lote, lotes_con_saldo_negativo
from .servicios import (
    CAMPOS_TRANSPORTE,
    ENCABEZADOS_PLANTILLA,
    buscar_lote,
    importar_conteo,
    leer_planilla,
    normalizar_patente,
    normalizar_transporte,
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


def _kilos(cantidad, kilos_por_unidad):
    """Kilos totales (cantidad x kilos por unidad), o None si el producto no tiene cargados los kilos."""
    if not kilos_por_unidad or cantidad is None:
        return None
    return round(cantidad * kilos_por_unidad, 3)


def _excel(nombre, titulo, encabezados, filas, pie=None, hojas_extra=()):
    archivo = armar_excel(titulo, encabezados, filas, pie, hojas_extra)
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
        # El stock mínimo es opcional: si ningún producto de la lista lo tiene, no se muestra.
        "hay_minimos": any(r["stock_minimo"] > 0 for r in filas),
        "por_vencer": len(stock_de_lotes(empresa_id=f["empresa_id"], vence_hasta=_limite_aviso())),
        "kilos": sum(_kilos(r["stock"], r["kilos_por_unidad"]) or 0 for r in filas if r["stock"] > 0),
    }
    # Pallets (u otros insumos) de los clientes que hay en el depósito, uno por insumo.
    saldos = defaultdict(float)
    for (_, insumo_id), saldo in ins.saldos_cliente(f["empresa_id"]).items():
        saldos[insumo_id] += saldo
    nombres = {i["id"]: i["nombre"] for i in ins.lista(solo_activos=False)}
    resumen["insumos_cliente"] = [(nombres[i], round(s, 6)) for i, s in saldos.items()]
    resumen["envases_cliente"] = vacios.totales_por_tipo(f["empresa_id"])
    return render_template(
        "inicio.html", filas=filas, lotes=lotes, filtros=f, empresas=_empresas(), resumen=resumen,
        vtos=proximo_vencimiento_por_producto(f["empresa_id"]), negativos=lotes_con_saldo_negativo(f["empresa_id"]),
    )


@bp.route("/stock/excel")
def stock_excel():
    f = _filtros_stock()
    filas, lotes = _stock_con_lotes(f)
    if f["ver_lotes"] or f["lote"]:
        return _excel(
            "stock_por_lote",
            "Stock por lote",
            ["Empresa", "Código", "Descripción", "Lote", "Vencimiento", "Cantidad", "Envase", "Kg por unidad",
             "Kilos totales", "Equivale a (pallets)"],
            [
                (l["empresa"], l["codigo"], l["descripcion"], l["lote"] or "(sin lote)", l["vencimiento"] or "",
                 l["stock"], l["unidad"], l["kilos_por_unidad"], _kilos(l["stock"], l["kilos_por_unidad"]),
                 cantidad_pallets(l["stock"], l["unidades_por_pallet"]))
                for r in filas for l in lotes.get(r["id"], [])
            ],
        )
    vtos = proximo_vencimiento_por_producto(f["empresa_id"])
    return _excel(
        "stock",
        "Stock",
        ["Empresa", "Código", "Descripción", "Cantidad", "Envase", "Kg por unidad", "Kilos totales",
         "Equivale a (pallets)", "Unidades por pallet", "Stock mínimo", "Próximo vencimiento", "Último movimiento"],
        [
            (r["empresa"], r["codigo"], r["descripcion"], r["stock"], r["unidad"], r["kilos_por_unidad"],
             _kilos(r["stock"], r["kilos_por_unidad"]), cantidad_pallets(r["stock"], r["unidades_por_pallet"]),
             r["unidades_por_pallet"], r["stock_minimo"], vtos.get(r["id"], ""), r["ultimo_mov"] or "")
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
        ["Empresa", "Código", "Descripción", "Lote", "Vencimiento", "Cantidad", "Envase", "Kilos totales",
         "Equivale a (pallets)", "Ubicaciones", "Sin etiqueta"],
        [
            (r["empresa"], r["codigo"], r["descripcion"], r["lote"] or "(sin lote)",
             r["vencimiento"] or "", r["stock"], r["unidad"], _kilos(r["stock"], r["kilos_por_unidad"]),
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


REMITO_STOCK_INICIAL = "STOCK INICIAL"


def _tiene_remitos(producto_id, sin=None):
    """Si el producto está en algún remito que no se anuló (sin contar el remito `sin`)."""
    return get_db().execute(
        """SELECT 1 FROM movimiento_items i JOIN movimientos m ON m.id = i.movimiento_id
           WHERE i.producto_id = ? AND m.anulado = 0 AND m.id <> ? LIMIT 1""",
        (producto_id, sin or 0),
    ).fetchone() is not None


def _stock_inicial_de(producto_id):
    """El remito STOCK INICIAL del producto (el primero que no está anulado), o None.

    'otros' cuenta los renglones de otros productos: el remito que arma Importar Excel tiene los de toda la empresa.
    """
    if not producto_id:
        return None
    return get_db().execute(
        """SELECT m.*, (SELECT COUNT(*) FROM movimiento_items o
                        WHERE o.movimiento_id = m.id AND o.producto_id <> ?) AS otros
           FROM movimientos m
           WHERE m.tipo = 'INGRESO' AND m.anulado = 0 AND m.trasvaso_id IS NULL AND UPPER(TRIM(m.remito)) = ?
             AND EXISTS (SELECT 1 FROM movimiento_items i WHERE i.movimiento_id = m.id AND i.producto_id = ?)
           ORDER BY m.fecha, m.id LIMIT 1""",
        (producto_id, REMITO_STOCK_INICIAL, producto_id),
    ).fetchone()


def _renglones_stock_inicial(si, producto_id):
    """Los lotes del stock inicial del producto, para el formulario."""
    return [
        {"lote": r["lote"], "vencimiento": r["vencimiento"] or "", "cantidad": formato_numero(r["cantidad"], 6)}
        for r in get_db().execute(
            """SELECT l.lote, l.vencimiento, i.cantidad FROM movimiento_items i JOIN lotes l ON l.id = i.lote_id
               WHERE i.movimiento_id = ? AND i.producto_id = ? ORDER BY i.id""",
            (si["id"], producto_id),
        )
    ]


def _fecha_para_stock_inicial(producto_id):
    """La fecha que se propone para un stock inicial nuevo: la del primer remito del producto (así ningún egreso
    queda antes de la mercadería), o la de hoy."""
    hoy = date.today().isoformat()
    if not producto_id:
        return hoy
    primera = get_db().execute(
        """SELECT MIN(m.fecha) FROM movimiento_items i JOIN movimientos m ON m.id = i.movimiento_id
           WHERE i.producto_id = ? AND m.anulado = 0""",
        (producto_id,),
    ).fetchone()[0]
    return min(primera, hoy) if primera else hoy


def _leer_stock_inicial():
    """Los renglones del stock inicial del formulario del producto (lote, vencimiento, cantidad), sin los vacíos."""
    columnas = [request.form.getlist(c) for c in ("si_lote", "si_vencimiento", "si_cantidad")]
    renglones = []
    for n in range(max(len(c) for c in columnas)):
        lote, vencimiento, cantidad = ((c[n] if n < len(c) else "").strip() for c in columnas)
        if lote or vencimiento or cantidad:
            renglones.append({"lote": lote, "vencimiento": vencimiento, "cantidad": cantidad})
    return renglones


def _texto_stock_inicial(items, unidad):
    """'132,5 bolsas en 2 lotes'."""
    total = sum(items.values())
    unidad = (singular(unidad) if total == 1 else unidad).lower()
    return f"{formato_numero(total)} {unidad} en {len(items)} {'lote' if len(items) == 1 else 'lotes'}"


def _avisar_pallets(creados, borrados, etiquetas="con el botón de abajo"):
    """Después de corregir un ingreso: los pallets que se armaron o se borraron solos."""
    if creados:
        flash(f"Hay pallets nuevos: imprimí sus etiquetas {etiquetas}.", "aviso")
    if borrados:
        uno = len(borrados) == 1
        flash(f"{'El pallet' if uno else 'Los pallets'} {', '.join(pl.codigo_pallet(p) for p in borrados)} ya no "
              f"{'hacía' if uno else 'hacían'} falta y se {'borró' if uno else 'borraron'}: si "
              f"tenía{'' if uno else 'n'} etiqueta pegada, sacala.", "aviso")


def _avisar_negativos(empresa_id, producto_id=None, lote_ids=()):
    """Después de guardar: si algún lote sigue quedando negativo en alguna fecha (un error de antes), se avisa."""
    for n in lotes_con_saldo_negativo(empresa_id):
        if n["producto_id"] == producto_id or n["lote_id"] in lote_ids:
            flash(f"Ojo: el saldo de {n['codigo']} ({('lote ' + n['lote']) if n['lote'] else 'sin lote'}) sigue "
                  f"quedando negativo desde el {formato_fecha(n['desde'])} (llega a {formato_numero(n['minimo'])} "
                  f"{n['unidad'].lower()}): hay egresos con fecha anterior a la mercadería. Revisá la fecha del stock "
                  "inicial o la de esos egresos.", "aviso")


def _guardar_stock_inicial(producto, si, fecha, items, vencimientos):
    """Carga o corrige el stock inicial del producto: sus renglones del remito STOCK INICIAL.

    producto: {id, empresa_id, codigo, unidad}. si: el remito STOCK INICIAL que ya tenía, o None.
    items y vencimientos: lo que devolvió revisar_renglones (vacíos para sacar el stock inicial).
    Si el remito tiene otros productos (el de Importar Excel) y cambia la fecha, este producto pasa a un remito
    propio y los otros no se tocan. Devuelve (errores, texto para el mensaje, (pallets armados, pallets
    borrados)). No hace commit: si hay errores, hay que deshacer (rollback).
    """
    db = get_db()
    pid = producto["id"]
    items = {(pid, lote): c for (_, lote), c in items.items()}
    vencimientos = {(pid, lote): v for (_, lote), v in vencimientos.items()}
    if si is None:
        if not items:
            return [], "", ([], [])
        registrar_movimiento("INGRESO", producto["empresa_id"], fecha, REMITO_STOCK_INICIAL,
                             "Stock inicial, cargado con el producto", rm.lineas_de_ingreso(items, vencimientos))
        return [], (f" Stock inicial: {_texto_stock_inicial(items, producto['unidad'])} "
                    f"(remito {REMITO_STOCK_INICIAL})."), ([], [])

    antes = cr.foto(si["id"], pid)
    lotes_viejos = {i["lote_id"] for i in antes["items"]}
    existentes = {l["id"] for l in (buscar_lote(pid, lote) for lote, _ in vencimientos.values()) if l}
    estado = cr.Estado(lotes_viejos | existentes)
    m, notas = si, []
    if items and fecha != si["fecha"]:
        if si["otros"]:
            nuevo = db.execute(
                "INSERT INTO movimientos (tipo, fecha, remito, empresa_id, observaciones) VALUES ('INGRESO',?,?,?,?)",
                (fecha, si["remito"], si["empresa_id"], si["observaciones"]),
            ).lastrowid
            mios = [r[0] for r in db.execute(
                "SELECT id FROM movimiento_items WHERE movimiento_id = ? AND producto_id = ?", (si["id"], pid))]
            marcas = ",".join("?" * len(mios))
            db.execute(f"UPDATE movimiento_items SET movimiento_id = ? WHERE id IN ({marcas})", [nuevo, *mios])
            db.execute(
                f"""UPDATE pallets SET movimiento_id = ? WHERE movimiento_id = ?
                    AND id IN (SELECT pallet_id FROM pallet_movs WHERE item_id IN ({marcas}))""",
                [nuevo, si["id"], *mios],
            )
            cr.anotar_remito("EDITADO", cr.foto(si["id"]),
                             f"Los renglones de {producto['codigo']} pasaron a un remito {si['remito']} propio, con "
                             f"fecha {formato_fecha(fecha)} (se cambió la fecha de su stock inicial desde el producto).")
            notas.append(f"Antes estaba en el remito {si['remito']} del {formato_fecha(si['fecha'])}, junto con otros "
                         "productos: pasó a un remito propio.")
            m = db.execute("SELECT * FROM movimientos WHERE id = ?", (nuevo,)).fetchone()
        else:
            db.execute("UPDATE movimientos SET fecha = ? WHERE id = ?", (fecha, si["id"]))
            db.execute("UPDATE envase_movs SET fecha = ? WHERE movimiento_id = ?", (fecha, si["id"]))
            m = db.execute("SELECT * FROM movimientos WHERE id = ?", (si["id"],)).fetchone()
    lineas = rm.lineas_de_ingreso(items, vencimientos, corrigiendo=m["id"])
    errores, creados, borrados = cr.aplicar_items(m, lineas, solo_producto=pid)
    if not errores:
        errores = estado.problemas({lid for _, lid, _ in lineas}, inicio="Stock inicial: ",
                                   fecha=fecha if items else None)
    if errores:
        return errores, "", ([], [])
    cr.limpiar_lotes(lotes_viejos)
    cambios = cr.diferencias(antes, cr.foto(m["id"], pid)) + _texto_pallets(creados, borrados)
    if not cambios:
        return [], "", ([], [])
    queda_algo = db.execute(
        """SELECT EXISTS (SELECT 1 FROM movimiento_items WHERE movimiento_id = :m)
               OR EXISTS (SELECT 1 FROM movimiento_insumos WHERE movimiento_id = :m)
               OR EXISTS (SELECT 1 FROM envase_movs WHERE movimiento_id = :m)
               OR EXISTS (SELECT 1 FROM movimiento_fotos WHERE movimiento_id = :m)""",
        {"m": m["id"]},
    ).fetchone()[0]
    if not queda_algo:
        # Era un remito con el stock inicial de este producto solo: se borra (y queda anotado).
        cr.anotar_remito("BORRADO", antes,
                         cr.texto_remito(antes) + "\nSe borró desde el producto: quedó sin stock inicial.")
        cr.borrar_movimiento(m)
        return [], " Se borró su stock inicial.", (creados, borrados)
    cr.anotar_remito("EDITADO", cr.foto(m["id"]), "\n".join(
        [f"Stock inicial de {producto['codigo']}, corregido desde el producto:"] + notas + cambios))
    if not items:
        return [], " Se quitó su stock inicial.", (creados, borrados)
    return [], f" Stock inicial corregido: {_texto_stock_inicial(items, producto['unidad'])}.", (creados, borrados)


@bp.route("/productos/nuevo", methods=["GET", "POST"])
@bp.route("/productos/<int:producto_id>/editar", methods=["GET", "POST"])
def producto_form(producto_id=None):
    db = get_db()
    producto = None
    if producto_id:
        producto = db.execute("SELECT * FROM productos WHERE id = ?", (producto_id,)).fetchone()
        if producto is None:
            abort(404)
    volver = (request.form.get("volver") if request.method == "POST" else request.args.get("volver")) or ""
    # El stock inicial se carga y se corrige en el producto. Si el producto se crea desde un remito, no: lo que
    # llega ya está en ese remito.
    con_stock_inicial = bool(producto) or not volver
    si = _stock_inicial_de(producto_id) if producto else None
    stock_inicial = {
        "fecha": si["fecha"] if si else _fecha_para_stock_inicial(producto_id),
        "renglones": _renglones_stock_inicial(si, producto_id) if si else [],
        "cargado": si is not None,
        "compartido": bool(si and si["otros"]),
        "movimiento_id": si["id"] if si else None,
        "con_remitos": bool(producto) and _tiene_remitos(producto_id, sin=si["id"] if si else None),
    }
    if request.method == "POST":
        unidad = _texto("unidad")
        if unidad == "__otro__":
            unidad = _texto("unidad_otra")
        por_pallet_texto = _texto("unidades_por_pallet")
        kilos_texto = _texto("kilos_por_unidad")
        datos = {
            "empresa_id": producto["empresa_id"] if producto else _int_o_none(request.form.get("empresa_id")),
            "codigo": _texto("codigo"),
            "descripcion": _texto("descripcion"),
            "unidad": unidad,
            "stock_minimo": _numero(request.form.get("stock_minimo")) or 0,
            "activo": 1 if (request.form.get("activo") or not producto) else 0,
            "unidades_por_pallet": _numero(por_pallet_texto),
            "kilos_por_unidad": _numero(kilos_texto),
        }
        errores = []
        if not datos["unidad"]:
            errores.append("Elegí el tipo de envase.")
        if por_pallet_texto and (datos["unidades_por_pallet"] is None or datos["unidades_por_pallet"] <= 0):
            errores.append("Las unidades por pallet tienen que ser un número mayor a cero (o dejalo vacío).")
        if kilos_texto and (datos["kilos_por_unidad"] is None or datos["kilos_por_unidad"] <= 0):
            errores.append("Los kilos por unidad tienen que ser un número mayor a cero (o dejalo vacío).")
        if not datos["empresa_id"]:
            errores.append("Elegí la empresa.")
        if not datos["codigo"]:
            errores.append("El código es obligatorio.")
        if not datos["descripcion"]:
            errores.append("La descripción es obligatoria.")
        if datos["stock_minimo"] < 0:
            errores.append("El stock mínimo no puede ser negativo.")
        items, vencimientos = {}, {}
        if con_stock_inicial:
            stock_inicial.update(fecha=_texto("si_fecha"), renglones=_leer_stock_inicial())
        if stock_inicial["renglones"]:
            if not _fecha_valida(stock_inicial["fecha"]):
                errores.append("Stock inicial: la fecha no es válida.")
            pid = producto_id or -1  # el producto nuevo todavía no tiene número
            items, vencimientos, errores_si = rm.revisar_renglones(
                "INGRESO", [dict(r, producto_id=pid) for r in stock_inicial["renglones"]],
                {pid: {"id": pid, "codigo": "Stock inicial"}}, corrigiendo=si["id"] if si else None,
            )
            errores += errores_si
        if not errores:
            try:
                if producto:
                    db.execute(
                        """UPDATE productos SET codigo=?, descripcion=?, unidad=?, stock_minimo=?, activo=?,
                                  unidades_por_pallet=?, kilos_por_unidad=?
                           WHERE id=?""",
                        (datos["codigo"], datos["descripcion"], datos["unidad"], datos["stock_minimo"],
                         datos["activo"], datos["unidades_por_pallet"], datos["kilos_por_unidad"], producto_id),
                    )
                else:
                    producto_id = db.execute(
                        """INSERT INTO productos (empresa_id, codigo, descripcion, unidad, stock_minimo, activo,
                                                 unidades_por_pallet, kilos_por_unidad)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        tuple(datos.values()),
                    ).lastrowid
                asegurar_envase(datos["unidad"])
                texto_si, pallets_si = "", ([], [])
                if con_stock_inicial:
                    # El stock inicial queda como un ingreso del producto, con sus lotes (remito STOCK INICIAL).
                    errores, texto_si, pallets_si = _guardar_stock_inicial(
                        {"id": producto_id, "empresa_id": datos["empresa_id"], "codigo": datos["codigo"],
                         "unidad": datos["unidad"]},
                        si, stock_inicial["fecha"], items, vencimientos,
                    )
                if errores:
                    db.rollback()
                    producto_id = producto["id"] if producto else None
                else:
                    db.commit()
                    flash("Producto guardado." + texto_si, "ok")
                    _avisar_pallets(*pallets_si, etiquetas="desde el remito STOCK INICIAL")
                    _avisar_negativos(datos["empresa_id"], producto_id=producto_id)
                    destino = request.form.get("volver") or url_for(
                        "stock.productos", empresa_id=datos["empresa_id"]
                    )
                    if not destino.startswith("/"):
                        destino = url_for("stock.productos")
                    return redirect(destino)
            except sqlite3.IntegrityError:
                db.rollback()
                producto_id = producto["id"] if producto else None
                errores.append("Esa empresa ya tiene un producto con ese código.")
        for e in errores:
            flash(e, "error")
        return render_template(
            "producto_form.html", producto=datos, editando=bool(producto), volver=volver,
            empresas=_empresas(solo_activas=True), envases=envases(solo_activos=True),
            con_stock_inicial=con_stock_inicial, stock_inicial=stock_inicial,
        )
    inicial = producto or {
        "empresa_id": _int_o_none(request.args.get("empresa_id")), "unidad": "",
        "stock_minimo": 0, "activo": 1,
    }
    return render_template(
        "producto_form.html", producto=inicial, editando=bool(producto), volver=volver,
        empresas=_empresas(solo_activas=True), envases=envases(solo_activos=True),
        con_stock_inicial=con_stock_inicial, stock_inicial=stock_inicial,
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
    if request.form.get("accion") == "vacios":
        db.execute("UPDATE envases SET lleva_vacios = 1 - lleva_vacios WHERE id = ?", (envase_id,))
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
    renglones = get_db().execute(
        SQL_RENGLONES + " WHERE i.producto_id = ? AND m.anulado = 0", (producto_id,)
    ).fetchall()
    return render_template(
        "producto_ficha.html", p=filas[0], libro=libro_por_lote(renglones),
        lotes=stock_de_lotes(producto_id=producto_id),
    )


@bp.route("/api/productos")
def api_productos():
    """Los productos de la empresa con su stock por lote. excluir: un remito que no se cuenta (al corregir un
    egreso, para que se vea lo que había sin él)."""
    empresa_id = _int_o_none(request.args.get("empresa_id"))
    if not empresa_id:
        return jsonify([])
    excluir = _int_o_none(request.args.get("excluir"))
    filas = stock_de_productos(empresa_id, excluir_movimiento=excluir)
    lotes = defaultdict(list)
    for l in stock_de_lotes(empresa_id=empresa_id, excluir_movimiento=excluir):
        lotes[l["producto_id"]].append(
            {"id": l["id"], "lote": l["lote"], "vencimiento": l["vencimiento"], "stock": l["stock"]}
        )
    return jsonify([
        {"id": r["id"], "codigo": r["codigo"], "descripcion": r["descripcion"],
         "unidad": r["unidad"], "stock": r["stock"], "por_pallet": r["unidades_por_pallet"],
         "kilos": r["kilos_por_unidad"], "lotes": lotes.get(r["id"], [])}
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

# Un renglón por producto de cada remito, con lo necesario para el historial por lote.
SQL_RENGLONES = """SELECT m.id, m.tipo, m.fecha, m.remito, m.observaciones, m.anulado, m.trasvaso_id,
                          m.chofer, m.transporte, m.patente_camion, m.patente_semi, m.contenedor, m.tipo_contenedor,
                          e.nombre AS empresa, p.id AS producto_id, p.codigo, p.descripcion, p.unidad,
                          p.unidades_por_pallet, p.kilos_por_unidad, i.id AS item_id, i.cantidad,
                          l.id AS lote_id, l.lote, l.vencimiento""" + SQL_MOVS_DESDE


def _where_movimientos(f, con_anulados, con_producto=True):
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
    if f["producto"] and con_producto:
        where.append("(p.codigo LIKE ? OR p.descripcion LIKE ? OR l.lote LIKE ?)")
        params += [f"%{f['producto']}%"] * 3
    if f["transporte"]:
        texto = f"%{f['transporte']}%"
        condiciones = ["m.chofer LIKE ?", "m.transporte LIKE ?", "m.contenedor LIKE ?"]
        params += [texto, texto, texto]
        patente = normalizar_patente(f["transporte"])
        if patente:  # 'ab 123' encuentra la patente AB123CD y 'mscu 123' el contenedor MSCU1234567
            condiciones += ["m.patente_camion LIKE ?", "m.patente_semi LIKE ?", "m.contenedor LIKE ?"]
            params += [f"%{patente}%"] * 3
        where.append("(" + " OR ".join(condiciones) + ")")
    if not con_anulados:
        where.append("m.anulado = 0")
    return (" WHERE " + " AND ".join(where)) if where else "", params


LIMITE_RENGLONES = 5000


def _renglones_movimientos(f, limite=LIMITE_RENGLONES):
    """Un renglón por producto de cada remito, según los filtros, de lo más viejo a lo más nuevo.

    Si hay más que el límite, quedan los más recientes.
    """
    where, params = _where_movimientos(f, con_anulados=f["ver_anulados"])
    # Dentro de un día, primero los ingresos (ver historial.py).
    sql = SQL_RENGLONES + where + " ORDER BY m.fecha DESC, m.tipo = 'EGRESO' DESC, m.id DESC, i.id DESC"
    if limite:
        sql += f" LIMIT {int(limite)}"
    return get_db().execute(sql, params).fetchall()[::-1]


def _totales_movimientos(f):
    """Total de ingresos y egresos por tipo de envase, con sus kilos (nunca cuenta los remitos anulados)."""
    where, params = _where_movimientos(f, con_anulados=False)
    sql = ("""SELECT MIN(p.unidad) AS unidad,
                     SUM(CASE WHEN m.tipo = 'INGRESO' THEN i.cantidad ELSE 0 END) AS ingresos,
                     SUM(CASE WHEN m.tipo = 'EGRESO' THEN i.cantidad ELSE 0 END) AS egresos,
                     SUM(CASE WHEN m.tipo = 'INGRESO' THEN i.cantidad * COALESCE(p.kilos_por_unidad, 0)
                              ELSE 0 END) AS kilos_ingresos,
                     SUM(CASE WHEN m.tipo = 'EGRESO' THEN i.cantidad * COALESCE(p.kilos_por_unidad, 0)
                              ELSE 0 END) AS kilos_egresos,
                     COUNT(DISTINCT CASE WHEN p.kilos_por_unidad IS NULL THEN p.id END) AS sin_kilos"""
           + SQL_MOVS_DESDE + where + " GROUP BY p.unidad COLLATE NOCASE ORDER BY MIN(p.unidad)")
    return [
        {**dict(r), "diferencia": r["ingresos"] - r["egresos"]}
        for r in get_db().execute(sql, params).fetchall()
    ]


def _kilos_de_totales(totales):
    """Los kilos de todos los envases juntos."""
    ingresos = round(sum(t["kilos_ingresos"] for t in totales), 3)
    egresos = round(sum(t["kilos_egresos"] for t in totales), 3)
    return {"ingresos": ingresos, "egresos": egresos, "diferencia": round(ingresos - egresos, 3),
            "sin_kilos": sum(t["sin_kilos"] for t in totales)}


def _renglones_insumos(f):
    """Los insumos adicionales y los envases vacíos o sucios de los remitos, con los mismos filtros
    (el de producto no aplica)."""
    if f["producto"]:
        return []
    where, params = _where_movimientos(f, con_anulados=f["ver_anulados"], con_producto=False)
    db = get_db()
    insumos = [dict(r) for r in db.execute(
        """SELECT m.id, m.tipo, m.fecha, m.remito, m.anulado, e.nombre AS empresa,
                  mi.insumo_id, mi.propietario, mi.cantidad, s.nombre, s.unidad
           FROM movimientos m JOIN empresas e ON e.id = m.empresa_id
           JOIN movimiento_insumos mi ON mi.movimiento_id = m.id
           JOIN insumos s ON s.id = mi.insumo_id""" + where + " ORDER BY m.fecha, m.id, mi.id",
        params,
    )]
    envases = [
        {"id": r["id"], "tipo": r["tipo"], "fecha": r["fecha"], "remito": r["remito"], "anulado": r["anulado"],
         "empresa": r["empresa"], "insumo_id": None, "propietario": None, "cantidad": r["cantidad"],
         "nombre": f"{r['envase']} {vacios.palabras(r['envase'], r['estado'])[1]}", "unidad": "", "envase": True}
        for r in db.execute(
            """SELECT m.id, m.tipo, m.fecha, m.remito, m.anulado, e.nombre AS empresa, s.nombre AS envase,
                      v.estado, ABS(SUM(v.cantidad)) AS cantidad
               FROM movimientos m JOIN empresas e ON e.id = m.empresa_id
               JOIN envase_movs v ON v.movimiento_id = m.id
               JOIN envases s ON s.id = v.envase_id""" + where
            + " GROUP BY m.id, v.envase_id, v.estado ORDER BY m.fecha, m.id, s.nombre, v.estado DESC",
            params,
        )
    ]
    return sorted(insumos + envases, key=lambda r: (r["fecha"], r["id"], r.get("envase", False)))


def _totales_insumos(renglones):
    totales = {}
    for r in renglones:
        if r["anulado"]:
            continue
        t = totales.setdefault((r["nombre"].lower(), r["propietario"] or ""),
                               {"nombre": ins.nombre_renglon(r), "unidad": r["unidad"], "entran": 0, "salen": 0})
        t["entran" if r["tipo"] == "INGRESO" else "salen"] += r["cantidad"]
    return [totales[k] for k in sorted(totales)]


@bp.route("/movimientos")
def movimientos():
    f = _filtros_movs()
    renglones = _renglones_movimientos(f)
    totales = _totales_movimientos(f)
    insumos = _renglones_insumos(f)
    return render_template(
        "movimientos.html", libro=libro_por_lote(renglones, f["desde"], f["hasta"]),
        cantidad_renglones=len(renglones), limite=LIMITE_RENGLONES, totales=totales,
        kilos=_kilos_de_totales(totales), insumos=insumos, totales_insumos=_totales_insumos(insumos),
        hay_envases=any(r.get("envase") for r in insumos),
        nombre_insumo=ins.nombre_renglon, filtros=f, empresas=_empresas(), tipos=TIPOS,
    )


@bp.route("/movimientos/excel")
def movimientos_excel():
    f = _filtros_movs()
    libro = libro_por_lote(_renglones_movimientos(f, limite=None), f["desde"], f["hasta"])
    encabezados = ["Fecha", "Remito", "Empresa", "Código", "Descripción", "Lote", "Vencimiento", "Envase",
                   "Ingreso (debe)", "Egreso (haber)", "Kilos", "Saldo del lote", "Saldo en kg", "Equivale a (pallets)",
                   "Chofer", "Empresa de transporte", "Patente camión", "Patente semi", "Contenedor",
                   "Tipo de contenedor", "Observaciones", "Anulado"]
    filas = []
    for p in libro:
        for l in p["lotes"]:
            if l["saldo_anterior"] is not None:
                filas.append((f["desde"], "Saldo anterior", p["empresa"], p["codigo"], p["descripcion"], l["lote"],
                              l["vencimiento"] or "", p["unidad"], None, None, None, l["saldo_anterior"],
                              l["kilos_anterior"]))
            for r in l["renglones"]:
                filas.append((
                    r["fecha"], r["remito"], r["empresa"], r["codigo"], r["descripcion"], r["lote"],
                    r["vencimiento"] or "", r["unidad"],
                    r["cantidad"] if r["tipo"] == "INGRESO" else None,
                    r["cantidad"] if r["tipo"] == "EGRESO" else None,
                    r["kilos"], r["saldo"], r["kilos_saldo"], cantidad_pallets(r["cantidad"], r["unidades_por_pallet"]),
                    r["chofer"] or "", r["transporte"] or "", r["patente_camion"] or "", r["patente_semi"] or "",
                    r["contenedor"] or "", r["tipo_contenedor"] or "", r["observaciones"] or "",
                    "Sí" if r["anulado"] else "",
                ))
    col_ingreso = encabezados.index("Ingreso (debe)")
    vacio = [""] * (col_ingreso - 1)
    pie = []
    totales = _totales_movimientos(f)
    for t in totales:
        pie.append([f"Total {t['unidad']}"] + vacio + [t["ingresos"], t["egresos"]])
        pie.append([f"Diferencia {t['unidad']} (ingresos - egresos)"] + vacio + [t["diferencia"]])
    kilos = _kilos_de_totales(totales)
    if kilos["ingresos"] or kilos["egresos"]:
        pie.append(["Total kilos"] + vacio + [kilos["ingresos"], kilos["egresos"]])
        pie.append(["Diferencia kilos (ingresos - egresos)"] + vacio + [kilos["diferencia"]])
    insumos = [
        (r["fecha"], r["remito"], r["empresa"], ins.nombre_renglon(r),
         r["cantidad"] if r["tipo"] == "INGRESO" else None, r["cantidad"] if r["tipo"] == "EGRESO" else None,
         r["unidad"], "Sí" if r["anulado"] else "")
        for r in _renglones_insumos(f)
    ]
    hojas = [("Insumos", ["Fecha", "Remito", "Empresa", "Insumo o envase", "En ingresos", "En egresos", "Unidad",
                          "Anulado"], insumos)] if insumos else []
    return _excel("movimientos", "Movimientos", encabezados, filas, pie=pie, hojas_extra=hojas)


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
        "contenedores": _distintos("contenedor"),
        "tipos_contenedor": _tipos_contenedor(),
    }


TIPOS_CONTENEDOR = ["20' Dry", "40' Dry", "40' High Cube", "20' Reefer", "40' Reefer", "20' Open Top",
                    "40' Open Top", "20' Flat Rack", "40' Flat Rack", "20' Isotanque (ISO tank)"]


def _tipos_contenedor():
    """Los tipos ya usados primero y después los más comunes."""
    usados = _distintos("tipo_contenedor")
    vistos = {t.lower() for t in usados}
    return usados + [t for t in TIPOS_CONTENEDOR if t.lower() not in vistos]


def _form_movimiento(tipo, form, renglones, mostrar_repetido=False, textos_insumos=None, textos_envases=None,
                     editando=None):
    """El formulario de un remito nuevo o, con editando (el remito guardado), el de una corrección."""
    return render_template(
        "movimiento_form.html", tipo=tipo, tipos=TIPOS, empresas=_empresas(solo_activas=not editando),
        form=form, renglones=renglones, mostrar_repetido=mostrar_repetido,
        previos=_transportes_previos(), insumos=ins.lista(), campos_insumo=ins.campos,
        textos_insumos=textos_insumos or {}, envases=vacios.tipos_para_cargar(), campo_envase=vacios.campo,
        columnas_envases=vacios.COLUMNAS[tipo], textos_envases=textos_envases or {}, editando=editando,
    )


def _leer_remito(tipo, m=None):
    """Lee y revisa el formulario de un remito: uno nuevo, o uno guardado que se está corrigiendo (m).

    Devuelve (d, errores, mostrar_repetido). d tiene lo leído, para guardarlo o para volver a mostrar el
    formulario; si no hay errores también trae los renglones revisados (items y vencimientos).
    """
    db = get_db()
    empresa_id = m["empresa_id"] if m else _int_o_none(request.form.get("empresa_id"))
    d = {
        "empresa_id": empresa_id, "fecha": _texto("fecha"), "remito": _texto("remito"),
        "observaciones": _texto("observaciones"),
        "transporte": normalizar_transporte({c: _texto(c) for c in CAMPOS_TRANSPORTE}),
        "renglones": rm.leer_renglones(request.form),
    }
    d["insumos"], errores_insumos, d["textos_insumos"] = ins.leer_formulario(request.form)
    d["envases"], errores_envases, d["textos_envases"] = vacios.leer_formulario(request.form, tipo)
    d["fotos"], errores_fotos = ft.leer_subidas(request.files.getlist("fotos"))

    errores = []
    if not empresa_id:
        errores.append("Elegí la empresa.")
    if not _fecha_valida(d["fecha"]):
        errores.append("La fecha no es válida.")
    if not d["remito"]:
        errores.append("El número de remito es obligatorio.")
    if not d["renglones"] and not d["insumos"] and not d["envases"] and not errores_insumos and not errores_envases:
        errores.append("Cargá al menos un producto (o solo insumos o envases vacíos, por ejemplo una devolución "
                       "de pallets o los tambores vacíos que manda el cliente).")
    errores += errores_insumos + errores_envases + errores_fotos
    if m:
        # Al corregir, lo que tenía el mismo remito no cuenta: se revisa cómo quedaría con lo nuevo.
        errores += ins.problemas_al_cambiar(empresa_id, tipo, d["insumos"] + _insumos_que_quedan(m), m["id"])
        errores += vacios.problemas_al_cambiar(empresa_id, tipo, d["envases"] + _envases_que_quedan(m),
                                               movimiento_id=m["id"])
    else:
        if tipo == "EGRESO" and empresa_id and d["insumos"]:
            errores += ins.chequear_egreso(empresa_id, d["insumos"])
        if tipo == "EGRESO" and empresa_id and d["envases"]:
            errores += vacios.chequear(empresa_id, tipo, d["envases"])

    d["productos"] = productos = rm.productos_de_empresa(empresa_id)
    d["items"], d["vencimientos"], errores_renglones = rm.revisar_renglones(
        tipo, d["renglones"], productos, corrigiendo=m["id"] if m else None)
    errores += errores_renglones
    if tipo == "EGRESO" and not errores:
        errores += rm.chequear_stock(d["items"], productos, d["fecha"], excluir=[m["id"]] if m else ())

    mostrar_repetido = False
    cambia_numero = not m or d["remito"].lower() != (m["remito"] or "").lower()
    if tipo == "INGRESO" and empresa_id and d["remito"] and cambia_numero and not errores:
        repetido = db.execute(
            "SELECT id FROM movimientos WHERE tipo = ? AND empresa_id = ? AND remito = ? AND anulado = 0 AND id <> ?",
            (tipo, empresa_id, d["remito"], m["id"] if m else 0),
        ).fetchone()
        if repetido and request.form.get("confirmar_repetido") != "1":
            mostrar_repetido = True
            errores.append(
                "Ya hay un ingreso con ese número de remito para esta empresa. "
                "Si es correcto, marcá la casilla 'Cargar igual' y guardá de nuevo."
            )
    return d, errores, mostrar_repetido


def _mostrar_de_nuevo(tipo, d, errores, mostrar_repetido, m=None):
    for e in dict.fromkeys(errores):  # sin repetidos
        flash(e, "error")
    if d["fotos"]:
        flash("Las fotos no se guardaron: volvé a elegirlas antes de guardar.", "aviso")
    form = {"empresa_id": d["empresa_id"], "fecha": d["fecha"], "remito": d["remito"],
            "observaciones": d["observaciones"], "crear_pallets": request.form.get("crear_pallets", ""),
            **d["transporte"]}
    return _form_movimiento(tipo, form, d["renglones"], mostrar_repetido, d["textos_insumos"], d["textos_envases"],
                            editando=m)


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
    d, errores, mostrar_repetido = _leer_remito(tipo)
    if errores:
        return _mostrar_de_nuevo(tipo, d, errores, mostrar_repetido)

    empresa_id, remito = d["empresa_id"], d["remito"]
    plan_pallets = {}
    if tipo == "INGRESO":
        lineas = rm.lineas_de_ingreso(d["items"], d["vencimientos"])
    else:
        lineas, plan_pallets = rm.lineas_de_egreso(d["items"], d["productos"])
    mid = registrar_movimiento(tipo, empresa_id, d["fecha"], remito, d["observaciones"], lineas, d["transporte"],
                               d["insumos"])
    vacios.registrar(empresa_id, d["fecha"], tipo, d["envases"], movimiento_id=mid)
    avisos, creados = [], []
    if tipo == "INGRESO" and request.form.get("crear_pallets") == "1":
        creados, avisos = pl.crear_pallets_de_ingreso(mid)
    elif plan_pallets:
        pl.registrar_salida_de_pallets(mid, plan_pallets)
    cantidad_fotos = ft.guardar(mid, d["fotos"])
    db.commit()
    mensaje = f"{TIPOS[tipo]} guardado (remito {remito})."
    if cantidad_fotos:
        mensaje += " Con 1 foto." if cantidad_fotos == 1 else f" Con {cantidad_fotos} fotos."
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


def items_de_movimiento(movimiento_id):
    """Los renglones de un remito con sus kilos, y los totales (por tipo de envase y en kilos)."""
    items = [
        {**dict(i), "kilos": _kilos(i["cantidad"], i["kilos_por_unidad"])}
        for i in get_db().execute(
            """SELECT i.cantidad, p.id AS producto_id, p.codigo, p.descripcion, p.unidad,
                      p.unidades_por_pallet, p.kilos_por_unidad, l.id AS lote_id, l.lote, l.vencimiento
               FROM movimiento_items i JOIN productos p ON p.id = i.producto_id
               JOIN lotes l ON l.id = i.lote_id
               WHERE i.movimiento_id = ? ORDER BY i.id""",
            (movimiento_id,),
        )
    ]
    por_envase = {}
    for i in items:
        por_envase.setdefault(i["unidad"].lower(), [i["unidad"], 0])[1] += i["cantidad"]
    totales = {
        "envases": list(por_envase.values()),
        "kilos": round(sum(i["kilos"] or 0 for i in items), 3),
        "sin_kilos": sorted({i["codigo"] for i in items if i["kilos"] is None}),
    }
    return items, totales


@bp.route("/movimientos/<int:movimiento_id>")
def movimiento_detalle(movimiento_id):
    m = _movimiento_o_404(movimiento_id)
    items, totales = items_de_movimiento(movimiento_id)
    usados = pl.pallets_de_movimiento(movimiento_id)
    armados = []
    if m["tipo"] == "INGRESO":
        armados = pl.pallets(ids=[r[0] for r in get_db().execute(
            "SELECT id FROM pallets WHERE movimiento_id = ?", (movimiento_id,))], con_contenido=False)
    trasvaso = None
    if m["trasvaso_id"]:
        trasvaso = get_db().execute("SELECT id, orden FROM trasvasos WHERE id = ?", (m["trasvaso_id"],)).fetchone()
    return render_template("movimiento_detalle.html", m=m, items=items, totales=totales, tipos=TIPOS,
                           usados=usados, armados=sorted(armados, key=lambda p: p["id"]),
                           insumos=ins.de_movimiento(movimiento_id), nombre_insumo=ins.nombre_renglon,
                           envases=vacios.de_movimiento(movimiento_id), fotos=ft.de_movimiento(movimiento_id),
                           trasvaso=trasvaso, cambios=cr.cambios(movimiento_id=movimiento_id))


@bp.route("/movimientos/<int:movimiento_id>/anular", methods=["POST"])
def movimiento_anular(movimiento_id):
    m = _movimiento_o_404(movimiento_id)
    db = get_db()
    if m["anulado"]:
        flash("Ese remito ya estaba anulado.", "error")
        return redirect(url_for("stock.movimiento_detalle", movimiento_id=movimiento_id))
    if m["trasvaso_id"]:
        flash("Este remito es parte de un trasvaso: para anularlo, anulá el trasvaso completo.", "error")
        return redirect(url_for("trasvasos.trasvaso_detalle", trasvaso_id=m["trasvaso_id"]))
    motivo = _texto("motivo")
    if not motivo:
        flash("Escribí el motivo de la anulación.", "error")
        return redirect(url_for("stock.movimiento_detalle", movimiento_id=movimiento_id))
    if m["tipo"] == "INGRESO":
        problema = rm.problema_al_anular_ingreso(m)
        if problema:
            flash(problema, "error")
            return redirect(url_for("stock.movimiento_detalle", movimiento_id=movimiento_id))
    db.execute(
        "UPDATE movimientos SET anulado = 1, motivo_anulacion = ? WHERE id = ?", (motivo, movimiento_id)
    )
    vacios.anular(movimiento_id=movimiento_id)
    db.commit()
    flash("Remito anulado. El stock se recalculó.", "ok")
    return redirect(url_for("stock.movimiento_detalle", movimiento_id=movimiento_id))


# ------------------------------------------------------- corregir o borrar un remito


def _insumos_que_quedan(m):
    """Los insumos del remito que no están en el formulario (tipos desactivados): no se tocan al corregir."""
    activos = [i["id"] for i in ins.lista()]
    return [(r["insumo_id"], r["propietario"], r["cantidad"]) for r in get_db().execute(
        f"""SELECT insumo_id, propietario, cantidad FROM movimiento_insumos WHERE movimiento_id = ?
            AND insumo_id NOT IN ({','.join('?' * len(activos)) or 'NULL'})""",
        [m["id"], *activos],
    )]


def _envases_que_quedan(m):
    """Los envases del remito que no están en el formulario (tipos que ya no llevan la cuenta): no se tocan."""
    tipos = [e["id"] for e in vacios.tipos_para_cargar()]
    return [(r["envase_id"], r["estado"], abs(r["cantidad"])) for r in get_db().execute(
        f"""SELECT envase_id, estado, SUM(cantidad) AS cantidad FROM envase_movs
            WHERE movimiento_id = ? AND anulado = 0 AND envase_id NOT IN ({','.join('?' * len(tipos)) or 'NULL'})
            GROUP BY envase_id, estado""",
        [m["id"], *tipos],
    )]


def _datos_para_editar(m):
    """El formulario de un remito guardado: (form, renglones, textos de insumos, textos de envases)."""
    db = get_db()
    form = {"empresa_id": m["empresa_id"], "fecha": m["fecha"], "remito": m["remito"],
            "observaciones": m["observaciones"] or "", **{c: m[c] or "" for c in CAMPOS_TRANSPORTE}}
    renglones = [
        {"producto_id": r["producto_id"], "lote_id": r["lote_id"], "lote": r["lote"],
         "vencimiento": r["vencimiento"] or "", "cantidad": formato_numero(r["cantidad"], 6)}
        for r in db.execute(
            """SELECT i.producto_id, i.lote_id, i.cantidad, l.lote, l.vencimiento FROM movimiento_items i
               JOIN lotes l ON l.id = i.lote_id WHERE i.movimiento_id = ? ORDER BY i.id""",
            (m["id"],),
        )
    ]
    textos_insumos = defaultdict(float)
    for r in ins.de_movimiento(m["id"]):
        campo = f"ins_{r['insumo_id']}_{r['propietario']}" if r["propietario"] else f"ins_{r['insumo_id']}"
        textos_insumos[campo] += r["cantidad"]
    textos_envases = {
        vacios.campo(r["envase_id"], r["estado"]): formato_numero(abs(r["cantidad"]), 6)
        for r in db.execute(
            """SELECT envase_id, estado, SUM(cantidad) AS cantidad FROM envase_movs
               WHERE movimiento_id = ? AND anulado = 0 GROUP BY envase_id, estado""",
            (m["id"],),
        ) if abs(r["cantidad"]) > 1e-9
    }
    return form, renglones, {k: formato_numero(v, 6) for k, v in textos_insumos.items()}, textos_envases


def _texto_pallets(creados, borrados):
    lineas = []
    if creados:
        lineas.append(("Se armó el pallet " if len(creados) == 1 else "Se armaron los pallets ")
                      + ", ".join(pl.codigo_pallet(p) for p in creados))
    if borrados:
        lineas.append(("Se borró el pallet " if len(borrados) == 1 else "Se borraron los pallets ")
                      + ", ".join(pl.codigo_pallet(p) for p in borrados) + " (ya no hacía falta)")
    return lineas


@bp.route("/movimientos/<int:movimiento_id>/editar", methods=["GET", "POST"])
def movimiento_editar(movimiento_id):
    m = _movimiento_o_404(movimiento_id)
    if m["trasvaso_id"]:
        flash("Este remito es parte de un trasvaso: se corrige desde el trasvaso.", "aviso")
        return redirect(url_for("trasvasos.trasvaso_detalle", trasvaso_id=m["trasvaso_id"]))
    if m["anulado"]:
        flash("Este remito está anulado: no se puede editar. Si se cargó por error, podés borrarlo.", "error")
        return redirect(url_for("stock.movimiento_detalle", movimiento_id=movimiento_id))
    tipo = m["tipo"]
    if request.method == "GET":
        form, renglones, textos_insumos, textos_envases = _datos_para_editar(m)
        return _form_movimiento(tipo, form, renglones, False, textos_insumos, textos_envases, editando=m)

    d, errores, mostrar_repetido = _leer_remito(tipo, m)
    if errores:
        return _mostrar_de_nuevo(tipo, d, errores, mostrar_repetido, m)
    db = get_db()
    antes = cr.foto(movimiento_id)
    lotes_viejos = cr.lotes_de([movimiento_id])
    if tipo == "INGRESO":
        existentes = {l["id"] for l in (buscar_lote(pid, lote) for (pid, _), (lote, _) in d["vencimientos"].items()) if l}
    else:
        existentes = {lid for _, lid in d["items"]}
    estado = cr.Estado(lotes_viejos | existentes)
    try:
        t = {c: d["transporte"].get(c) or None for c in CAMPOS_TRANSPORTE}
        db.execute(
            f"""UPDATE movimientos SET fecha = ?, remito = ?, observaciones = ?, {", ".join(c + " = ?" for c in CAMPOS_TRANSPORTE)}
                WHERE id = ?""",
            (d["fecha"], d["remito"], d["observaciones"] or None, *(t[c] for c in CAMPOS_TRANSPORTE), movimiento_id),
        )
        if tipo == "INGRESO":
            lineas = rm.lineas_de_ingreso(d["items"], d["vencimientos"], corrigiendo=movimiento_id)
        else:
            lineas = [(pid, lid, cant) for (pid, lid), cant in d["items"].items()]
        errores, creados, borrados = cr.aplicar_items(m, lineas)
        # Insumos y envases: se reemplazan por lo del formulario (los de tipos que no se muestran quedan).
        activos = [i["id"] for i in ins.lista()]
        if activos:
            db.execute(f"DELETE FROM movimiento_insumos WHERE movimiento_id = ? AND insumo_id IN ({','.join('?' * len(activos))})",
                       [movimiento_id, *activos])
        db.executemany(
            "INSERT INTO movimiento_insumos (movimiento_id, insumo_id, propietario, cantidad) VALUES (?,?,?,?)",
            [(movimiento_id, iid, propietario, cant) for iid, propietario, cant in d["insumos"]],
        )
        tipos_envase = [e["id"] for e in vacios.tipos_para_cargar()]
        if tipos_envase:
            db.execute(f"DELETE FROM envase_movs WHERE movimiento_id = ? AND envase_id IN ({','.join('?' * len(tipos_envase))})",
                       [movimiento_id, *tipos_envase])
        db.execute("UPDATE envase_movs SET fecha = ? WHERE movimiento_id = ?", (d["fecha"], movimiento_id))
        vacios.registrar(m["empresa_id"], d["fecha"], tipo, d["envases"], movimiento_id=movimiento_id)
        if not errores:
            errores = estado.problemas({lid for _, lid, _ in lineas}, fecha=d["fecha"] if tipo == "INGRESO" else None)
        if errores:
            db.rollback()
            return _mostrar_de_nuevo(tipo, d, errores, mostrar_repetido, m)
        cr.limpiar_lotes(lotes_viejos)
        cantidad_fotos = ft.guardar(movimiento_id, d["fotos"])
        cambios = cr.diferencias(antes, cr.foto(movimiento_id)) + _texto_pallets(creados, borrados)
        if cambios:
            cr.anotar_remito("EDITADO", cr.foto(movimiento_id), "\n".join(cambios))
        db.commit()
    except Exception:
        db.rollback()
        raise
    if not cambios:
        flash("No había nada para cambiar.", "ok")
    else:
        mensaje = "Remito corregido."
        if cantidad_fotos:
            mensaje += " Con 1 foto nueva." if cantidad_fotos == 1 else f" Con {cantidad_fotos} fotos nuevas."
        flash(mensaje, "ok")
        _avisar_pallets(creados, borrados)
        _avisar_negativos(m["empresa_id"], lote_ids=lotes_viejos | {lid for _, lid, _ in lineas})
    return redirect(url_for("stock.movimiento_detalle", movimiento_id=movimiento_id))


@bp.route("/movimientos/<int:movimiento_id>/borrar", methods=["GET", "POST"])
def movimiento_borrar(movimiento_id):
    m = _movimiento_o_404(movimiento_id)
    if m["trasvaso_id"]:
        flash("Este remito es parte de un trasvaso: para borrarlo, borrá el trasvaso completo.", "aviso")
        return redirect(url_for("trasvasos.trasvaso_detalle", trasvaso_id=m["trasvaso_id"]))
    problema = cr.problema_al_borrar(m)
    if request.method == "GET" or problema:
        if problema and request.method == "POST":
            flash(problema, "error")
        items, totales = items_de_movimiento(movimiento_id)
        return render_template("movimiento_borrar.html", m=m, tipos=TIPOS, items=items, totales=totales,
                               problema=problema, fotos=ft.de_movimiento(movimiento_id),
                               armados=pl.pallets(ids=[r[0] for r in get_db().execute(
                                   "SELECT id FROM pallets WHERE movimiento_id = ?", (movimiento_id,))],
                                   con_contenido=False))
    db = get_db()
    try:
        cr.anotar_remito("BORRADO", cr.foto(movimiento_id), cr.texto_remito(cr.foto(movimiento_id)))
        archivos = cr.borrar_movimiento(m)
        db.commit()
    except Exception:
        db.rollback()
        raise
    cr.archivar_fotos(archivos)
    flash(f"{TIPOS[m['tipo']]} borrado (remito {m['remito']} del {formato_fecha(m['fecha'])}). "
          "Queda anotado en Movimientos → Cambios y borrados.", "ok")
    return redirect(url_for("stock.movimientos", empresa_id=m["empresa_id"]))


@bp.route("/movimientos/cambios")
def movimientos_cambios():
    empresa_id = _int_o_none(request.args.get("empresa_id"))
    accion = request.args.get("accion") if request.args.get("accion") in ("EDITADO", "BORRADO") else None
    return render_template("cambios.html", cambios=cr.cambios(empresa_id=empresa_id, accion=accion),
                           empresas=_empresas(), empresa_id=empresa_id, accion=accion, tipos=TIPOS)


# -------------------------------------------------------------------- fotos


def _volver(por_defecto):
    """A dónde volver después de agregar o quitar fotos (el trasvaso muestra las fotos de su ingreso)."""
    destino = request.form.get("volver") or ""
    if destino.startswith("/") and not destino.startswith("//"):
        return destino
    return por_defecto


@bp.route("/movimientos/<int:movimiento_id>/fotos", methods=["POST"])
def movimiento_fotos(movimiento_id):
    _movimiento_o_404(movimiento_id)
    subidas, errores = ft.leer_subidas(request.files.getlist("fotos"))
    for e in errores:
        flash(e, "error")
    if not subidas and not errores:
        flash("Elegí al menos una foto.", "error")
    if subidas:
        cantidad = ft.guardar(movimiento_id, subidas)
        get_db().commit()
        flash("Se agregó 1 foto." if cantidad == 1 else f"Se agregaron {cantidad} fotos.", "ok")
    return redirect(_volver(url_for("stock.movimiento_detalle", movimiento_id=movimiento_id) + "#fotos"))


@bp.route("/fotos/<int:foto_id>")
def foto(foto_id):
    fila = ft.buscar(foto_id)
    if fila is None:
        abort(404)
    return send_from_directory(current_app.config["FOTOS"], fila["archivo"], mimetype=ft.tipo_mime(fila))


@bp.route("/fotos/<int:foto_id>/borrar", methods=["POST"])
def foto_borrar(foto_id):
    fila = ft.buscar(foto_id)
    if fila is None:
        abort(404)
    ft.borrar(fila)
    flash("Foto quitada.", "ok")
    return redirect(_volver(url_for("stock.movimiento_detalle", movimiento_id=fila["movimiento_id"]) + "#fotos"))


# ----------------------------------------------------------------- insumos


@bp.route("/api/insumos")
def api_insumos():
    """Saldo de pallets (y otros insumos) del cliente en el depósito: {insumo_id: saldo}."""
    empresa_id = _int_o_none(request.args.get("empresa_id"))
    excluir = _int_o_none(request.args.get("excluir"))
    saldos = ins.saldos_cliente(empresa_id, excluir_movimiento=excluir) if empresa_id else {}
    return jsonify({str(iid): saldo for (_, iid), saldo in saldos.items()})


@bp.route("/insumos", methods=["GET", "POST"])
def insumos_lista():
    db = get_db()
    if request.method == "POST":
        nombre, unidad = _texto("nombre"), _texto("unidad") or "unidades"
        if not nombre:
            flash("Escribí el nombre del insumo.", "error")
        elif db.execute("SELECT 1 FROM insumos WHERE nombre = ?", (nombre,)).fetchone():
            flash(f"'{nombre}' ya está en la lista.", "error")
        else:
            db.execute("INSERT INTO insumos (nombre, unidad, con_propietario) VALUES (?,?,?)",
                       (nombre, unidad, 1 if request.form.get("con_propietario") else 0))
            db.commit()
            flash(f"Se agregó '{nombre}'.", "ok")
        return redirect(url_for("stock.insumos_lista"))
    filas = db.execute(
        """SELECT s.*, (SELECT COUNT(*) FROM movimiento_insumos mi WHERE mi.insumo_id = s.id) AS usos
           FROM insumos s ORDER BY s.activo DESC, s.con_propietario DESC, s.nombre"""
    ).fetchall()
    return render_template("insumos.html", insumos=filas, saldos=ins.saldos_por_empresa())


@bp.route("/insumos/<int:insumo_id>", methods=["POST"])
def insumo_editar(insumo_id):
    db = get_db()
    insumo = db.execute("SELECT * FROM insumos WHERE id = ?", (insumo_id,)).fetchone()
    if insumo is None:
        abort(404)
    if request.form.get("accion") == "activar":
        db.execute("UPDATE insumos SET activo = 1 - activo WHERE id = ?", (insumo_id,))
        db.commit()
        return redirect(url_for("stock.insumos_lista"))
    nombre, unidad = _texto("nombre"), _texto("unidad") or insumo["unidad"]
    if not nombre:
        flash("El nombre no puede quedar vacío.", "error")
    elif db.execute("SELECT 1 FROM insumos WHERE nombre = ? AND id <> ?", (nombre, insumo_id)).fetchone():
        flash(f"Ya existe un insumo llamado '{nombre}'.", "error")
    else:
        db.execute("UPDATE insumos SET nombre = ?, unidad = ? WHERE id = ?", (nombre, unidad, insumo_id))
        db.commit()
        flash("Insumo actualizado.", "ok")
    return redirect(url_for("stock.insumos_lista"))


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
        ("Empresa Ejemplo SA", "ABC-001", "Producto de ejemplo x 25kg", "Bolsas", 40, 25, "L2026-15", "31/12/2027",
         120),
        ("Empresa Ejemplo SA", "ABC-001", "Producto de ejemplo x 25kg", "Bolsas", 40, 25, "L2026-22", "30/06/2028",
         40),
        ("Otra Empresa SRL", "XY-10", "Solvente x 200 L", "Tambores de chapa", 4, 180, "", "", 15),
    ]
    archivo = armar_excel("Conteo", ENCABEZADOS_PLANTILLA, ejemplo)
    return send_file(
        archivo, as_attachment=True, download_name="plantilla_conteo_stock.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
