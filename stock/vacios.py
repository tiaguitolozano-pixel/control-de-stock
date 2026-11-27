"""Envases vacíos y sucios de los clientes: tambores, cisternas, bidones...

Son de cada cliente (empresa), no del depósito. Los vacíos entran con los ingresos (el cliente los manda
para los trasvasos); cada trasvaso usa vacíos de la empresa y, si se vacían envases que estaban en el
depósito, le deja sucios; los egresos devuelven vacíos o sucios. A mano se anotan los lavados, los
descartes y los conteos. Se cuentan por empresa y tipo de envase (los tipos que tienen marcado "vacíos y
sucios" en Tipos de envase). El saldo es la suma de los movimientos que no están anulados y nunca puede
quedar negativo.
"""
from collections import defaultdict

from .db import get_db
from .formato import formato_numero
from .servicios import parse_numero

ESTADOS = {"VACIO": "vacíos", "SUCIO": "sucios"}
EPS = 1e-6

# Los casilleros de cada formulario: (estado, texto, signo). El signo +1 suma al saldo del cliente y -1 resta.
COLUMNAS = {
    "INGRESO": [("VACIO", "Vacíos que entran", 1)],
    "EGRESO": [("VACIO", "Vacíos que salen", -1), ("SUCIO", "Sucios que salen", -1)],
    "TRASVASO": [("VACIO", "Vacíos que se usan", -1), ("SUCIO", "Sucios que quedan", 1)],
}

# Lo que se anota a mano en la página de envases: (texto, [(estado, signo)]). Los conteos fijan el saldo.
ACCIONES = {
    "LAVADO": ("Se lavan sucios (pasan a vacíos)", [("SUCIO", -1), ("VACIO", 1)]),
    "SALEN_SUCIOS": ("Salen sucios (devolución sin remito, descarte…)", [("SUCIO", -1)]),
    "SALEN_VACIOS": ("Salen vacíos (devolución sin remito…)", [("VACIO", -1)]),
    "ENTRAN_VACIOS": ("Entran vacíos del cliente (sin remito)", [("VACIO", 1)]),
    "ENTRAN_SUCIOS": ("Entran sucios (sin remito)", [("SUCIO", 1)]),
    "CONTEO_VACIOS": ("Conteo: hay esta cantidad de vacíos", [("VACIO", 0)]),
    "CONTEO_SUCIOS": ("Conteo: hay esta cantidad de sucios", [("SUCIO", 0)]),
}
MOTIVOS = {"TRASVASO": "Trasvaso", "CONTEO": "Conteo", "INGRESO": "Ingreso", "EGRESO": "Egreso",
           **{k: v[0].split(" (")[0] for k, v in ACCIONES.items() if not k.startswith("CONTEO")}}


def _n(cantidad):
    return formato_numero(cantidad)


def palabras(nombre, estado):
    """('los', 'vacíos') o ('las', 'vacías')...: cisternas, bolsas y cajas van en femenino; tambores y bidones no."""
    primera = (nombre or "").strip().split(" ")[0].lower()
    femenino = primera.endswith(("a", "as"))
    raiz = "vací" if estado == "VACIO" else "suci"
    return ("las", raiz + "as") if femenino else ("los", raiz + "os")


def campo(envase_id, estado):
    return f"env_{envase_id}_{estado}"


def _nombres():
    return {r["id"]: r["nombre"] for r in get_db().execute("SELECT id, nombre FROM envases")}


# ----------------------------------------------------------------- saldos


def saldos(empresa_id=None, excluir_movimiento=None, excluir_trasvaso=None):
    """{(empresa_id, envase_id, estado): saldo}, de una empresa o de todas.

    excluir_movimiento / excluir_trasvaso: lo que no se cuenta (el remito o trasvaso que se está corrigiendo).
    """
    sql = """SELECT empresa_id, envase_id, estado, SUM(cantidad) AS saldo FROM envase_movs
             WHERE anulado = 0 AND empresa_id IS NOT NULL"""
    params = []
    if empresa_id:
        sql += " AND empresa_id = ?"
        params.append(empresa_id)
    if excluir_movimiento:
        sql += " AND COALESCE(movimiento_id, 0) <> ?"
        params.append(excluir_movimiento)
    if excluir_trasvaso:
        sql += " AND COALESCE(trasvaso_id, 0) <> ?"
        params.append(excluir_trasvaso)
    filas = get_db().execute(sql + " GROUP BY empresa_id, envase_id, estado", params).fetchall()
    return {(r["empresa_id"], r["envase_id"], r["estado"]): round(r["saldo"], 6) for r in filas}


