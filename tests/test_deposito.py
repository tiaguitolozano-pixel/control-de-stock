"""Pruebas de la etapa 2: pallets con etiqueta QR, ubicaciones y la pantalla de la terminal."""
import sqlite3
from io import BytesIO

from openpyxl import load_workbook

from stock import create_app
from stock import pallets as pl
from stock.db import get_db, stock_por_id
from stock.qr import qr_svg
from test_stock import crear_empresa, crear_producto, crear_producto_pallet, movimiento, planilla


def contenido(app, pallet_id):
    """{lote_id: cantidad} de un pallet."""
    with app.app_context():
        return {r["lote_id"]: r["cantidad"] for r in pl.contenido(pallet_ids=[pallet_id])}


def cantidad_pallets(app):
    with app.app_context():
        return get_db().execute("SELECT COUNT(*) FROM pallets").fetchone()[0]


def preparar(client, por_pallet="40"):
    """Empresa 1 con el producto 1 (bolsas, 40 por pallet)."""
    crear_empresa(client)
    crear_producto_pallet(client, codigo="B1", por_pallet=por_pallet)


def ingreso_con_pallets(client, items, remito="R-1", empresa_id=1):
    r = movimiento(client, "ingreso", items, remito=remito, empresa_id=empresa_id, crear_pallets="1")
    assert r.status_code == 302, r.data
    return r


def estibas(client, galpon=16, calle=1, desde=1, hasta=10):
    r = client.post("/deposito/estibas", data={"galpon": galpon, "calle": calle, "desde": desde, "hasta": hasta})
    assert r.status_code == 302


def despachar(client, lineas, remito="E-1", **extra):
    """lineas: (pallet_id, producto_id, lote_id, cantidad)."""
    return client.post("/terminal/despachar", data={
        "accion": "confirmar", "remito": remito, "fecha": "2026-11-20",
        "l_pallet": [str(l[0]) for l in lineas], "l_producto": [str(l[1]) for l in lineas],
        "l_lote": [str(l[2]) for l in lineas], "l_cant": [str(l[3]) for l in lineas], **extra,
    })


def test_pantallas_nuevas_cargan(client):
    for url in ["/deposito", "/pallets", "/pallets/armar", "/terminal", "/terminal/consultar",
                "/terminal/ubicar", "/terminal/despachar", "/pallets/etiquetas", "/deposito/etiquetas",
                "/pallets/excel"]:
        assert client.get(url).status_code == 200, url


def test_qr_en_svg():
    svg = qr_svg("P000001")
    assert svg.startswith("<svg") and 'viewBox="0 0 29 29"' in svg and "<path" in svg
    assert 'viewBox="0 0 29 29"' in qr_svg("G16-C2-E07")  # también entra en el QR más chico


def test_ingreso_arma_pallets_completos_y_uno_con_lo_que_sobra(app, client):
    preparar(client)
    r = ingreso_con_pallets(client, [(1, "100", "L1", "31/12/2027")])
    pagina = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Se armaron 3 pallets" in pagina and "P000001" in pagina and "P000003" in pagina
    assert [contenido(app, i) for i in (1, 2, 3)] == [{1: 40}, {1: 40}, {1: 20}]
    etiquetas = client.get("/pallets/etiquetas?movimiento_id=1").get_data(as_text=True)
    assert etiquetas.count('class="etiqueta"') == 3
    assert "Pallet 3 de 3" in etiquetas and "31/12/2027" in etiquetas and "<svg" in etiquetas
    assert 'class="hoja n2"' in client.get("/pallets/etiquetas?ids=1,2&por_hoja=2").get_data(as_text=True)


def test_ingreso_sin_la_casilla_no_arma_pallets(app, client):
    preparar(client)
    assert movimiento(client, "ingreso", [(1, "100")]).status_code == 302
    assert cantidad_pallets(app) == 0


