from datetime import datetime
from io import BytesIO

from openpyxl import Workbook, load_workbook

from stock import create_app, formato_numero
from stock.db import envases, get_db, stock_de_lotes, stock_por_id, stock_por_lote_id
from stock.formato import texto_pallets


def crear_empresa(client, nombre="Acme SA"):
    r = client.post("/empresas/nueva", data={"nombre": nombre})
    assert r.status_code == 302
    return nombre


def crear_producto(client, empresa_id=1, codigo="A1", descripcion="Caja chica", minimo=""):
    r = client.post("/productos/nuevo", data={
        "empresa_id": empresa_id, "codigo": codigo, "descripcion": descripcion,
        "unidad": "cajas", "stock_minimo": minimo,
    })
    assert r.status_code == 302, r.data


def movimiento(client, tipo, items, remito="R-1", empresa_id=1, fecha="2026-11-10", **extra):
    """items de ingreso: (producto_id, cantidad[, lote[, vencimiento]])
    items de egreso: (producto_id, cantidad[, lote_id])  (si no se indica, lote_id = producto_id)"""
    data = {"empresa_id": empresa_id, "fecha": fecha, "remito": remito,
            "producto_id": [str(i[0]) for i in items], "cantidad": [str(i[1]) for i in items], **extra}
    if tipo == "ingreso":
        data["lote"] = [i[2] if len(i) > 2 else "" for i in items]
        data["vencimiento"] = [i[3] if len(i) > 3 else "" for i in items]
    else:
        data["lote_id"] = [str(i[2] if len(i) > 2 else i[0]) for i in items]
    return client.post(f"/movimientos/nuevo/{tipo}", data=data)


def stock(app, pid):
    with app.app_context():
        return stock_por_id([pid])[pid]


def test_pantallas_vacias_cargan(client):
    for url in ["/", "/empresas", "/productos", "/movimientos", "/empresas/nueva",
                "/productos/nuevo", "/movimientos/nuevo/ingreso", "/movimientos/nuevo/egreso"]:
        assert client.get(url).status_code == 200, url


def test_ingreso_y_egreso_actualizan_stock(app, client):
    crear_empresa(client)
    crear_producto(client)
    assert movimiento(client, "ingreso", [(1, "100")]).status_code == 302
    assert stock(app, 1) == 100
    assert movimiento(client, "egreso", [(1, "30,5")], remito="E-1").status_code == 302
    assert stock(app, 1) == 69.5
    pagina = client.get("/").get_data(as_text=True)
    assert "69,5" in pagina


def test_no_permite_sacar_mas_de_lo_que_hay(app, client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "10")])
    r = movimiento(client, "egreso", [(1, "11")], remito="E-1")
    assert r.status_code == 200
    assert "No hay stock suficiente" in r.get_data(as_text=True)
    assert stock(app, 1) == 10


def test_renglones_repetidos_se_suman_para_validar(app, client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "10")])
    r = movimiento(client, "egreso", [(1, "6"), (1, "6")], remito="E-1")
    assert "No hay stock suficiente" in r.get_data(as_text=True)


def test_no_mezcla_productos_de_otra_empresa(client):
    crear_empresa(client, "Acme")
    crear_empresa(client, "Otra")
    crear_producto(client, empresa_id=2, codigo="X")
    r = movimiento(client, "ingreso", [(1, "5")], empresa_id=1)
    assert "no son de la empresa" in r.get_data(as_text=True)


def test_mismo_codigo_en_distintas_empresas_y_duplicado_en_la_misma(client):
    crear_empresa(client, "Acme")
    crear_empresa(client, "Otra")
    crear_producto(client, empresa_id=1, codigo="A1")
    crear_producto(client, empresa_id=2, codigo="A1")
    r = client.post("/productos/nuevo", data={"empresa_id": 1, "codigo": "a1", "descripcion": "x", "unidad": "Bolsas"})
    assert "ya tiene un producto con ese código" in r.get_data(as_text=True)