def saldos_de(empresa_id, excluir_movimiento=None, excluir_trasvaso=None):
    """{envase_id: {'VACIO': saldo, 'SUCIO': saldo}} de una empresa (para mostrar en los formularios)."""
    res = defaultdict(dict)
    for (_, envase_id, estado), saldo in saldos(empresa_id, excluir_movimiento, excluir_trasvaso).items():
        res[envase_id][estado] = saldo
    return dict(res)


def tipos_para_cargar():
    """Los tipos de envase que llevan la cuenta de vacíos y sucios: son los casilleros de los formularios."""
    return get_db().execute(
        "SELECT id, nombre FROM envases WHERE lleva_vacios = 1 AND activo = 1 ORDER BY nombre"
    ).fetchall()


def resumen(empresa_id=None):
    """Para mostrar: los vacíos y sucios de cada empresa por tipo de envase.

    Con una empresa: todos los tipos que llevan la cuenta (aunque estén en cero) y los que tengan movimientos.
    Sin empresa: las empresas y tipos que tuvieron movimientos.
    """
    db = get_db()
    s = saldos(empresa_id)
    sql = "SELECT DISTINCT empresa_id, envase_id FROM envase_movs WHERE anulado = 0 AND empresa_id IS NOT NULL"
    pares = {(r[0], r[1]) for r in db.execute(sql + (" AND empresa_id = ?" if empresa_id else ""),
                                              [empresa_id] if empresa_id else [])}
    if empresa_id:
        pares |= {(empresa_id, r["id"]) for r in tipos_para_cargar()}
    envases = _nombres()
    empresas = {r["id"]: r["nombre"] for r in db.execute("SELECT id, nombre FROM empresas")}
    filas = [
        {"empresa_id": emp, "empresa": empresas.get(emp, "?"), "envase_id": eid, "nombre": envases[eid],
         "vacios": s.get((emp, eid, "VACIO"), 0), "sucios": s.get((emp, eid, "SUCIO"), 0)}
        for emp, eid in pares
    ]
    return sorted(filas, key=lambda r: (r["empresa"].lower(), r["nombre"].lower()))


def totales_por_tipo(empresa_id=None):
    """[(texto, saldo)] de los envases de clientes que hay en el depósito (para el resumen del stock)."""
    suma = defaultdict(float)
    for (_, envase_id, estado), saldo in saldos(empresa_id).items():
        suma[(envase_id, estado)] += saldo
    nombres = _nombres()
    return sorted(
        (f"{nombres[eid]} {palabras(nombres[eid], estado)[1]}", round(saldo, 6))
        for (eid, estado), saldo in suma.items() if abs(saldo) > EPS
    )


# ------------------------------------------------- formularios: remitos y trasvasos


def leer_formulario(form, modo):
    """Lee los casilleros de envases de un formulario (modo INGRESO, EGRESO o TRASVASO).

    Devuelve (renglones, errores, textos): renglones = [(envase_id, estado, cantidad)], con la cantidad
    siempre positiva; textos guarda lo escrito, para volver a mostrar el formulario si hay errores.
    """
    renglones, errores, textos = [], [], {}
    for e in tipos_para_cargar():
        for estado, _, _ in COLUMNAS[modo]:
            nombre = campo(e["id"], estado)
            texto = (form.get(nombre) or "").strip()
            if not texto:
                continue
            textos[nombre] = texto
            cantidad = parse_numero(texto)
            if cantidad is None or cantidad < 0:
                errores.append(f"Envases: la cantidad de {e['nombre'].lower()} {palabras(e['nombre'], estado)[1]} "
                               "no es un número válido.")
            elif cantidad > EPS:
                renglones.append((e["id"], estado, cantidad))
    return renglones, errores, textos


