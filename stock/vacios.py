"""Envases vacíos y sucios del depósito: tambores, cisternas, bidones...

Son del depósito, no mercadería de los clientes. En los trasvasos se usan envases vacíos para envasar
y, cuando se vacían envases que estaban en el depósito, quedan envases sucios. Se cuentan por tipo de
envase (los que tienen marcado "vacíos y sucios" en Tipos de envase). El saldo es la suma de los
movimientos que no están anulados y nunca puede quedar negativo.
"""
from collections import defaultdict

from .db import get_db
from .formato import formato_numero
from .servicios import parse_numero

ESTADOS = {"VACIO": "vacíos", "SUCIO": "sucios"}
EPS = 1e-6

# Lo que se anota a mano en la página de envases: (texto, [(estado, signo)]). Los conteos fijan el saldo.
ACCIONES = {
    "ENTRAN_VACIOS": ("Entran vacíos (compra, devolución…)", [("VACIO", 1)]),
    "LAVADO": ("Se lavan sucios (pasan a vacíos)", [("SUCIO", -1), ("VACIO", 1)]),
    "SALEN_SUCIOS": ("Salen sucios (venta, descarte, devolución…)", [("SUCIO", -1)]),
    "SALEN_VACIOS": ("Salen vacíos (venta, préstamo…)", [("VACIO", -1)]),
    "ENTRAN_SUCIOS": ("Entran sucios (de afuera)", [("SUCIO", 1)]),
    "CONTEO_VACIOS": ("Conteo: hay esta cantidad de vacíos", [("VACIO", 0)]),
    "CONTEO_SUCIOS": ("Conteo: hay esta cantidad de sucios", [("SUCIO", 0)]),
}
MOTIVOS = {"TRASVASO": "Trasvaso", "CONTEO": "Conteo",
           **{k: v[0].split(" (")[0] for k, v in ACCIONES.items() if not k.startswith("CONTEO")}}


def _n(cantidad):
    return formato_numero(cantidad)


def palabras(nombre, estado):
    """('los', 'vacíos') o ('las', 'vacías')...: cisternas, bolsas y cajas van en femenino; tambores y bidones no."""
    primera = (nombre or "").strip().split(" ")[0].lower()
    femenino = primera.endswith(("a", "as"))
    raiz = "vací" if estado == "VACIO" else "suci"
    return ("las", raiz + "as") if femenino else ("los", raiz + "os")


def saldos():
    """{(envase_id, estado): saldo}."""
    filas = get_db().execute(
        "SELECT envase_id, estado, SUM(cantidad) AS saldo FROM envase_movs WHERE anulado = 0 GROUP BY envase_id, estado"
    ).fetchall()
    return {(r["envase_id"], r["estado"]): round(r["saldo"], 6) for r in filas}


def tipos():
    """Los tipos de envase que llevan vacíos y sucios, más los que tengan saldo aunque ya no los lleven.

    Cada uno con su saldo de vacíos y de sucios.
    """
    s = saldos()
    con_saldo = {eid for (eid, _), saldo in s.items() if abs(saldo) > EPS}
    filas = get_db().execute("SELECT * FROM envases ORDER BY nombre").fetchall()
    return [
        {"id": e["id"], "nombre": e["nombre"], "lleva_vacios": e["lleva_vacios"],
         "vacios": s.get((e["id"], "VACIO"), 0), "sucios": s.get((e["id"], "SUCIO"), 0)}
        for e in filas
        if (e["lleva_vacios"] and e["activo"]) or e["id"] in con_saldo
    ]


def campo(envase_id, estado):
    return f"env_{envase_id}_{estado}"


def leer_formulario(form, lista):
    """Lee del formulario del trasvaso los vacíos que se usan y los sucios que quedan.

    Devuelve (renglones, errores, textos): renglones = [(envase_id, estado, cantidad)].
    """
    renglones, errores, textos = [], [], {}
    for e in lista:
        for estado in ESTADOS:
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


def _nombres():
    return {r["id"]: r["nombre"] for r in get_db().execute("SELECT id, nombre FROM envases")}


def chequear(renglones):
    """Los vacíos que se usan no pueden ser más de los que hay."""
    s, nombres, errores = saldos(), _nombres(), []
    for envase_id, estado, cantidad in renglones:
        hay = s.get((envase_id, "VACIO"), 0)
        if estado == "VACIO" and cantidad > hay + EPS:
            errores.append(
                f"{nombres[envase_id]} {palabras(nombres[envase_id], 'VACIO')[1]}: en el depósito hay {_n(hay)} "
                f"y querés usar {_n(cantidad)}. "
                "Si el número no está al día, corregilo en Trasvasos → Envases vacíos y sucios (con un conteo)."
            )
    return errores


def registrar_trasvaso(trasvaso_id, fecha, renglones):
    """Los vacíos que se usan salen y los sucios que quedan entran. No hace commit."""
    get_db().executemany(
        """INSERT INTO envase_movs (envase_id, estado, cantidad, motivo, fecha, trasvaso_id)
           VALUES (?,?,?,'TRASVASO',?,?)""",
        [(eid, estado, -cant if estado == "VACIO" else cant, fecha, trasvaso_id)
         for eid, estado, cant in renglones],
    )


