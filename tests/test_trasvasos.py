"""Pruebas de los trasvasos y del stock de envases vacíos y sucios (pedido del 26/11)."""
import sqlite3
from io import BytesIO

from openpyxl import load_workbook

from stock import create_app
from stock import insumos as ins
from stock.db import ESQUEMA, MIGRACIONES, get_db, stock_por_id
from test_kilos_insumos_fotos import JPG, PALLETS, producto_con_kilos
from test_stock import crear_empresa, movimiento

TAMBORES, CISTERNAS = 1, 2  # ids de los productos que arma `preparar`


def preparar(client):
    """Una empresa con el producto en tambores (180 kg, 4 por pallet) y en cisternas (900 kg, 1 por pallet)."""
    crear_empresa(client)
    producto_con_kilos(client, codigo="BG-T200", kilos="180", por_pallet="4", unidad="Tambores de chapa")
    producto_con_kilos(client, codigo="BG-C1000", kilos="900", por_pallet="1", unidad="Cisternas")


def envase(app, nombre):
    with app.app_context():
        return get_db().execute("SELECT id FROM envases WHERE nombre = ?", (nombre,)).fetchone()[0]


def saldo(app, nombre, estado):
    with app.app_context():
        r = get_db().execute(
            """SELECT COALESCE(SUM(m.cantidad), 0) FROM envase_movs m JOIN envases v ON v.id = m.envase_id
               WHERE v.nombre = ? AND m.estado = ? AND m.anulado = 0""",
            (nombre, estado),
        ).fetchone()
        return r[0]


def stock(app, producto_id):
    with app.app_context():
        return stock_por_id([producto_id])[producto_id]


def anotar(client, app, nombre, accion, cantidad, **extra):
    return client.post("/trasvasos/envases", data={
        "envase_id": envase(app, nombre), "accion": accion, "cantidad": str(cantidad), "fecha": "2026-11-01", **extra,
    }, follow_redirects=True)


def lote_id(app, producto_id, lote):
    with app.app_context():
        return get_db().execute("SELECT id FROM lotes WHERE producto_id = ? AND lote = ?",
                                (producto_id, lote)).fetchone()[0]


def trasvaso(client, app, destino, origen=None, envases=None, orden="4521", **extra):
    """destino: [(producto_id, cantidad, lote, vencimiento)]. origen (del depósito): [(producto_id, cantidad, lote_id)].
    envases: {(nombre del envase, 'VACIO'|'SUCIO'): cantidad}."""
    data = {"empresa_id": 1, "fecha": "2026-11-20", "orden": orden, "origen": "DEPOSITO" if origen else "AFUERA",
            "producto_id": [str(d[0]) for d in destino], "cantidad": [str(d[1]) for d in destino],
            "lote": [d[2] for d in destino], "vencimiento": [d[3] for d in destino]}
    if origen:
        data.update({"o_producto_id": [str(o[0]) for o in origen], "o_cantidad": [str(o[1]) for o in origen],
                     "o_lote_id": [str(o[2]) for o in origen]})
    for (nombre, estado), cantidad in (envases or {}).items():
        data[f"env_{envase(app, nombre)}_{estado}"] = str(cantidad)
    data.update(extra)
    return client.post("/trasvasos/nuevo", data=data, content_type="multipart/form-data")


def texto(r):
    return r.get_data(as_text=True)


# --------------------------------------------------------------- envases


def test_tambores_y_cisternas_llevan_vacios_y_sucios(app):
    with app.app_context():
        lleva = {r["nombre"]: r["lleva_vacios"] for r in get_db().execute("SELECT * FROM envases")}
    assert lleva["Tambores de chapa"] == lleva["Tambores de plástico"] == lleva["Cisternas"] == lleva["Bidones"] == 1
    assert lleva["Bolsas"] == lleva["Cajas"] == 0


def test_envases_vacios_y_sucios_a_mano(app, client):
    assert "Ningún tipo" not in texto(client.get("/trasvasos/envases"))
    r = anotar(client, app, "Tambores de chapa", "ENTRAN_VACIOS", 10, observaciones="Compra a Envases SA")
    assert "Listo, quedó anotado" in texto(r)
    assert saldo(app, "Tambores de chapa", "VACIO") == 10
    r = anotar(client, app, "Tambores de chapa", "SALEN_VACIOS", 15)
    assert "hay 10 tambores de chapa vacíos y querés sacar 15" in texto(r)
    anotar(client, app, "Tambores de chapa", "CONTEO_SUCIOS", 7)
    assert saldo(app, "Tambores de chapa", "SUCIO") == 7
    anotar(client, app, "Tambores de chapa", "LAVADO", 3)
    assert saldo(app, "Tambores de chapa", "SUCIO") == 4 and saldo(app, "Tambores de chapa", "VACIO") == 13
    r = anotar(client, app, "Tambores de chapa", "CONTEO_VACIOS", 13)
    assert "no hay nada que corregir" in texto(r)
    r = anotar(client, app, "Tambores de chapa", "SALEN_SUCIOS", 0)
    assert "mayor a cero" in texto(r)
    pagina = texto(client.get("/trasvasos/envases"))
    # El lavado es una sola anotación: salen 3 sucios y entran 3 vacíos.
    assert pagina.count("Se lavan sucios") == 2  # en la lista de opciones y en el historial
    assert "Compra a Envases SA" in pagina