def test_producto_sin_unidades_por_pallet_avisa(app, client):
    crear_empresa(client)
    crear_producto(client)  # sin unidades por pallet
    r = ingreso_con_pallets(client, [(1, "10")])
    assert "no tiene cargadas las unidades por pallet" in client.get(r.headers["Location"]).get_data(as_text=True)
    assert cantidad_pallets(app) == 0


def test_leer_codigos_del_lector(app, client):
    preparar(client)
    ingreso_con_pallets(client, [(1, "40")])
    estibas(client, galpon=16, calle=2, hasta=10)
    with app.test_request_context():
        for texto in ("G16-C2-E07", "g16 c2 e7", "C2/E07", "16'C2'E07", "G16C2E07"):
            tipo, fila = pl.leer_codigo(texto)
            assert tipo == "ubicacion" and (fila["calle"], fila["estiba"]) == (2, 7), texto
        for texto in ("P000001", "p-1", " P1 "):
            tipo, fila = pl.leer_codigo(texto)
            assert tipo == "pallet" and fila["id"] == 1, texto
        assert pl.leer_codigo("P000099")[0] is None
        assert pl.leer_codigo("G16-C9-E01")[0] is None
        assert pl.leer_codigo("hola")[0] is None
        assert pl.leer_codigo("")[0] is None
        pl.crear_estibas("17", 2, 7, 7)  # otra calle 2, estiba 7 en el galpón 17
        tipo, mensaje = pl.leer_codigo("C2-E07")
        assert tipo is None and "más de un galpón" in mensaje
        assert pl.leer_codigo("G17-C2-E07")[1]["galpon"] == "17"


def test_crear_estibas_y_mapa(app, client):
    estibas(client, galpon=16, calle=1, hasta=12)
    pagina = client.get("/deposito?galpon=16").get_data(as_text=True)
    assert "Calle 1" in pagina and "12 estibas" in pagina
    r = client.post("/deposito/estibas", data={"galpon": 16, "calle": 1, "desde": 1, "hasta": 12},
                    follow_redirects=True)
    assert "ya existían" in r.get_data(as_text=True)
    r = client.post("/deposito/estibas", data={"galpon": 16, "calle": 2, "desde": 5, "hasta": 2},
                    follow_redirects=True)
    assert "Indicá hasta qué número" in r.get_data(as_text=True)
    with app.app_context():
        assert len(pl.ubicaciones(galpon="16")) == 12
        assert pl.ubicaciones(galpon="16")[2]["codigo"] == "G16-C1-E03"
    etiquetas = client.get("/deposito/etiquetas?galpon=16&calle=1").get_data(as_text=True)
    assert etiquetas.count('class="etiqueta"') == 12 and '<div class="numero">12</div>' in etiquetas
    assert 'class="hoja n4"' in client.get("/deposito/etiquetas?galpon=16&por_hoja=4").get_data(as_text=True)


def ubicacion_de(app, pallet_id):
    with app.app_context():
        u = get_db().execute(
            "SELECT u.codigo FROM pallets p LEFT JOIN ubicaciones u ON u.id = p.ubicacion_id WHERE p.id = ?",
            (pallet_id,),
        ).fetchone()
        return u[0]


