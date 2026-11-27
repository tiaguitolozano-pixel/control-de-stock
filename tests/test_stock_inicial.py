"""Pedido del 27/11: al pasar de Cristal a este programa, el stock inicial se carga con el producto
(en lugar del stock mínimo, que queda como opción escondida)."""
from html import unescape

from stock.db import get_db, stock_por_id

from test_stock import crear_empresa, crear_producto, movimiento


def texto(r):
    return unescape(r.get_data(as_text=True))


def nuevo_producto(client, codigo="BG-25", lotes=(), fecha="2026-11-27", **extra):
    """lotes: [(lote, vencimiento, cantidad)] del stock inicial."""
    data = {"empresa_id": "1", "codigo": codigo, "descripcion": "Butilglicol x 25 kg", "unidad": "Bolsas",
            "unidades_por_pallet": "40", "kilos_por_unidad": "25", "si_fecha": fecha,
            "si_lote": [l[0] for l in lotes], "si_vencimiento": [l[1] for l in lotes],
            "si_cantidad": [l[2] for l in lotes], **extra}
    return client.post("/productos/nuevo", data=data)


def remitos(app):
    with app.app_context():
        return [dict(r) for r in get_db().execute(
            """SELECT m.tipo, m.remito, m.fecha, m.empresa_id, m.anulado, l.lote, l.vencimiento, i.cantidad
               FROM movimientos m JOIN movimiento_items i ON i.movimiento_id = m.id
               JOIN lotes l ON l.id = i.lote_id ORDER BY i.id""")]


def stock(app, pid=1):
    with app.app_context():
        return stock_por_id([pid])[pid]


def test_el_producto_nuevo_se_carga_con_su_stock_inicial(app, client):
    crear_empresa(client)
    pagina = texto(client.get("/productos/nuevo"))
    assert "Stock inicial" in pagina and 'id="tarjeta-stock-inicial"' in pagina
    # El stock mínimo ya no está a la vista: queda como opción.
    assert "Aviso de stock mínimo (opcional)" in pagina
    r = nuevo_producto(client, lotes=[("L-0915", "2027-09-15", "100"), ("l-0915", "", "20"),
                                      ("", "", "12,5"), ("", "", "")])
    assert r.status_code == 302
    mensajes = texto(client.get(r.headers["Location"]))
    assert "Producto guardado. Stock inicial: 132,5 bolsas en 2 lotes (remito STOCK INICIAL)." in mensajes
    assert stock(app) == 132.5
    # Un ingreso con el remito STOCK INICIAL, la fecha elegida y un renglón por lote (el mismo lote se suma).
    assert remitos(app) == [
        {"tipo": "INGRESO", "remito": "STOCK INICIAL", "fecha": "2026-11-27", "empresa_id": 1, "anulado": 0,
         "lote": "L-0915", "vencimiento": "2027-09-15", "cantidad": 120.0},
        {"tipo": "INGRESO", "remito": "STOCK INICIAL", "fecha": "2026-11-27", "empresa_id": 1, "anulado": 0,
         "lote": "", "vencimiento": None, "cantidad": 12.5},
    ]
    # Se ve en el stock y en la ficha como cualquier ingreso.
    assert "STOCK INICIAL" in texto(client.get("/productos/1"))
    assert "132,5" in texto(client.get("/"))


def test_sin_stock_inicial_no_se_crea_ningun_remito(app, client):
    crear_empresa(client)
    r = nuevo_producto(client)
    assert r.status_code == 302
    assert remitos(app) == []
    assert "Producto guardado." in texto(client.get(r.headers["Location"]))


def test_si_el_stock_inicial_tiene_errores_no_se_guarda_nada(app, client):
    crear_empresa(client)
    r = nuevo_producto(client, lotes=[("L1", "31/02/2027", "10"), ("L2", "", "diez"), ("L3", "2027-01-01", "")])
    assert r.status_code == 200
    pagina = texto(r)
    assert "Stock inicial: la fecha de vencimiento no es válida." in pagina
    assert "Stock inicial: la cantidad tiene que ser mayor a cero." in pagina
    # Ni el producto ni el stock: y el formulario conserva lo escrito.
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM productos").fetchone()[0] == 0
    assert remitos(app) == []
    assert 'value="L2"' in pagina and 'value="diez"' in pagina and 'value="BG-25"' in pagina
    # Un mismo lote no puede tener dos vencimientos.
    r = nuevo_producto(client, lotes=[("L1", "2027-01-01", "10"), ("L1", "2027-02-01", "5")])
    assert "Stock inicial: el lote L1 está dos veces con distinto vencimiento." in texto(r)
    r = nuevo_producto(client, lotes=[("L1", "", "10")], fecha="")
    assert "Stock inicial: la fecha no es válida." in texto(r)
    # Si el problema es del producto (código repetido), tampoco queda el stock inicial.
    assert nuevo_producto(client, lotes=[("L1", "", "10")]).status_code == 302
    r = nuevo_producto(client, lotes=[("L9", "", "7")])
    assert "Esa empresa ya tiene un producto con ese código." in texto(r)
    assert [x["lote"] for x in remitos(app)] == ["L1"]