def test_empresa_duplicada(client):
    crear_empresa(client, "Acme")
    r = client.post("/empresas/nueva", data={"nombre": "ACME"})
    assert "Ya existe una empresa" in r.get_data(as_text=True)


def test_remito_de_ingreso_repetido_pide_confirmacion(app, client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "5")])
    r = movimiento(client, "ingreso", [(1, "5")])
    assert "Cargar igual" in r.get_data(as_text=True)
    assert stock(app, 1) == 5
    r = movimiento(client, "ingreso", [(1, "5")], confirmar_repetido="1")
    assert r.status_code == 302
    assert stock(app, 1) == 10


def test_validaciones_basicas(client):
    crear_empresa(client)
    crear_producto(client)
    r = movimiento(client, "ingreso", [(1, "0")], remito="")
    texto = r.get_data(as_text=True)
    assert "remito es obligatorio" in texto
    assert "mayor a cero" in texto


def test_anular_egreso_devuelve_stock(app, client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "10")])
    movimiento(client, "egreso", [(1, "4")], remito="E-1")
    r = client.post("/movimientos/2/anular", data={"motivo": "error"})
    assert r.status_code == 302
    assert stock(app, 1) == 10


def test_no_anula_ingreso_si_ya_se_saco_mercaderia(app, client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "10")])
    movimiento(client, "egreso", [(1, "4")], remito="E-1")
    r = client.post("/movimientos/1/anular", data={"motivo": "error"}, follow_redirects=True)
    assert "No se puede anular" in r.get_data(as_text=True)
    assert stock(app, 1) == 6


def test_ficha_de_producto_muestra_saldo(client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "10")], fecha="2026-11-01")
    movimiento(client, "egreso", [(1, "3")], remito="E-1", fecha="2026-11-02")
    texto = client.get("/productos/1").get_data(as_text=True)
    assert "02/11/2026" in texto and ">7<" in texto


def test_exportar_excel(client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "10")])
    r = client.get("/stock/excel")
    ws = load_workbook(BytesIO(r.data)).active
    assert ws["A1"].value == "Empresa"
    assert ws["D1"].value == "Cantidad" and ws["D2"].value == 10
    r = client.get("/movimientos/excel?tipo=INGRESO")
    ws = load_workbook(BytesIO(r.data)).active
    assert ws["B1"].value == "Remito" and ws["B2"].value == "R-1"


def test_filtro_movimientos_por_fecha(client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "10")], remito="VIEJO", fecha="2026-01-01")
    movimiento(client, "ingreso", [(1, "10")], remito="NUEVO", fecha="2026-11-01")
    texto = client.get("/movimientos?desde=2026-06-01").get_data(as_text=True)
    assert ">NUEVO<" in texto and ">VIEJO<" not in texto


def stock_lote(app, lote_id):
    with app.app_context():
        return stock_por_lote_id([lote_id])[lote_id]


def test_lotes_con_vencimiento(app, client):
    crear_empresa(client)
    crear_producto(client)
    r = movimiento(client, "ingreso", [(1, "10", "L1", "2027-01-31"), (1, "5", "L2", "2026-12-01")])
    assert r.status_code == 302
    assert stock(app, 1) == 15
    # Egreso del lote L2 (id 2): no puede sacar más de lo que tiene ese lote.
    r = movimiento(client, "egreso", [(1, "6", 2)], remito="E-1")
    assert "No hay stock suficiente" in r.get_data(as_text=True)
    assert movimiento(client, "egreso", [(1, "5", 2)], remito="E-1").status_code == 302
    assert stock_lote(app, 2) == 0 and stock_lote(app, 1) == 10
    # La API ofrece solo lotes con stock, primero el que vence antes.
    datos = client.get("/api/productos?empresa_id=1").get_json()
    assert [l["lote"] for l in datos[0]["lotes"]] == ["L1"]