def test_terminal_ubica_pallet_y_despues_estiba(app, client):
    preparar(client)
    ingreso_con_pallets(client, [(1, "120")])
    estibas(client)
    r = client.post("/terminal/ubicar", data={"codigo": "P000001"})
    assert "Ahora escaneá la estiba" in r.get_data(as_text=True)
    r = client.post("/terminal/ubicar", data={"pallet_id": "1", "codigo": "G16-C1-E03"}, follow_redirects=True)
    assert "P000001 quedó en C1-E03" in r.get_data(as_text=True)
    assert ubicacion_de(app, 1) == "G16-C1-E03"
    # Modo estiba: primero la estiba y después varios pallets.
    r = client.post("/terminal/ubicar", data={"codigo": "C1-E04"})
    assert "Modo estiba" in r.get_data(as_text=True)
    with app.app_context():
        e4 = get_db().execute("SELECT id FROM ubicaciones WHERE codigo = 'G16-C1-E04'").fetchone()[0]
    for codigo in ("P000002", "P000003"):
        r = client.post("/terminal/ubicar", data={"ubicacion_id": str(e4), "codigo": codigo})
        assert f"{codigo} quedó en C1-E04" in r.get_data(as_text=True)
    assert ubicacion_de(app, 2) == ubicacion_de(app, 3) == "G16-C1-E04"
    r = client.post("/terminal/ubicar", data={"ubicacion_id": str(e4), "accion": "soltar"})
    assert "Modo estiba" not in r.get_data(as_text=True)
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM pallet_ubicaciones").fetchone()[0] == 3
    # Consultar una estiba y un pallet
    pagina = client.get("/terminal/consultar?codigo=G16-C1-E04").get_data(as_text=True)
    assert "P000002" in pagina and "P000003" in pagina
    pagina = client.get("/terminal/consultar?codigo=P000001").get_data(as_text=True)
    assert "C1-E03" in pagina and "40 bolsas" in pagina
    assert "No reconozco" in client.get("/terminal/consultar?codigo=xyz").get_data(as_text=True)
    # Se ve en el mapa, en la estiba y en el listado de lotes
    assert "C1-E03" in client.get("/lotes").get_data(as_text=True)
    assert "P000002" in client.get(f"/deposito/ubicacion/{e4}").get_data(as_text=True)


def test_terminal_despacha_pallets_enteros_y_parciales(app, client):
    preparar(client)
    ingreso_con_pallets(client, [(1, "100")])  # pallets de 40, 40 y 20
    r = client.post("/terminal/despachar", data={"codigo": "P000001"})
    pagina = r.get_data(as_text=True)
    assert 'name="l_pallet" value="1"' in pagina and "Confirmar egreso" in pagina
    r = despachar(client, [(1, 1, 1, "40"), (3, 1, 1, "5")], remito="")
    assert "Escribí el número de remito" in r.get_data(as_text=True)
    r = despachar(client, [(1, 1, 1, "40"), (3, 1, 1, "5")], chofer="Juan Pérez", patente_camion="ab 123 cd")
    assert r.status_code == 302
    assert "Egreso guardado" in client.get(r.headers["Location"]).get_data(as_text=True)
    assert stock_por_id_app(app, 1) == 55
    assert contenido(app, 1) == {} and contenido(app, 2) == {1: 40} and contenido(app, 3) == {1: 15}
    with app.app_context():
        m = get_db().execute("SELECT * FROM movimientos WHERE tipo = 'EGRESO'").fetchone()
        assert (m["remito"], m["chofer"], m["patente_camion"]) == ("E-1", "Juan Pérez", "AB123CD")
    detalle = client.get(f"/movimientos/{m['id']}").get_data(as_text=True)
    assert "Pallets de los que salió" in detalle and "P000003" in detalle
    # No deja sacar más de lo que tiene el pallet ni escanear un pallet vacío.
    r = despachar(client, [(3, 1, 1, "16")], remito="E-2")
    assert "tiene 15" in r.get_data(as_text=True)
    r = client.post("/terminal/despachar", data={"codigo": "P000001"})
    assert "está vacío" in r.get_data(as_text=True)


def test_terminal_enter_sin_codigo_no_pierde_la_lista(app, client):
    preparar(client)
    ingreso_con_pallets(client, [(1, "100")])
    # Enter en una cantidad: vuelve la misma lista, con la cantidad cambiada y sin mensaje de error.
    r = client.post("/terminal/despachar", data={
        "accion": "escanear", "codigo": "", "l_pallet": ["3"], "l_producto": ["1"], "l_lote": ["1"], "l_cant": ["7"]})
    pagina = r.get_data(as_text=True)
    assert 'name="l_pallet" value="3"' in pagina and 'value="7"' in pagina
    assert "No llegó ningún código" not in pagina
    r = client.post("/terminal/ubicar", data={"accion": "escanear", "codigo": "  "})
    assert "No llegó ningún código" not in r.get_data(as_text=True)