def de_trasvaso(trasvaso_id):
    """[{nombre, vacios, sucios}]: los vacíos que se usaron y los sucios que quedaron en un trasvaso."""
    filas = get_db().execute(
        """SELECT v.nombre,
                  SUM(CASE WHEN m.estado = 'VACIO' THEN -m.cantidad ELSE 0 END) AS vacios,
                  SUM(CASE WHEN m.estado = 'SUCIO' THEN m.cantidad ELSE 0 END) AS sucios
           FROM envase_movs m JOIN envases v ON v.id = m.envase_id
           WHERE m.trasvaso_id = ? GROUP BY v.id ORDER BY v.nombre""",
        (trasvaso_id,),
    ).fetchall()
    return [dict(r) for r in filas]


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


def problemas_al_anular(trasvaso_id):
    """Al anular un trasvaso vuelven los vacíos y se van los sucios: los sucios no pueden quedar negativos."""
    s, problemas = saldos(), []
    for r in get_db().execute(
        """SELECT m.envase_id, v.nombre, SUM(m.cantidad) AS cantidad FROM envase_movs m
           JOIN envases v ON v.id = m.envase_id
           WHERE m.trasvaso_id = ? AND m.estado = 'SUCIO' AND m.anulado = 0 GROUP BY m.envase_id""",
        (trasvaso_id,),
    ):
        hay = s.get((r["envase_id"], "SUCIO"), 0)
        if hay - r["cantidad"] < -EPS:
            articulo, sucios = palabras(r["nombre"], "SUCIO")
            problemas.append(
                f"{articulo} {r['nombre'].lower()} {sucios} quedarían en negativo (hay {_n(hay)} y este trasvaso dejó "
                f"{_n(r['cantidad'])}; puede que ya se hayan lavado o sacado)"
            )
    return problemas


def anular_trasvaso(trasvaso_id):
    """No hace commit."""
    get_db().execute("UPDATE envase_movs SET anulado = 1 WHERE trasvaso_id = ?", (trasvaso_id,))


def registrar_accion(envase_id, accion, cantidad, fecha, observaciones=""):
    """Anota un movimiento hecho a mano (compra, lavado, venta, conteo...). Devuelve un error o None.

    No hace commit.
    """
    if accion not in ACCIONES:
        return "Elegí qué pasó con los envases."
    envase = get_db().execute("SELECT * FROM envases WHERE id = ?", (envase_id,)).fetchone()
    if envase is None:
        return "Elegí el tipo de envase."
    if cantidad is None or cantidad < 0 or (cantidad <= EPS and not accion.startswith("CONTEO")):
        return "La cantidad tiene que ser un número mayor a cero."
    s = saldos()
    nombre = envase["nombre"].lower()
    filas = []
    if accion.startswith("CONTEO"):
        estado = ACCIONES[accion][1][0][0]
        diferencia = round(cantidad - s.get((envase_id, estado), 0), 6)
        if abs(diferencia) <= EPS:
            return f"Ya figuran {_n(cantidad)} {nombre} {palabras(nombre, estado)[1]}: no hay nada que corregir."
        filas.append((estado, diferencia, "CONTEO"))
    else:
        for estado, signo in ACCIONES[accion][1]:
            hay = s.get((envase_id, estado), 0)
            if signo < 0 and cantidad > hay + EPS:
                return (f"En el depósito hay {_n(hay)} {nombre} {palabras(nombre, estado)[1]} "
                        f"y querés sacar {_n(cantidad)}. "
                        "Si el número no está al día, primero cargá un conteo.")
            filas.append((estado, signo * cantidad, accion))
    db = get_db()
    grupo = None
    for estado, cant, motivo in filas:
        cur = db.execute(
            """INSERT INTO envase_movs (envase_id, estado, cantidad, motivo, fecha, grupo, observaciones)
               VALUES (?,?,?,?,?,?,?)""",
            (envase_id, estado, cant, motivo, fecha, grupo, observaciones or None),
        )
        if grupo is None:
            grupo = cur.lastrowid
            db.execute("UPDATE envase_movs SET grupo = ? WHERE id = ?", (grupo, grupo))
    return None


def historial(limite=300):
    """Los últimos movimientos de envases, los más nuevos primero (un renglón por envase y anotación)."""
    return get_db().execute(
        """SELECT MIN(m.fecha) AS fecha, v.nombre, m.motivo, m.trasvaso_id, t.orden, MAX(m.observaciones) AS observaciones,
                  MAX(m.anulado) AS anulado,
                  SUM(CASE WHEN m.estado = 'VACIO' THEN m.cantidad ELSE 0 END) AS vacios,
                  SUM(CASE WHEN m.estado = 'SUCIO' THEN m.cantidad ELSE 0 END) AS sucios
           FROM envase_movs m JOIN envases v ON v.id = m.envase_id
           LEFT JOIN trasvasos t ON t.id = m.trasvaso_id
           GROUP BY COALESCE('g' || m.grupo, 't' || m.trasvaso_id || '-' || m.envase_id, 'm' || m.id)
           ORDER BY MAX(m.id) DESC LIMIT ?""",
        (limite,),
    ).fetchall()