def test_mismo_lote_con_otro_vencimiento_da_error(client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "10", "L1", "2027-01-31")])
    r = movimiento(client, "ingreso", [(1, "10", "L1", "2028-01-31")], remito="R-2")
    assert "ya está cargado con vencimiento 31/01/2027" in r.get_data(as_text=True)
    # Sin vencimiento usa el que ya tenía.
    assert movimiento(client, "ingreso", [(1, "10", "l1")], remito="R-3").status_code == 302


def test_egreso_exige_lote(client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "10")])
    r = client.post("/movimientos/nuevo/egreso", data={
        "empresa_id": 1, "fecha": "2026-11-10", "remito": "E", "producto_id": "1", "cantidad": "1", "lote_id": ""})
    assert "elegí de qué lote sale" in r.get_data(as_text=True)


def test_pantalla_lotes_y_vencidos(client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "10", "VIEJO", "2020-01-01"), (1, "3", "NUEVO", "2099-01-01")])
    texto = client.get("/lotes?vence=vencidos").get_data(as_text=True)
    assert "VIEJO" in texto and "NUEVO" not in texto
    assert "vencido" in client.get("/lotes").get_data(as_text=True)
    ws = load_workbook(BytesIO(client.get("/lotes/excel").data)).active
    assert ws["D2"].value == "VIEJO"


def planilla(filas, encabezados=("Empresa", "Código", "Descripción", "Unidad", "Lote", "Vencimiento", "Cantidad")):
    wb = Workbook()
    ws = wb.active
    ws.append(["Conteo al día"])  # título arriba: tiene que encontrar igual los encabezados
    ws.append(list(encabezados))
    for f in filas:
        ws.append(list(f))
    salida = BytesIO()
    wb.save(salida)
    salida.seek(0)
    return salida


def importar(client, archivo):
    return client.post("/importar", data={"fecha": "2026-11-16", "remito": "STOCK INICIAL",
                                          "archivo": (archivo, "conteo.xlsx")},
                       content_type="multipart/form-data")


def test_importar_conteo(app, client):
    crear_empresa(client, "Milberg")
    archivo = planilla([
        ("MILBERG", "M-1", "Producto uno", "bolsas", "A1", datetime(2027, 5, 1), 100),
        ("Milberg", "M-1", "", "", "A2", "15/08/2027", "50,5"),
        ("Copsa", 1234, "Producto copsa", "", "", None, 7),
        (None, None, None, None, None, None, None),
    ])
    r = importar(client, archivo)
    texto = r.get_data(as_text=True)
    assert "Importación terminada" in texto, texto
    with app.app_context():
        db = get_db()
        assert db.execute("SELECT COUNT(*) FROM empresas").fetchone()[0] == 2  # Milberg ya existía
        lotes = {r["lote"]: r for r in stock_de_lotes()}
        assert lotes["A1"]["stock"] == 100 and lotes["A1"]["vencimiento"] == "2027-05-01"
        assert lotes["A2"]["stock"] == 50.5 and lotes["A2"]["vencimiento"] == "2027-08-15"
        assert lotes[""]["codigo"] == "1234"


def test_importar_con_errores_no_guarda_nada(app, client):
    archivo = planilla([
        ("Copsa", "C-1", "Algo", "", "", "", 10),
        ("Copsa", "C-2", "", "", "", "", 10),          # nuevo sin descripción
        ("Copsa", "C-3", "Otro", "", "", "mañana", 5),  # fecha inválida
        ("Copsa", "C-4", "Otro", "", "", "", "diez"),   # cantidad inválida
    ])
    texto = importar(client, archivo).get_data(as_text=True)
    assert "No se importó nada" in texto
    assert "Fila 5" in texto and "Fila 6" in texto
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM empresas").fetchone()[0] == 0
    texto = importar(client, planilla([("Copsa", "C-2", "", "", "", "", 10)])).get_data(as_text=True)
    assert "le falta la descripción" in texto


def test_importar_sin_encabezados(client):
    texto = importar(client, planilla([("a", "b")], encabezados=("Hola", "Chau"))).get_data(as_text=True)
    assert "No encontré los encabezados" in texto