def stock_por_id_app(app, pid):
    with app.app_context():
        return stock_por_id([pid])[pid]


def test_despacho_no_mezcla_empresas(app, client):
    preparar(client)
    crear_empresa(client, "Otra SA")
    crear_producto_pallet(client, empresa_id=2, codigo="O1")
    ingreso_con_pallets(client, [(1, "40")])
    ingreso_con_pallets(client, [(2, "40")], remito="R-2", empresa_id=2)
    r = client.post("/terminal/despachar", data={
        "codigo": "P000002", "l_pallet": "1", "l_producto": "1", "l_lote": "1", "l_cant": "40"})
    assert "es de otra empresa" in r.get_data(as_text=True)
    r = client.post("/terminal/despachar", data={
        "codigo": "G16-C1-E01", "l_pallet": "1", "l_producto": "1", "l_lote": "1", "l_cant": "40"})
    assert "No existe la ubicación" in r.get_data(as_text=True)


def test_egreso_de_la_oficina_descuenta_pallets(app, client):
    preparar(client)
    ingreso_con_pallets(client, [(1, "100")])  # 40, 40, 20
    assert movimiento(client, "egreso", [(1, "50")], remito="E-1").status_code == 302
    # Un pallet completo entero y lo que falta del pallet empezado.
    assert [contenido(app, i) for i in (1, 2, 3)] == [{}, {1: 40}, {1: 10}]
    assert movimiento(client, "egreso", [(1, "45")], remito="E-2").status_code == 302
    assert [contenido(app, i) for i in (1, 2, 3)] == [{}, {}, {1: 5}]


def test_egreso_usa_primero_la_mercaderia_sin_etiqueta(app, client):
    preparar(client)
    assert movimiento(client, "ingreso", [(1, "30")]).status_code == 302  # sin pallets
    ingreso_con_pallets(client, [(1, "80")], remito="R-2")  # 2 pallets de 40
    assert movimiento(client, "egreso", [(1, "50")], remito="E-1").status_code == 302
    assert [contenido(app, i) for i in (1, 2)] == [{1: 20}, {1: 40}]
    with app.app_context():
        assert pl.sin_etiqueta_por_lote([1]) == {1: 0}


def test_anular_egreso_devuelve_la_mercaderia_a_sus_pallets(app, client):
    preparar(client)
    ingreso_con_pallets(client, [(1, "80")])
    assert despachar(client, [(1, 1, 1, "40"), (2, 1, 1, "10")]).status_code == 302
    r = client.post("/movimientos/2/anular", data={"motivo": "no salió"}, follow_redirects=True)
    assert "Remito anulado" in r.get_data(as_text=True)
    assert contenido(app, 1) == {1: 40} and contenido(app, 2) == {1: 40}


def test_no_anula_ingreso_si_sus_pallets_ya_se_movieron(app, client):
    preparar(client)
    ingreso_con_pallets(client, [(1, "80")])  # pallets 1 y 2
    assert movimiento(client, "ingreso", [(1, "100")], remito="R-2").status_code == 302  # sin pallets
    assert despachar(client, [(1, 1, 1, "10")]).status_code == 302
    r = client.post("/movimientos/1/anular", data={"motivo": "error"}, follow_redirects=True)
    pagina = r.get_data(as_text=True)
    assert "No se puede anular" in pagina and "P000001" in pagina
    with app.app_context():
        assert get_db().execute("SELECT anulado FROM movimientos WHERE id = 1").fetchone()[0] == 0


def test_no_anula_ingreso_con_pallets_armados_con_su_mercaderia(app, client):
    preparar(client)
    assert movimiento(client, "ingreso", [(1, "100")]).status_code == 302
    r = client.post("/pallets/armar", data={"lote_id": "1", "por_pallet": "40", "solo": "1"})
    assert r.status_code == 302 and cantidad_pallets(app) == 3
    r = client.post("/movimientos/1/anular", data={"motivo": "error"}, follow_redirects=True)
    assert "hay pallets armados con mercadería" in r.get_data(as_text=True)