def test_tipos_de_envase_eligen_si_llevan_vacios(app, client):
    preparar(client)
    bolsas = envase(app, "Bolsas")
    assert f"env_{bolsas}_VACIO" not in texto(client.get("/trasvasos/nuevo"))
    client.post(f"/envases/{bolsas}", data={"accion": "vacios"})
    assert f"env_{bolsas}_VACIO" in texto(client.get("/trasvasos/nuevo"))


# ------------------------------------------------------------- trasvasos


def test_isotanque_que_llega_del_puerto_a_tambores(app, client):
    preparar(client)
    anotar(client, app, "Tambores de chapa", "ENTRAN_VACIOS", 100)
    r = trasvaso(client, app, [(TAMBORES, "80", "L-BG1", "2028-01-31")],
                 envases={("Tambores de chapa", "VACIO"): 80}, kilos_origen="14.500",
                 contenedor="exfu 123456-7", tipo_contenedor="20' Isotanque (ISO tank)",
                 observaciones="Isotanque de butilglicol", crear_pallets="1")
    assert r.status_code == 302 and r.headers["Location"].endswith("/trasvasos/1")
    assert stock(app, TAMBORES) == 80
    assert saldo(app, "Tambores de chapa", "VACIO") == 20
    with app.app_context():
        db = get_db()
        movs = db.execute("SELECT tipo, remito, trasvaso_id, contenedor FROM movimientos").fetchall()
        assert [tuple(m) for m in movs] == [("INGRESO", "Orden 4521", 1, "EXFU1234567")]
        assert db.execute("SELECT COUNT(*) FROM pallets").fetchone()[0] == 20
    detalle = texto(client.get("/trasvasos/1"))
    assert "Orden 4521" in detalle and "Isotanque de butilglicol" in detalle and "EXFU1234567" in detalle
    assert "Patente camión" in detalle
    assert "14.400 kg" in detalle and "-100 kg" in detalle  # 80 x 180 kg contra los 14.500 que llegaron
    assert "Pallets armados con lo envasado (20)" in detalle
    lista = texto(client.get("/trasvasos"))
    assert "4521" in lista and "80 tambores de chapa" in lista and "Llegó de afuera" in lista
    historial = texto(client.get("/movimientos"))
    assert 'class="etq trasvaso"' in historial and 'href="/trasvasos/1"' in historial
    remito = texto(client.get("/movimientos/1"))
    assert "trasvaso de la orden 4521" in remito and "anulá el" in remito


def test_cisternas_del_deposito_a_tambores(app, client):
    preparar(client)
    movimiento(client, "ingreso", [(CISTERNAS, "10", "L-C", "2028-03-31")], remito="R-1")
    anotar(client, app, "Tambores de chapa", "ENTRAN_VACIOS", 100)
    lote_c = lote_id(app, CISTERNAS, "L-C")
    r = trasvaso(client, app, [(TAMBORES, "20", "L-C", "2028-03-31")], origen=[(CISTERNAS, "4", lote_c)],
                 envases={("Tambores de chapa", "VACIO"): 20, ("Cisternas", "SUCIO"): 4}, orden="77")
    assert r.status_code == 302, texto(r)
    assert stock(app, CISTERNAS) == 6 and stock(app, TAMBORES) == 20
    assert saldo(app, "Tambores de chapa", "VACIO") == 80 and saldo(app, "Cisternas", "SUCIO") == 4
    with app.app_context():
        movs = get_db().execute(
            "SELECT tipo, remito FROM movimientos WHERE trasvaso_id = 1 ORDER BY id").fetchall()
        assert [tuple(m) for m in movs] == [("EGRESO", "Orden 77"), ("INGRESO", "Orden 77")]
    detalle = texto(client.get("/trasvasos/1"))
    assert "salió del depósito" in detalle and "3.600 kg" in detalle  # 4 x 900 = 20 x 180
    assert "+0 kg" not in detalle and "Diferencia: <strong class=\"\">0 kg" in detalle
    assert "Patente camión" not in detalle  # sin transporte anotado, no se muestra el bloque
    assert "sin diferencia" in texto(client.get("/trasvasos"))
    # No puede salir más de lo que hay en el lote.
    r = trasvaso(client, app, [(TAMBORES, "35", "L-C", "")], origen=[(CISTERNAS, "7", lote_c)], orden="78")
    assert r.status_code == 200 and "Origen: No hay stock suficiente" in texto(r)
    # Si sale del depósito hay que elegir qué sale.
    r = client.post("/trasvasos/nuevo", data={
        "empresa_id": 1, "fecha": "2026-11-20", "orden": "79", "origen": "DEPOSITO",
        "producto_id": str(TAMBORES), "cantidad": "5", "lote": "L-C", "vencimiento": ""})
    assert r.status_code == 200 and "Origen: elegí qué producto del depósito" in texto(r)