def _con_signo(modo, renglones):
    signos = {estado: signo for estado, _, signo in COLUMNAS[modo]}
    return [(eid, estado, signos[estado] * cantidad) for eid, estado, cantidad in renglones]


def chequear(empresa_id, modo, renglones):
    """Lo que sale (los vacíos de un trasvaso, los vacíos y sucios de un egreso) no puede ser más de lo que
    la empresa tiene en el depósito."""
    s, nombres, errores = saldos(empresa_id), _nombres(), []
    salen = defaultdict(float)
    for eid, estado, cantidad in _con_signo(modo, renglones):
        if cantidad < 0:
            salen[(eid, estado)] -= cantidad
    for (eid, estado), cantidad in salen.items():
        hay = s.get((empresa_id, eid, estado), 0)
        if cantidad > hay + EPS:
            nombre = nombres[eid]
            conteo = "con un conteo en Trasvasos → Envases vacíos y sucios"
            ayuda = (f"Si el cliente mandó vacíos que no están cargados, cargalos primero con un ingreso "
                     f"(en + Ingreso, apartado Envases vacíos del cliente), o corregí el número {conteo}."
                     if estado == "VACIO" else f"Si el número no está al día, corregilo {conteo}.")
            errores.append(
                f"{nombre} {palabras(nombre, estado)[1]}: esta empresa tiene {_n(hay)} en el depósito y querés "
                f"{'usar' if modo == 'TRASVASO' else 'sacar'} {_n(cantidad)}. {ayuda}"
            )
    return errores


def problemas_al_cambiar(empresa_id, modo, renglones, movimiento_id=None, trasvaso_id=None):
    """Al corregir un remito o un trasvaso: con los envases nuevos, ningún saldo de la empresa puede quedar
    negativo (o más negativo de lo que ya estaba)."""
    actuales = saldos(empresa_id)
    sin_este = saldos(empresa_id, excluir_movimiento=movimiento_id, excluir_trasvaso=trasvaso_id)
    nuevos = defaultdict(float)
    for eid, estado, cantidad in _con_signo(modo, renglones):
        nuevos[(eid, estado)] += cantidad
    claves = set(nuevos) | {(e, st) for (_, e, st) in actuales} | {(e, st) for (_, e, st) in sin_este}
    nombres, errores = _nombres(), []
    for eid, estado in sorted(claves):
        hay = sin_este.get((empresa_id, eid, estado), 0)
        queda = round(hay + nuevos.get((eid, estado), 0), 6)
        if queda < -EPS and queda < actuales.get((empresa_id, eid, estado), 0) - EPS:
            nombre = nombres[eid]
            adjetivo = palabras(nombre, estado)[1]
            if nuevos.get((eid, estado), 0) < 0:
                errores.append(f"{nombre} {adjetivo}: sin contar este {'trasvaso' if trasvaso_id else 'remito'}, "
                               f"esta empresa tiene {_n(hay)} en el depósito y querés "
                               f"{'usar' if modo == 'TRASVASO' else 'sacar'} {_n(-nuevos[(eid, estado)])}.")
            else:
                errores.append(f"{nombre} {adjetivo}: con este cambio quedarían {_n(queda)} en el depósito, porque "
                               "ya se usaron o salieron.")
    return errores


def registrar(empresa_id, fecha, modo, renglones, trasvaso_id=None, movimiento_id=None):
    """Anota los envases de un trasvaso o de un remito (ingreso o egreso). No hace commit."""
    get_db().executemany(
        """INSERT INTO envase_movs (empresa_id, envase_id, estado, cantidad, motivo, fecha, trasvaso_id, movimiento_id)
           VALUES (?,?,?,?,?,?,?,?)""",
        [(empresa_id, eid, estado, cantidad, modo, fecha, trasvaso_id, movimiento_id)
         for eid, estado, cantidad in _con_signo(modo, renglones)],
    )