def test_anular_ingreso_con_pallets_sin_movimientos(app, client):
    preparar(client)
    ingreso_con_pallets(client, [(1, "80")])
    r = client.post("/movimientos/1/anular", data={"motivo": "remito equivocado"}, follow_redirects=True)
    assert "Remito anulado" in r.get_data(as_text=True)
    assert contenido(app, 1) == {} and "P000001" not in client.get("/pallets").get_data(as_text=True)


def test_armar_pallets_para_el_stock_sin_etiqueta(app, client):
    preparar(client)
    assert movimiento(client, "ingreso", [(1, "100", "L1")]).status_code == 302
    pagina = client.get("/pallets/armar").get_data(as_text=True)
    assert "L1" in pagina and 'value="40"' in pagina
    r = client.post("/pallets/armar", data={"lote_id": "1", "por_pallet": "", "solo": ""}, follow_redirects=True)
    assert "Completá cuántas unidades" in r.get_data(as_text=True)
    r = client.post("/pallets/armar", data={"lote_id": "1", "por_pallet": "40", "solo": ""})
    assert "nuevos=1%2C2%2C3" in r.headers["Location"] or "nuevos=1,2,3" in r.headers["Location"]
    assert [contenido(app, i) for i in (1, 2, 3)] == [{1: 40}, {1: 40}, {1: 20}]
    assert "No hay mercadería sin etiqueta" in client.get("/pallets/armar").get_data(as_text=True)
    with app.app_context():
        _, error = pl.armar_pallets(1, 40)
        assert "no queda mercadería sin etiqueta" in error


def test_juntar_pallets_con_dos_lotes_y_despachar_uno(app, client):
    preparar(client)
    ingreso_con_pallets(client, [(1, "30", "L1"), (1, "10", "L2")])  # pallet 1: L1 30, pallet 2: L2 10
    r = client.post("/pallets/1/juntar", data={"codigo": "P000002"}, follow_redirects=True)
    assert "Se pasó todo lo del pallet P000002" in r.get_data(as_text=True)
    assert contenido(app, 1) == {1: 30, 2: 10} and contenido(app, 2) == {}
    etiqueta = client.get("/pallets/etiquetas?ids=1").get_data(as_text=True)
    assert "L1" in etiqueta and "L2" in etiqueta
    # Sale solo el lote L2 del pallet mezclado.
    assert despachar(client, [(1, 1, 1, "0"), (1, 1, 2, "10")]).status_code == 302
    assert contenido(app, 1) == {1: 30}
    r = client.post("/pallets/1/juntar", data={"codigo": "P000001"}, follow_redirects=True)
    assert "Es el mismo pallet" in r.get_data(as_text=True)


def test_sacar_y_volver_a_sumar_mercaderia_de_un_pallet(app, client):
    preparar(client)
    ingreso_con_pallets(client, [(1, "40")])
    client.post("/pallets/1/sacar", data={"lote_id": "1", "cantidad": "5"})
    assert contenido(app, 1) == {1: 35}
    with app.app_context():
        assert pl.sin_etiqueta_por_lote([1]) == {1: 5}
    r = client.post("/pallets/1/sumar", data={"lote_id": "1", "cantidad": "6"}, follow_redirects=True)
    assert "solo hay 5 sin etiqueta" in r.get_data(as_text=True)
    client.post("/pallets/1/sumar", data={"lote_id": "1", "cantidad": "5"})
    assert contenido(app, 1) == {1: 40}
    assert stock_por_id_app(app, 1) == 40  # armar o desarmar pallets no cambia el stock
    ficha = client.get("/pallets/1").get_data(as_text=True)
    assert "Se sacó del pallet" in ficha and "Se armó con mercadería sin etiqueta" in ficha