def test_no_se_pueden_usar_mas_vacios_de_los_que_hay(app, client):
    preparar(client)
    anotar(client, app, "Tambores de chapa", "ENTRAN_VACIOS", 5)
    r = trasvaso(client, app, [(TAMBORES, "10", "", "")], envases={("Tambores de chapa", "VACIO"): 10})
    assert r.status_code == 200
    assert "Tambores de chapa vacíos: en el depósito hay 5 y querés usar 10" in texto(r)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM trasvasos").fetchone()[0] == 0
    # El formulario vuelve con lo que se había escrito.
    assert 'value="10"' in texto(r)


def test_orden_obligatoria_destino_obligatorio_y_orden_repetida(app, client):
    preparar(client)
    r = trasvaso(client, app, [(TAMBORES, "4", "", "")], orden="")
    assert "El número de orden es obligatorio" in texto(r)
    r = client.post("/trasvasos/nuevo", data={"empresa_id": 1, "fecha": "2026-11-20", "orden": "1", "origen": "AFUERA"})
    assert "Destino: cargá cómo queda envasado" in texto(r)
    r = trasvaso(client, app, [(TAMBORES, "4", "", "")], kilos_origen="mucho")
    assert "Los kilos que llegan no son un número válido" in texto(r)
    assert trasvaso(client, app, [(TAMBORES, "4", "", "")]).status_code == 302
    r = trasvaso(client, app, [(TAMBORES, "4", "", "")])
    assert "Ya hay un trasvaso con ese número de orden" in texto(r) and "confirmar_repetido" in texto(r)
    assert trasvaso(client, app, [(TAMBORES, "4", "", "")], confirmar_repetido="1").status_code == 302
    assert stock(app, TAMBORES) == 8


def test_anular_trasvaso(app, client):
    preparar(client)
    movimiento(client, "ingreso", [(CISTERNAS, "10", "L-C", "2028-03-31")], remito="R-1")
    anotar(client, app, "Tambores de chapa", "ENTRAN_VACIOS", 100)
    lote_c = lote_id(app, CISTERNAS, "L-C")
    trasvaso(client, app, [(TAMBORES, "20", "L-C", "")], origen=[(CISTERNAS, "4", lote_c)],
             envases={("Tambores de chapa", "VACIO"): 20, ("Cisternas", "SUCIO"): 4})
    # Los remitos del trasvaso no se anulan sueltos.
    r = client.post("/movimientos/2/anular", data={"motivo": "x"}, follow_redirects=True)
    assert "anulá el trasvaso completo" in texto(r)
    r = client.post("/trasvasos/1/anular", data={"motivo": ""}, follow_redirects=True)
    assert "Escribí el motivo" in texto(r)
    r = client.post("/trasvasos/1/anular", data={"motivo": "Cantidad mal cargada"}, follow_redirects=True)
    assert "Trasvaso anulado" in texto(r)
    assert stock(app, CISTERNAS) == 10 and stock(app, TAMBORES) == 0
    assert saldo(app, "Tambores de chapa", "VACIO") == 100 and saldo(app, "Cisternas", "SUCIO") == 0
    with app.app_context():
        motivos = {r[0] for r in get_db().execute("SELECT motivo_anulacion FROM movimientos WHERE trasvaso_id = 1")}
    assert motivos == {"Trasvaso anulado: Cantidad mal cargada"}
    assert "Ese trasvaso ya estaba anulado" in texto(
        client.post("/trasvasos/1/anular", data={"motivo": "otra vez"}, follow_redirects=True))
    assert 'class="anulado"' in texto(client.get("/trasvasos?anulados=1"))
    assert "4521" not in texto(client.get("/trasvasos"))