def test_plantilla_se_descarga(client):
    ws = load_workbook(BytesIO(client.get("/importar/plantilla").data)).active
    assert ws["A1"].value == "Empresa"


def test_formato_numero():
    assert formato_numero(1234.5) == "1.234,5"
    assert formato_numero(10.0) == "10"


# ------------------------------------------------------------ segunda ronda


def crear_producto_pallet(client, empresa_id=1, codigo="B1", unidad="Bolsas", por_pallet="40"):
    r = client.post("/productos/nuevo", data={
        "empresa_id": empresa_id, "codigo": codigo, "descripcion": f"Producto {codigo}",
        "unidad": unidad, "unidades_por_pallet": por_pallet,
    })
    assert r.status_code == 302, r.data


def test_texto_pallets():
    assert texto_pallets(100, 40, "Bolsas") == "2 pallets + 20 bolsas"
    assert texto_pallets(80, 40, "Bolsas") == "2 pallets"
    assert texto_pallets(40, 40, "Bolsas") == "1 pallet"
    assert texto_pallets(10, 40, "Bolsas") == "menos de 1 pallet"
    assert texto_pallets(10, None, "Bolsas") == ""
    assert texto_pallets(0, 40, "Bolsas") == ""


def test_envase_obligatorio_y_otro_tipo_se_agrega_a_la_lista(app, client):
    crear_empresa(client)
    r = client.post("/productos/nuevo", data={"empresa_id": 1, "codigo": "X", "descripcion": "x"})
    assert "Elegí el tipo de envase" in r.get_data(as_text=True)
    r = client.post("/productos/nuevo", data={"empresa_id": 1, "codigo": "X", "descripcion": "x",
                                              "unidad": "__otro__", "unidad_otra": "Contenedores IBC"})
    assert r.status_code == 302
    with app.app_context():
        nombres = [e["nombre"] for e in envases()]
        assert "Contenedores IBC" in nombres and "Tambores de chapa" in nombres and "Cisternas" in nombres
        assert get_db().execute("SELECT unidad FROM productos").fetchone()[0] == "Contenedores IBC"


def test_renombrar_envase_actualiza_productos(app, client):
    crear_empresa(client)
    crear_producto_pallet(client, unidad="Tambores de chapa")
    with app.app_context():
        envase_id = get_db().execute("SELECT id FROM envases WHERE nombre = 'Tambores de chapa'").fetchone()[0]
    r = client.post(f"/envases/{envase_id}", data={"nombre": "Tambores de chapa 200 L"})
    assert r.status_code == 302
    with app.app_context():
        assert get_db().execute("SELECT unidad FROM productos").fetchone()[0] == "Tambores de chapa 200 L"
    texto = client.get("/envases").get_data(as_text=True)
    assert "Tambores de chapa 200 L" in texto


def test_por_pallet_invalido(client):
    crear_empresa(client)
    r = client.post("/productos/nuevo", data={"empresa_id": 1, "codigo": "X", "descripcion": "x",
                                              "unidad": "Bolsas", "unidades_por_pallet": "cero"})
    assert "unidades por pallet" in r.get_data(as_text=True)


def test_stock_muestra_pallets(client):
    crear_empresa(client)
    crear_producto_pallet(client)
    movimiento(client, "ingreso", [(1, "100", "L1")])
    texto = client.get("/").get_data(as_text=True)
    assert "2 pallets + 20 bolsas" in texto
    ws = load_workbook(BytesIO(client.get("/stock/excel").data)).active
    encabezados = [c.value for c in ws[1]]
    assert ws.cell(2, encabezados.index("Equivale a (pallets)") + 1).value == 2.5