def _por_tipo(condicion, valor, signo_vacios):
    filas = get_db().execute(
        f"""SELECT v.nombre,
                   {signo_vacios} * SUM(CASE WHEN m.estado = 'VACIO' THEN m.cantidad ELSE 0 END) AS vacios,
                   ABS(SUM(CASE WHEN m.estado = 'SUCIO' THEN m.cantidad ELSE 0 END)) AS sucios
            FROM envase_movs m JOIN envases v ON v.id = m.envase_id
            WHERE {condicion} = ? GROUP BY v.id ORDER BY v.nombre""",
        (valor,),
    ).fetchall()
    return [dict(r) for r in filas]


def de_trasvaso(trasvaso_id):
    """[{nombre, vacios, sucios}]: los vacíos que se usaron y los sucios que quedaron en un trasvaso."""
    return _por_tipo("m.trasvaso_id", trasvaso_id, -1)


def de_movimiento(movimiento_id):
    """[{nombre, vacios, sucios}]: los envases que entraron (ingreso) o salieron (egreso) con un remito."""
    return [{**r, "vacios": abs(r["vacios"])} for r in _por_tipo("m.movimiento_id", movimiento_id, 1)]


def de_trasvasos(trasvaso_ids):
    """{trasvaso_id: [{nombre, vacios, sucios}]} para la lista de trasvasos."""
    ids = list(trasvaso_ids)
    res = defaultdict(list)
    if not ids:
        return res
    filas = get_db().execute(
        f"""SELECT m.trasvaso_id, v.nombre,
                   SUM(CASE WHEN m.estado = 'VACIO' THEN -m.cantidad ELSE 0 END) AS vacios,
                   SUM(CASE WHEN m.estado = 'SUCIO' THEN m.cantidad ELSE 0 END) AS sucios
            FROM envase_movs m JOIN envases v ON v.id = m.envase_id
            WHERE m.trasvaso_id IN ({','.join('?' * len(ids))})
            GROUP BY m.trasvaso_id, v.id ORDER BY v.nombre""",
        ids,
    ).fetchall()
    for r in filas:
        res[r["trasvaso_id"]].append(dict(r))
    return res


def problemas_al_anular(trasvaso_id=None, movimiento_id=None):
    """Al anular un trasvaso o un remito se deshacen sus envases: ningún saldo puede quedar negativo.

    Por ejemplo, los sucios que dejó un trasvaso ya lavados, o los vacíos de un ingreso ya usados en un trasvaso.
    """
    columna, valor = ("trasvaso_id", trasvaso_id) if trasvaso_id else ("movimiento_id", movimiento_id)
    filas = get_db().execute(
        f"""SELECT m.empresa_id, m.envase_id, m.estado, v.nombre, SUM(m.cantidad) AS cantidad
            FROM envase_movs m JOIN envases v ON v.id = m.envase_id
            WHERE m.{columna} = ? AND m.anulado = 0 GROUP BY m.empresa_id, m.envase_id, m.estado""",
        (valor,),
    ).fetchall()
    problemas = []
    for r in filas:
        if r["cantidad"] <= EPS:
            continue  # lo que había salido vuelve: nunca deja nada en negativo
        hay = saldos(r["empresa_id"]).get((r["empresa_id"], r["envase_id"], r["estado"]), 0)
        if hay - r["cantidad"] < -EPS:
            articulo, adjetivo = palabras(r["nombre"], r["estado"])
            origen = "este trasvaso dejó" if trasvaso_id else "este remito trajo"
            causa = "lavado o sacado" if r["estado"] == "SUCIO" else "usado en un trasvaso o sacado"
            problemas.append(
                f"{articulo} {r['nombre'].lower()} {adjetivo} de esta empresa quedarían en negativo (hay {_n(hay)} "
                f"y {origen} {_n(r['cantidad'])}; puede que ya se hayan {causa})"
            )
    return problemas


def anular(trasvaso_id=None, movimiento_id=None):
    """Deshace los envases de un trasvaso o de un remito. No hace commit."""
    columna, valor = ("trasvaso_id", trasvaso_id) if trasvaso_id else ("movimiento_id", movimiento_id)
    get_db().execute(f"UPDATE envase_movs SET anulado = 1 WHERE {columna} = ?", (valor,))


# ------------------------------------------------- página de envases (a mano)