def test_no_se_anula_si_lo_envasado_ya_salio_o_los_sucios_se_lavaron(app, client):
    preparar(client)
    movimiento(client, "ingreso", [(CISTERNAS, "10", "L-C", "2028-03-31")], remito="R-1")
    lote_c = lote_id(app, CISTERNAS, "L-C")
    trasvaso(client, app, [(TAMBORES, "8", "L-C", "")], origen=[(CISTERNAS, "2", lote_c)],
             envases={("Cisternas", "SUCIO"): 2}, orden="1")
    # Se lavaron las cisternas sucias: anular dejaría los sucios en negativo.
    anotar(client, app, "Cisternas", "LAVADO", 2)
    r = client.post("/trasvasos/1/anular", data={"motivo": "x"}, follow_redirects=True)
    assert "las cisternas sucias quedarían en negativo" in texto(r)
    anotar(client, app, "Cisternas", "CONTEO_SUCIOS", 2)
    # Salieron 3 de los tambores envasados: anular dejaría stock negativo.
    movimiento(client, "egreso", [(TAMBORES, "3", lote_id(app, TAMBORES, "L-C"))], remito="E-1")
    r = client.post("/trasvasos/1/anular", data={"motivo": "x"}, follow_redirects=True)
    assert "el stock quedaría negativo para BG-T200" in texto(r)
    assert stock(app, TAMBORES) == 5


def test_fotos_e_insumos_van_con_lo_envasado(app, client):
    preparar(client)
    r = trasvaso(client, app, [(TAMBORES, "8", "", "")], fotos=[(BytesIO(JPG), "isotanque.jpg")],
                 **{f"ins_{PALLETS}_CLIENTE": "2", f"ins_{PALLETS}_PROPIO": "1"})
    assert r.status_code == 302
    detalle = texto(client.get("/trasvasos/1"))
    assert detalle.count('src="/fotos/') == 1 and 'name="volver" value="/trasvasos/1#fotos"' in detalle
    assert "Pallets del cliente" in detalle
    with app.app_context():
        assert ins.saldos_cliente(1).get((1, PALLETS)) == 2
    # Agregar otra foto desde el trasvaso vuelve al trasvaso.
    r = client.post("/movimientos/1/fotos", data={"fotos": (BytesIO(JPG), "tambores.jpg"), "volver": "/trasvasos/1#fotos"},
                    content_type="multipart/form-data")
    assert r.headers["Location"].endswith("/trasvasos/1#fotos")
    r = client.post("/movimientos/1/fotos", data={"fotos": (BytesIO(JPG), "x.jpg"), "volver": "//otro.sitio"},
                    content_type="multipart/form-data")
    assert r.headers["Location"].endswith("/movimientos/1#fotos")


def test_lista_filtros_y_excel(app, client):
    preparar(client)
    anotar(client, app, "Tambores de chapa", "ENTRAN_VACIOS", 50)
    trasvaso(client, app, [(TAMBORES, "40", "L1", "")], envases={("Tambores de chapa", "VACIO"): 40},
             kilos_origen="7300", orden="A-1")
    trasvaso(client, app, [(CISTERNAS, "2", "L2", "")], orden="B-2")
    assert "A-1" in texto(client.get("/trasvasos?orden=A")) and "B-2" not in texto(client.get("/trasvasos?orden=A"))
    assert "Todavía no" not in texto(client.get("/trasvasos"))
    assert "No hay trasvasos con esos filtros" in texto(client.get("/trasvasos?desde=2030-01-01"))
    r = client.get("/trasvasos/excel")
    ws = load_workbook(BytesIO(r.data)).active
    encabezados = [c.value for c in ws[1]]
    assert encabezados[:5] == ["Fecha", "N° de orden", "Remito", "Empresa", "Origen"]
    fila = {encabezados[i]: c.value for i, c in enumerate(ws[2])}
    assert fila["N° de orden"] == "A-1" and fila["Queda envasado"] == "40 tambores de chapa (BG-T200)"
    assert fila["Kilos envasados"] == 7200 and fila["Kilos del origen"] == 7300 and fila["Diferencia de kilos"] == -100
    assert fila["Envases vacíos usados"] == "40 tambores de chapa"


def test_migracion_desde_la_version_3(tmp_path):
    ruta = tmp_path / "datos" / "stock.db"
    ruta.parent.mkdir()
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA)
    for numero, sql in enumerate(MIGRACIONES[:3], start=1):
        con.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {numero};\nCOMMIT;")
    con.executescript("""
        INSERT INTO empresas (nombre) VALUES ('Copsa');
        INSERT INTO envases (nombre) VALUES ('Contenedores IBC');
    """)
    con.commit()
    con.close()
    app = create_app({"TESTING": True, "DATABASE": str(ruta)})
    with app.app_context():
        db = get_db()
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACIONES)
        lleva = {r["nombre"]: r["lleva_vacios"] for r in db.execute("SELECT * FROM envases")}
        assert lleva["Contenedores IBC"] == lleva["Cisternas"] == 1 and lleva["Bolsas"] == 0
        assert "trasvaso_id" in {c[1] for c in db.execute("PRAGMA table_info(movimientos)")}
    assert len(list((tmp_path / "datos" / "respaldos").glob("antes-de-actualizar-v3_*.db"))) == 1
    assert app.test_client().get("/trasvasos").status_code == 200