def test_el_stock_inicial_se_carga_y_se_corrige_desde_el_producto(app, client):
    crear_empresa(client)
    crear_producto(client)  # A1, sin stock inicial
    datos = {"codigo": "A1", "descripcion": "Caja chica", "unidad": "cajas", "activo": "1"}
    # Editando el producto se puede cargar.
    assert 'id="tarjeta-stock-inicial"' in texto(client.get("/productos/1/editar"))
    r = client.post("/productos/1/editar", data={
        **datos, "si_fecha": "2026-11-27", "si_lote": ["C-1"], "si_vencimiento": [""], "si_cantidad": ["30"]})
    assert r.status_code == 302
    assert stock(app) == 30
    # Ya cargado, se ve en el producto con sus lotes y se puede corregir: cambiar el lote y la cantidad.
    pagina = texto(client.get("/productos/1/editar"))
    assert 'id="tarjeta-stock-inicial"' in pagina and 'value="C-1"' in pagina and 'value="30"' in pagina
    assert "Este producto ya tiene remitos" not in pagina
    r = client.post("/productos/1/editar", data={
        **datos, "si_fecha": "2026-11-27", "si_lote": ["C-2"], "si_vencimiento": [""], "si_cantidad": ["99"]})
    assert "Producto guardado. Stock inicial corregido: 99 cajas en 1 lote." in texto(client.get(r.headers["Location"]))
    assert stock(app) == 99
    assert [(x["remito"], x["lote"], x["cantidad"]) for x in remitos(app)] == [("STOCK INICIAL", "C-2", 99)]
    with app.app_context():
        # El lote mal escrito no queda dando vueltas, y la corrección queda anotada en el remito.
        assert get_db().execute("SELECT COUNT(*) FROM lotes WHERE lote = 'C-1'").fetchone()[0] == 0
        detalle = get_db().execute("SELECT detalle FROM cambios").fetchone()[0]
    assert "Stock inicial de A1, corregido desde el producto:" in detalle
    assert "Se quitó A1 - Caja chica (lote C-1): 30 cajas" in detalle
    assert "Se agregó A1 - Caja chica (lote C-2): 99 cajas" in detalle
    # Guardar sin tocar nada no cambia nada ni anota nada.
    client.post("/productos/1/editar", data={
        **datos, "si_fecha": "2026-11-27", "si_lote": ["C-2"], "si_vencimiento": [""], "si_cantidad": ["99"]})
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM cambios").fetchone()[0] == 1
    # Si se anula el stock inicial (por ejemplo, estaba mal), se puede cargar uno nuevo desde el producto.
    client.post("/movimientos/1/anular", data={"motivo": "cantidad mal"})
    pagina = texto(client.get("/productos/1/editar"))
    assert 'id="tarjeta-stock-inicial"' in pagina and 'value="C-2"' not in pagina


def test_desde_un_remito_el_producto_se_crea_sin_stock_inicial(app, client):
    crear_empresa(client)
    volver = "/movimientos/nuevo/ingreso?empresa_id=1"
    assert 'id="tarjeta-stock-inicial"' not in texto(client.get("/productos/nuevo", query_string={"volver": volver}))
    r = nuevo_producto(client, lotes=[("L1", "", "50")], volver=volver)
    assert r.status_code == 302 and r.headers["Location"].endswith(volver)
    assert remitos(app) == []
    # Y en el ingreso se carga como siempre.
    assert movimiento(client, "ingreso", [(1, "50", "L1")]).status_code == 302
    assert stock(app) == 50


def test_el_stock_minimo_se_ve_solo_si_algun_producto_lo_usa(app, client):
    crear_empresa(client)
    crear_producto(client)
    assert "Mínimo" not in texto(client.get("/productos"))
    assert "Bajo el mínimo" not in texto(client.get("/"))
    assert "Stock mínimo" not in texto(client.get("/productos/1"))
    crear_producto(client, codigo="A2", minimo="10")
    assert "Mínimo" in texto(client.get("/productos"))
    assert "Bajo el mínimo" in texto(client.get("/"))
    assert "Stock mínimo" in texto(client.get("/productos/2"))
    # Al editarlo, el aviso de stock mínimo aparece abierto con su valor.
    pagina = texto(client.get("/productos/2/editar"))
    assert '<details class="campo" open>' in pagina and 'value="10"' in pagina