def tipos_para_anotar():
    """Los tipos que se pueden elegir al anotar a mano: los que llevan la cuenta y los que tienen saldo."""
    con_saldo = {eid for (_, eid, _), saldo in saldos().items() if abs(saldo) > EPS}
    return [r for r in get_db().execute("SELECT id, nombre, lleva_vacios, activo FROM envases ORDER BY nombre")
            if (r["lleva_vacios"] and r["activo"]) or r["id"] in con_saldo]


def registrar_accion(empresa_id, envase_id, accion, cantidad, fecha, observaciones=""):
    """Anota un movimiento hecho a mano (lavado, devolución, descarte, conteo...). Devuelve un error o None.

    No hace commit.
    """
    db = get_db()
    if not empresa_id or db.execute("SELECT 1 FROM empresas WHERE id = ?", (empresa_id,)).fetchone() is None:
        return "Elegí la empresa (de quién son los envases)."
    if accion not in ACCIONES:
        return "Elegí qué pasó con los envases."
    envase = db.execute("SELECT * FROM envases WHERE id = ?", (envase_id,)).fetchone()
    if envase is None:
        return "Elegí el tipo de envase."
    if cantidad is None or cantidad < 0 or (cantidad <= EPS and not accion.startswith("CONTEO")):
        return "La cantidad tiene que ser un número mayor a cero."
    s = saldos(empresa_id)
    nombre = envase["nombre"].lower()
    filas = []
    if accion.startswith("CONTEO"):
        estado = ACCIONES[accion][1][0][0]
        diferencia = round(cantidad - s.get((empresa_id, envase_id, estado), 0), 6)
        if abs(diferencia) <= EPS:
            return f"Ya figuran {_n(cantidad)} {nombre} {palabras(nombre, estado)[1]}: no hay nada que corregir."
        filas.append((estado, diferencia, "CONTEO"))
    else:
        for estado, signo in ACCIONES[accion][1]:
            hay = s.get((empresa_id, envase_id, estado), 0)
            if signo < 0 and cantidad > hay + EPS:
                return (f"Esta empresa tiene {_n(hay)} {nombre} {palabras(nombre, estado)[1]} en el depósito "
                        f"y querés sacar {_n(cantidad)}. Si el número no está al día, primero cargá un conteo.")
            filas.append((estado, signo * cantidad, accion))
    grupo = None
    for estado, cant, motivo in filas:
        cur = db.execute(
            """INSERT INTO envase_movs (empresa_id, envase_id, estado, cantidad, motivo, fecha, grupo, observaciones)
               VALUES (?,?,?,?,?,?,?,?)""",
            (empresa_id, envase_id, estado, cant, motivo, fecha, grupo, observaciones or None),
        )
        if grupo is None:
            grupo = cur.lastrowid
            db.execute("UPDATE envase_movs SET grupo = ? WHERE id = ?", (grupo, grupo))
    return None


def historial(empresa_id=None, limite=300):
    """Los últimos movimientos de envases, los más nuevos primero (un renglón por envase y anotación)."""
    filtro, params = ("WHERE m.empresa_id = ?", [empresa_id]) if empresa_id else ("", [])
    return get_db().execute(
        f"""SELECT MIN(m.fecha) AS fecha, e.nombre AS empresa, v.nombre, m.motivo, m.trasvaso_id, t.orden,
                   m.movimiento_id, mv.remito, MAX(m.observaciones) AS observaciones, MAX(m.anulado) AS anulado,
                   SUM(CASE WHEN m.estado = 'VACIO' THEN m.cantidad ELSE 0 END) AS vacios,
                   SUM(CASE WHEN m.estado = 'SUCIO' THEN m.cantidad ELSE 0 END) AS sucios
            FROM envase_movs m JOIN envases v ON v.id = m.envase_id
            LEFT JOIN empresas e ON e.id = m.empresa_id
            LEFT JOIN trasvasos t ON t.id = m.trasvaso_id
            LEFT JOIN movimientos mv ON mv.id = m.movimiento_id
            {filtro}
            GROUP BY COALESCE('g' || m.grupo, 't' || m.trasvaso_id || '-' || m.envase_id,
                              'r' || m.movimiento_id || '-' || m.envase_id, 'm' || m.id)
            ORDER BY MAX(m.id) DESC LIMIT ?""",
        (*params, limite),
    ).fetchall()