def test_transporte_se_guarda_y_autocompleta(app, client):
    crear_empresa(client)
    crear_producto(client)
    r = movimiento(client, "ingreso", [(1, "10")], chofer="Juan Pérez", transporte="Transportes Sur",
                   patente_camion="ab 123 cd", patente_semi="ac-456-de", observaciones="Llegó bien")
    assert r.status_code == 302
    texto = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Juan Pérez" in texto and "AB123CD" in texto and "AC456DE" in texto and "Llegó bien" in texto
    form = client.get("/movimientos/nuevo/egreso").get_data(as_text=True)
    assert '<option value="Juan Pérez">' in form and '<option value="Transportes Sur">' in form
    # Filtro por patente (aunque se escriba con espacios) y por chofer
    assert ">R-1<" in client.get("/movimientos?transporte=AB 123").get_data(as_text=True)
    assert ">R-1<" not in client.get("/movimientos?transporte=Gomez").get_data(as_text=True)
    ws = load_workbook(BytesIO(client.get("/movimientos/excel").data)).active
    encabezados = [c.value for c in ws[1]]
    assert ws.cell(2, encabezados.index("Chofer") + 1).value == "Juan Pérez"
    assert ws.cell(2, encabezados.index("Patente semi") + 1).value == "AC456DE"


def test_movimientos_debe_haber_y_totales(app, client):
    crear_empresa(client)
    crear_producto(client)                       # cajas
    crear_producto_pallet(client, codigo="B1")   # Bolsas, producto 2
    movimiento(client, "ingreso", [(1, "10"), (2, "100")])
    movimiento(client, "egreso", [(1, "4", 1)], remito="E-1")
    movimiento(client, "egreso", [(2, "30", 2)], remito="E-2")
    movimiento(client, "egreso", [(2, "5", 2)], remito="E-3")
    client.post("/movimientos/4/anular", data={"motivo": "error"})   # E-3 no cuenta
    with app.app_context():
        from stock.rutas import _totales_movimientos
        f = {"empresa_id": None, "tipo": "", "desde": "", "hasta": "", "remito": "", "producto": "",
             "transporte": "", "ver_anulados": True}
        totales = {t["unidad"]: t for t in _totales_movimientos(f)}
    assert totales["cajas"]["ingresos"] == 10 and totales["cajas"]["egresos"] == 4
    assert totales["Bolsas"]["ingresos"] == 100 and totales["Bolsas"]["egresos"] == 30
    assert totales["Bolsas"]["diferencia"] == 70
    texto = client.get("/movimientos").get_data(as_text=True)
    assert "Ingresos (debe)" in texto and "Egresos (haber)" in texto and "Total Bolsas" in texto
    ws = load_workbook(BytesIO(client.get("/movimientos/excel").data)).active
    valores = [[c.value for c in fila] for fila in ws.iter_rows()]
    total_bolsas = next(f for f in valores if f[0] == "Total Bolsas")
    assert total_bolsas[8:10] == [100, 30]
    diferencia = next(f for f in valores if f[0] and str(f[0]).startswith("Diferencia Bolsas"))
    assert diferencia[8] == 70


def test_stock_filtra_y_detalla_por_lote(client):
    crear_empresa(client)
    crear_producto(client, codigo="A1")
    crear_producto(client, codigo="A2")
    movimiento(client, "ingreso", [(1, "10", "LOTE-111"), (1, "5", "LOTE-222"), (2, "7", "OTRO-9")])
    texto = client.get("/?lote=111").get_data(as_text=True)
    assert "Lote LOTE-111" in texto and "LOTE-222" not in texto and ">A2<" not in texto
    texto = client.get("/?ver_lotes=1").get_data(as_text=True)
    assert "Lote LOTE-111" in texto and "Lote LOTE-222" in texto and "Lote OTRO-9" in texto
    ws = load_workbook(BytesIO(client.get("/stock/excel?ver_lotes=1").data)).active
    assert ws["D1"].value == "Lote" and ws.max_row == 4


