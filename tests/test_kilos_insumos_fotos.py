"""Pruebas de los pedidos del 24/11: kilos, contenedor, insumos adicionales, fotos e historial por lote."""
import os
import sqlite3
from io import BytesIO

from openpyxl import load_workbook

from stock import create_app
from stock import insumos as ins
from stock.db import get_db
from test_deposito import despachar, ingreso_con_pallets
from test_stock import crear_empresa, crear_producto, importar, movimiento, planilla

JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 64  # alcanza con el comienzo de un JPG
PALLETS, FILM = 1, 2                      # los dos insumos que vienen cargados


def producto_con_kilos(client, codigo="T1", kilos="200", por_pallet="4", unidad="Tambores de chapa", empresa_id=1):
    r = client.post("/productos/nuevo", data={
        "empresa_id": empresa_id, "codigo": codigo, "descripcion": f"Producto {codigo}", "unidad": unidad,
        "unidades_por_pallet": por_pallet, "kilos_por_unidad": kilos,
    })
    assert r.status_code == 302, r.data


def saldo_pallets(app, empresa_id=1):
    with app.app_context():
        return ins.saldos_cliente(empresa_id).get((empresa_id, PALLETS), 0)


def errores(r):
    return r.get_data(as_text=True)


# ------------------------------------------------------------------- kilos


def test_producto_con_kilos_y_stock_con_kilos_totales(app, client):
    crear_empresa(client)
    producto_con_kilos(client, kilos="200")
    assert movimiento(client, "ingreso", [(1, "10")]).status_code == 302
    pagina = client.get("/").get_data(as_text=True)
    assert "Kilos totales" in pagina and "2.000 kg" in pagina and ">Cantidad<" in pagina
    assert client.get("/api/productos?empresa_id=1").get_json()[0]["kilos"] == 200
    assert "2.000 kg" in client.get("/productos/1").get_data(as_text=True)
    assert ">200<" in client.get("/productos").get_data(as_text=True)
    ws = load_workbook(BytesIO(client.get("/stock/excel").data)).active
    encabezados = [c.value for c in ws[1]]
    assert ws.cell(2, encabezados.index("Kg por unidad") + 1).value == 200
    assert ws.cell(2, encabezados.index("Kilos totales") + 1).value == 2000
    ws = load_workbook(BytesIO(client.get("/lotes/excel").data)).active
    encabezados = [c.value for c in ws[1]]
    assert ws.cell(2, encabezados.index("Kilos totales") + 1).value == 2000


def test_kilos_invalidos(app, client):
    crear_empresa(client)
    for malo in ("abc", "0", "-5"):
        r = client.post("/productos/nuevo", data={"empresa_id": 1, "codigo": "X", "descripcion": "X",
                                                  "unidad": "Bolsas", "kilos_por_unidad": malo})
        assert r.status_code == 200 and "Los kilos por unidad" in errores(r)
    producto_con_kilos(client, kilos="")      # vacío está bien
    producto_con_kilos(client, codigo="T2", kilos="25,5")
    with app.app_context():
        kilos = [r[0] for r in get_db().execute("SELECT kilos_por_unidad FROM productos ORDER BY id")]
    assert kilos == [None, 25.5]


def test_detalle_del_remito_suma_kilos_por_envase(client):
    crear_empresa(client)
    producto_con_kilos(client, codigo="T1", kilos="200")
    producto_con_kilos(client, codigo="B1", kilos="25", unidad="Bolsas", por_pallet="40")
    crear_producto(client, codigo="C1")          # sin kilos
    r = movimiento(client, "ingreso", [(1, "4"), (2, "100"), (3, "7")])
    pagina = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "800 kg" in pagina and "2.500 kg" in pagina and "3.300 kg" in pagina
    assert "Sin kilos cargados: C1" in pagina


def test_importar_con_kilos_por_unidad(app, client):
    encabezados = ("Empresa", "Código", "Descripción", "Envase", "Kilos por unidad", "Cantidad")
    assert "Importación terminada" in errores(importar(client, planilla(
        [("Copsa", "C-1", "Solvente", "Tambores", "180", 10), ("Copsa", "C-2", "Urea", "Bolsas", None, 5)],
        encabezados=encabezados)))
    # Un producto que ya existía sin kilos se completa con la planilla.
    assert "Importación terminada" in errores(importar(client, planilla(
        [("Copsa", "C-2", "", "", "25", 1)], encabezados=encabezados)))
    with app.app_context():
        kilos = dict(get_db().execute("SELECT codigo, kilos_por_unidad FROM productos").fetchall())
    assert kilos == {"C-1": 180, "C-2": 25}
    texto = errores(importar(client, planilla([("Copsa", "C-3", "Otro", "", "mucho", 1)], encabezados=encabezados)))
    assert "No se importó nada" in texto and "kilos por unidad" in texto
    plantilla = load_workbook(BytesIO(client.get("/importar/plantilla").data)).active
    assert "Kilos por unidad" in [c.value for c in plantilla[1]]