def test_cambiar_ubicacion_desde_la_ficha_y_desactivar_estiba(app, client):
    preparar(client)
    ingreso_con_pallets(client, [(1, "40")])
    estibas(client, hasta=3)
    client.post("/pallets/1/ubicar", data={"codigo": "C1-E02"})
    assert ubicacion_de(app, 1) == "G16-C1-E02"
    r = client.post("/deposito/ubicacion/2/activar", follow_redirects=True)
    assert "tiene pallets" in r.get_data(as_text=True)
    client.post("/pallets/1/ubicar", data={"ubicacion_id": ""})
    assert ubicacion_de(app, 1) is None
    r = client.post("/deposito/ubicacion/2/activar", follow_redirects=True)
    assert "Estiba desactivada" in r.get_data(as_text=True)
    r = client.post("/terminal/ubicar", data={"pallet_id": "1", "codigo": "C1-E02"})
    assert "está desactivada" in r.get_data(as_text=True)


def test_listado_de_pallets_filtros_y_excel(app, client):
    preparar(client)
    crear_producto_pallet(client, codigo="C9", por_pallet="10")
    ingreso_con_pallets(client, [(1, "40", "L1"), (2, "10", "LX")])
    pagina = client.get("/pallets?buscar=LX").get_data(as_text=True)
    assert "P000002" in pagina and "P000001" not in pagina
    assert "P000001" in client.get("/pallets?sin_ubicar=1").get_data(as_text=True)
    libro = load_workbook(BytesIO(client.get("/pallets/excel").data))
    filas = list(libro.active.values)
    assert filas[0][0] == "Pallet" and len(filas) == 3


def test_importar_arma_pallets(app, client):
    archivo = planilla([("Acme SA", "B1", "Bolsa", "Bolsas", 40, "L1", "", 90)],
                       encabezados=("Empresa", "Código", "Descripción", "Envase", "Por pallet", "Lote",
                                    "Vencimiento", "Cantidad"))
    r = client.post("/importar", data={"archivo": (archivo, "conteo.xlsx"), "fecha": "2026-11-20",
                                       "remito": "STOCK INICIAL", "crear_pallets": "1"},
                    content_type="multipart/form-data")
    pagina = r.get_data(as_text=True)
    assert "Pallets armados con su etiqueta: <strong>3</strong>" in pagina and "Imprimir las 3 etiquetas" in pagina
    assert cantidad_pallets(app) == 3


def test_migracion_de_la_version_anterior(tmp_path):
    """Una base de la segunda versión (con envases y transporte) suma las tablas de pallets."""
    from stock.db import ESQUEMA, MIGRACIONES
    ruta = tmp_path / "datos" / "stock.db"
    ruta.parent.mkdir()
    con = sqlite3.connect(ruta)
    con.executescript(ESQUEMA)
    con.executescript(f"BEGIN;\n{MIGRACIONES[0]}\nPRAGMA user_version = 1;\nCOMMIT;")
    con.executescript("""
        INSERT INTO empresas (nombre) VALUES ('Copsa');
        INSERT INTO productos (empresa_id, codigo, descripcion, unidad, unidades_por_pallet)
            VALUES (1, 'C1', 'Urea', 'Bolsas', 40);
        INSERT INTO lotes (producto_id, lote) VALUES (1, 'L1');
        INSERT INTO movimientos (tipo, fecha, remito, empresa_id) VALUES ('INGRESO', '2026-11-10', 'R1', 1);
        INSERT INTO movimiento_items (movimiento_id, producto_id, lote_id, cantidad) VALUES (1, 1, 1, 100);
    """)
    con.commit()
    con.close()
    app = create_app({"TESTING": True, "DATABASE": str(ruta)})
    with app.app_context():
        db = get_db()
        assert db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRACIONES)
        tablas = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {"ubicaciones", "pallets", "pallet_movs", "pallet_ubicaciones"} <= tablas
        assert pl.sin_etiqueta_por_lote([1]) == {1: 100}
    assert list((tmp_path / "datos" / "respaldos").glob("antes-de-actualizar-v1_*.db"))