def test_migracion_de_base_vieja(tmp_path):
    """Una base creada por la primera versión se actualiza sola, con copia de respaldo antes."""
    import sqlite3
    from stock.db import ESQUEMA, MIGRACIONES
    ruta = tmp_path / "datos" / "stock.db"
    ruta.parent.mkdir()
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA)
    con.executescript("""
        INSERT INTO empresas (nombre) VALUES ('Milberg');
        INSERT INTO productos (empresa_id, codigo, descripcion, unidad) VALUES (1, 'M1', 'Resina', 'bolsas');
        INSERT INTO lotes (producto_id, lote) VALUES (1, 'L1');
        INSERT INTO movimientos (tipo, fecha, remito, empresa_id) VALUES ('INGRESO', '2026-11-10', 'R1', 1);
        INSERT INTO movimiento_items (movimiento_id, producto_id, lote_id, cantidad) VALUES (1, 1, 1, 25);
    """)
    con.commit()
    con.close()
    app = create_app({"TESTING": True, "DATABASE": str(ruta)})
    with app.app_context():
        db = get_db()
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACIONES)
        assert stock_por_id([1])[1] == 25
        nombres = [e["nombre"].lower() for e in envases()]
        assert nombres.count("bolsas") == 1 and "cisternas" in nombres
        assert db.execute("SELECT unidad FROM productos").fetchone()[0] == "Bolsas"
        columnas = [c[1] for c in db.execute("PRAGMA table_info(movimientos)")]
        assert {"chofer", "transporte", "patente_camion", "patente_semi"} <= set(columnas)
    respaldos = list((tmp_path / "datos" / "respaldos").glob("antes-de-actualizar-*.db"))
    assert len(respaldos) == 1
    viejo = sqlite3.connect(respaldos[0])
    assert viejo.execute("SELECT COUNT(*) FROM movimiento_items").fetchone()[0] == 1
    viejo.close()
    # Al volver a abrir no migra ni respalda de nuevo.
    create_app({"TESTING": True, "DATABASE": str(ruta)})
    assert len(list((tmp_path / "datos" / "respaldos").glob("*.db"))) == 1


def test_importar_con_envase_y_por_pallet(app, client):
    archivo = planilla(
        [("Copsa", "C-1", "Solvente", "Tambores de chapa", 4, "", "", 10)],
        encabezados=("Empresa", "Código", "Descripción", "Envase", "Por pallet", "Lote", "Vencimiento", "Cantidad"),
    )
    assert "Importación terminada" in importar(client, archivo).get_data(as_text=True)
    with app.app_context():
        p = get_db().execute("SELECT unidad, unidades_por_pallet FROM productos").fetchone()
        assert p["unidad"] == "Tambores de chapa" and p["unidades_por_pallet"] == 4


def test_respaldo_diario(tmp_path):
    import os
    from stock.db import respaldo_diario
    ruta = tmp_path / "stock.db"
    create_app({"TESTING": True, "DATABASE": str(ruta)})
    carpeta = tmp_path / "respaldos"
    carpeta.mkdir()
    for dia in range(1, 35):  # copias viejas de otros días
        (carpeta / f"stock-2020-01-{dia:02d}.db").write_bytes(b"")
    destino = respaldo_diario(str(ruta))
    assert os.path.exists(destino)
    assert len(list(carpeta.glob("stock-*.db"))) == 30
    assert respaldo_diario(str(ruta)) == destino  # mismo día: no duplica


def test_numeros_a_la_argentina():
    from stock.servicios import parse_numero
    assert parse_numero("1.000") == 1000
    assert parse_numero("12.500") == 12500
    assert parse_numero("1.000.000") == 1000000
    assert parse_numero("1.234,5") == 1234.5
    assert parse_numero("1234,5") == 1234.5
    assert parse_numero("2.5") == 2.5
    assert parse_numero("0.500") == 0.5
    assert parse_numero("40") == 40
    assert parse_numero("diez") is None


def test_cantidad_con_punto_de_miles(app, client):
    crear_empresa(client)
    crear_producto_pallet(client, por_pallet="1.000")
    assert movimiento(client, "ingreso", [(1, "2.500")]).status_code == 302
    assert stock(app, 1) == 2500
    assert "2 pallets + 500 bolsas" in client.get("/").get_data(as_text=True)