# -------------------------------------------------------------- contenedor


def test_contenedor_se_guarda_busca_y_exporta(client):
    crear_empresa(client)
    crear_producto(client)
    r = movimiento(client, "ingreso", [(1, "10")], contenedor="mscu 123456-5", tipo_contenedor="40' High Cube")
    assert r.status_code == 302
    detalle = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "MSCU1234565" in detalle and "40&#39; High Cube" in detalle
    # Lo que no tiene forma de contenedor queda como se escribió.
    movimiento(client, "ingreso", [(1, "5")], remito="R-2", contenedor="Cisterna  12")
    assert "Cisterna 12" in client.get("/movimientos/2").get_data(as_text=True)
    assert ">R-1<" in client.get("/movimientos?transporte=mscu 1234").get_data(as_text=True)
    assert ">R-1<" not in client.get("/movimientos?transporte=cisterna").get_data(as_text=True)
    ws = load_workbook(BytesIO(client.get("/movimientos/excel").data)).active
    encabezados = [c.value for c in ws[1]]
    columna = [ws.cell(n, encabezados.index("Contenedor") + 1).value for n in range(2, 4)]
    assert "MSCU1234565" in columna
    form = client.get("/movimientos/nuevo/egreso").get_data(as_text=True)
    assert '<option value="MSCU1234565">' in form and '<option value="40&#39; High Cube">' in form


# ----------------------------------------------------------------- insumos


def test_pallets_del_cliente_llevan_saldo_y_los_propios_no(app, client):
    crear_empresa(client)
    crear_producto(client)
    r = movimiento(client, "ingreso", [(1, "10")],
                   **{f"ins_{PALLETS}_CLIENTE": "20", f"ins_{PALLETS}_PROPIO": "5", f"ins_{FILM}": "2"})
    assert r.status_code == 302
    detalle = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Pallets del cliente" in detalle and "Pallets propios" in detalle and "Film stretch" in detalle
    assert saldo_pallets(app) == 20
    # No pueden salir más pallets del cliente de los que hay.
    r = movimiento(client, "egreso", [(1, "1")], remito="E-1", **{f"ins_{PALLETS}_CLIENTE": "25"})
    assert r.status_code == 200 and "hay 20 pallets de esta empresa" in errores(r)
    assert movimiento(client, "egreso", [], remito="E-1",
                      **{f"ins_{PALLETS}_CLIENTE": "15", f"ins_{PALLETS}_PROPIO": "100"}).status_code == 302
    assert saldo_pallets(app) == 5
    # El ingreso no se puede anular mientras sus pallets ya salieron.
    r = client.post("/movimientos/1/anular", data={"motivo": "error"}, follow_redirects=True)
    assert "pallets del cliente quedarían en negativo" in errores(r)
    client.post("/movimientos/2/anular", data={"motivo": "error"})
    assert saldo_pallets(app) == 20
    client.post("/movimientos/1/anular", data={"motivo": "error"})
    assert saldo_pallets(app) == 0
    pagina = client.get("/movimientos?ver_anulados=1").get_data(as_text=True)
    assert "Insumos adicionales" in pagina and "Pallets del cliente" in pagina
    hoja = load_workbook(BytesIO(client.get("/movimientos/excel?ver_anulados=1").data))["Insumos"]
    filas = [[c.value for c in fila][3:6] for fila in hoja.iter_rows(min_row=2)]
    assert ["Pallets del cliente", 20, None] in filas and ["Pallets del cliente", None, 15] in filas


def test_remito_solo_de_insumos(app, client):
    crear_empresa(client)
    r = movimiento(client, "ingreso", [], remito="Saldo inicial", **{f"ins_{PALLETS}_CLIENTE": "30"})
    assert r.status_code == 302
    assert "solo de insumos" in client.get(r.headers["Location"]).get_data(as_text=True)
    assert movimiento(client, "egreso", [], remito="Devolución", **{f"ins_{PALLETS}_CLIENTE": "10"}).status_code == 302
    assert saldo_pallets(app) == 20
    assert client.get("/api/insumos?empresa_id=1").get_json() == {str(PALLETS): 20}
    r = movimiento(client, "egreso", [], remito="Nada")
    assert r.status_code == 200 and "Cargá al menos un producto" in errores(r)
    r = movimiento(client, "egreso", [], remito="Malo", **{f"ins_{FILM}": "dos"})
    assert r.status_code == 200 and "film stretch no es un número" in errores(r)
    stock = client.get("/").get_data(as_text=True)
    assert "Pallets de clientes en el depósito" in stock


