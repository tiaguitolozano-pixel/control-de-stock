from io import BytesIO

import pytest
from openpyxl import load_workbook

from stock import create_app, formato_numero
from stock.db import get_db, stock_por_id


@pytest.fixture
def app(tmp_path):
    return create_app({"TESTING": True, "DATABASE": str(tmp_path / "test.db")})


@pytest.fixture
def client(app):
    return app.test_client()


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
    data = {"empresa_id": empresa_id, "fecha": fecha, "remito": remito,
            "producto_id": [str(p) for p, _ in items], "cantidad": [str(c) for _, c in items], **extra}
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
    r = client.post("/productos/nuevo", data={"empresa_id": 1, "codigo": "a1", "descripcion": "x"})
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
    assert "mayores a cero" in texto


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
    assert ws["E2"].value == 10
    r = client.get("/movimientos/excel?tipo=INGRESO")
    ws = load_workbook(BytesIO(r.data)).active
    assert ws["C2"].value == "R-1"


def test_filtro_movimientos_por_fecha(client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "10")], remito="VIEJO", fecha="2026-01-01")
    movimiento(client, "ingreso", [(1, "10")], remito="NUEVO", fecha="2026-11-01")
    texto = client.get("/movimientos?desde=2026-06-01").get_data(as_text=True)
    assert ">NUEVO<" in texto and ">VIEJO<" not in texto


def test_formato_numero():
    assert formato_numero(1234.5) == "1.234,5"
    assert formato_numero(10.0) == "10"