def test_pagina_de_insumos(app, client):
    crear_empresa(client, "Milberg")
    movimiento(client, "ingreso", [], remito="Saldo inicial", **{f"ins_{PALLETS}_CLIENTE": "12"})
    pagina = client.get("/insumos").get_data(as_text=True)
    assert "Milberg" in pagina and ">12<" in pagina
    client.post("/insumos", data={"nombre": "Esquineros", "unidad": "unidades"}, follow_redirects=True)
    r = client.post("/insumos", data={"nombre": "esquineros"}, follow_redirects=True)  # repetido
    assert "ya está en la lista" in errores(r)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM insumos").fetchone()[0] == 3
    client.post("/insumos/3", data={"nombre": "Esquineros de cartón", "unidad": "unidades"}, follow_redirects=True)
    client.post("/insumos/3", data={"accion": "activar"}, follow_redirects=True)  # desactivar
    form = client.get("/movimientos/nuevo/ingreso").get_data(as_text=True)
    assert "Film stretch" in form and "Esquineros" not in form
    assert "Esquineros de cartón" in client.get("/insumos").get_data(as_text=True)


def test_terminal_despacha_con_contenedor_e_insumos(app, client):
    crear_empresa(client)
    producto_con_kilos(client, codigo="B1", kilos="25", unidad="Bolsas", por_pallet="40")
    ingreso_con_pallets(client, [(1, "80", "L1")])
    movimiento(client, "ingreso", [], remito="Saldo inicial", **{f"ins_{PALLETS}_CLIENTE": "2"})
    r = despachar(client, [(1, 1, 1, "40")], **{f"ins_{PALLETS}_CLIENTE": "3"})
    assert r.status_code == 200 and "hay 2 pallets de esta empresa" in errores(r)
    r = despachar(client, [(1, 1, 1, "40")], contenedor="CSQU 305438-3", **{f"ins_{PALLETS}_CLIENTE": "1"})
    assert r.status_code == 302
    with app.app_context():
        m = get_db().execute("SELECT * FROM movimientos WHERE tipo = 'EGRESO'").fetchone()
    assert m["contenedor"] == "CSQU3054383"
    assert saldo_pallets(app) == 1


# ------------------------------------------------------------------- fotos


def test_fotos_al_cargar_y_desde_el_remito(app, client):
    crear_empresa(client)
    crear_producto(client)
    data = {"empresa_id": 1, "fecha": "2026-11-10", "remito": "R-1", "producto_id": "1", "cantidad": "10",
            "lote": "", "vencimiento": "", "fotos": [(BytesIO(JPG), "camion.jpg"), (BytesIO(JPG), "otra.jpeg")]}
    r = client.post("/movimientos/nuevo/ingreso", data=data, content_type="multipart/form-data")
    assert r.status_code == 302
    pagina = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Con 2 fotos" in pagina and pagina.count('src="/fotos/') == 2
    foto = client.get("/fotos/1")
    assert foto.status_code == 200 and foto.mimetype == "image/jpeg" and foto.data == JPG
    with app.app_context():
        archivo = get_db().execute("SELECT archivo FROM movimiento_fotos WHERE id = 1").fetchone()[0]
    ruta = os.path.join(app.config["FOTOS"], *archivo.split("/"))
    assert os.path.exists(ruta)
    # Agregar otra desde el remito y quitar una.
    r = client.post("/movimientos/1/fotos", data={"fotos": (BytesIO(JPG), "precinto.jpg")},
                    content_type="multipart/form-data", follow_redirects=True)
    assert "Se agregó 1 foto" in errores(r)
    client.post("/fotos/1/borrar")
    assert not os.path.exists(ruta)
    assert client.get("/fotos/1").status_code == 404
    assert client.get("/movimientos/1").get_data(as_text=True).count('src="/fotos/') == 2


def test_lo_que_no_es_foto_no_se_guarda(app, client):
    crear_empresa(client)
    crear_producto(client)
    data = {"empresa_id": 1, "fecha": "2026-11-10", "remito": "R-1", "producto_id": "1", "cantidad": "10",
            "lote": "", "vencimiento": "", "fotos": (BytesIO(b"MZ esto no es una foto"), "virus.jpg")}
    r = client.post("/movimientos/nuevo/ingreso", data=data, content_type="multipart/form-data")
    assert r.status_code == 200 and "no es una foto" in errores(r)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM movimientos").fetchone()[0] == 0
    # Si falta otro dato, avisa que hay que volver a elegir las fotos.
    data.update(remito="", fotos=(BytesIO(JPG), "bien.jpg"))
    r = client.post("/movimientos/nuevo/ingreso", data=data, content_type="multipart/form-data")
    assert "volvé a elegirlas" in errores(r)


def test_envio_demasiado_grande(tmp_path):
    app = create_app({"TESTING": True, "DATABASE": str(tmp_path / "t.db"), "MAX_CONTENT_LENGTH": 2000})
    client = app.test_client()
    r = client.post("/movimientos/nuevo/ingreso", data={"fotos": (BytesIO(JPG * 100), "grande.jpg")},
                    content_type="multipart/form-data", headers={"Referer": "/movimientos/nuevo/ingreso"},
                    follow_redirects=True)
    assert "pesa demasiado" in errores(r)


# --------------------------------------------------------- historial por lote


def test_historial_por_lote_de_arriba_hacia_abajo(client):
    crear_empresa(client)
    producto_con_kilos(client, codigo="B1", kilos="25", unidad="Bolsas", por_pallet="40")
    movimiento(client, "ingreso", [(1, "10", "L1")], remito="R-1", fecha="2026-11-01")
    movimiento(client, "ingreso", [(1, "5", "L2")], remito="R-2", fecha="2026-11-02")
    movimiento(client, "egreso", [(1, "3", 1)], remito="E-1", fecha="2026-11-03")
    pagina = client.get("/movimientos").get_data(as_text=True)
    # Separado por lote: primero todo el L1 (de lo más viejo a lo más nuevo) y después el L2.
    assert pagina.index("Lote L1") < pagina.index(">R-1<") < pagina.index(">E-1<") < pagina.index("Lote L2")
    assert pagina.index("Lote L2") < pagina.index(">R-2<")
    assert "Cantidad general" in pagina and "12 bolsas" in pagina and "300 kg" in pagina
    assert '<td class="num saldo">7</td>' in pagina
    # Desde una fecha: cada lote arranca con su saldo anterior.
    pagina = client.get("/movimientos?desde=2026-11-02").get_data(as_text=True)
    assert "Saldo anterior al 02/11/2026" in pagina and ">R-1<" not in pagina and ">E-1<" in pagina
    # Solo egresos: el saldo sigue siendo el real del lote.
    pagina = client.get("/movimientos?tipo=EGRESO").get_data(as_text=True)
    assert '<td class="num saldo">7</td>' in pagina and ">R-1<" not in pagina
    ficha = client.get("/productos/1").get_data(as_text=True)
    assert ficha.index(">R-1<") < ficha.index(">E-1<") < ficha.index(">R-2<")


def test_historial_excel_con_saldo_y_kilos(client):
    crear_empresa(client)
    producto_con_kilos(client, codigo="B1", kilos="25", unidad="Bolsas", por_pallet="40")
    movimiento(client, "ingreso", [(1, "10", "L1")], remito="R-1", fecha="2026-11-01")
    movimiento(client, "egreso", [(1, "4", 1)], remito="E-1", fecha="2026-11-03")
    ws = load_workbook(BytesIO(client.get("/movimientos/excel").data)).active
    filas = [[c.value for c in fila] for fila in ws.iter_rows()]
    encabezados = filas[0]
    saldo, kilos = encabezados.index("Saldo del lote"), encabezados.index("Kilos")
    saldo_kg = encabezados.index("Saldo en kg")
    assert [(f[1], f[kilos], f[saldo], f[saldo_kg]) for f in filas[1:3]] == [("R-1", 250, 10, 250), ("E-1", 100, 6, 150)]
    total_kilos = next(f for f in filas if f[0] == "Total kilos")
    assert total_kilos[8:10] == [250, 100]
    ws = load_workbook(BytesIO(client.get("/movimientos/excel?desde=2026-11-02").data)).active
    assert [c.value for c in ws[2]][1] == "Saldo anterior" and [c.value for c in ws[2]][saldo] == 10


def test_migracion_desde_la_version_2(tmp_path):
    """Una base de la etapa 2 suma los kilos, el contenedor, los insumos y las fotos."""
    from stock.db import ESQUEMA, MIGRACIONES
    ruta = tmp_path / "datos" / "stock.db"
    ruta.parent.mkdir()
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA)
    for numero, sql in enumerate(MIGRACIONES[:2], start=1):
        con.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {numero};\nCOMMIT;")
    con.executescript("""
        INSERT INTO empresas (nombre) VALUES ('Copsa');
        INSERT INTO productos (empresa_id, codigo, descripcion, unidad) VALUES (1, 'C1', 'Urea', 'Bolsas');
    """)
    con.commit()
    con.close()
    app = create_app({"TESTING": True, "DATABASE": str(ruta)})
    with app.app_context():
        db = get_db()
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACIONES) == 3
        assert "kilos_por_unidad" in [c[1] for c in db.execute("PRAGMA table_info(productos)")]
        assert {"contenedor", "tipo_contenedor"} <= {c[1] for c in db.execute("PRAGMA table_info(movimientos)")}
        assert [r["nombre"] for r in ins.lista()] == ["Pallets", "Film stretch"]
    assert len(list((tmp_path / "datos" / "respaldos").glob("antes-de-actualizar-v2_*.db"))) == 1
