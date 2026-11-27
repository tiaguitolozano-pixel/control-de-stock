"""Pedido del 27/11 (noche): las fechas cuentan (no se saca mercadería antes de que entre), el stock inicial se
corrige desde el producto, y un remito cargado con un error se puede editar o borrar (queda anotado)."""
import os
from html import unescape
from io import BytesIO

from stock import pallets as pl
from stock.db import get_db, stock_por_id
from stock.servicios import registrar_movimiento
from test_kilos_insumos_fotos import JPG, producto_con_kilos
from test_stock import crear_empresa, crear_producto, movimiento, planilla


def texto(r):
    return unescape(r.get_data(as_text=True))


def seguir(client, r):
    """La página a la que manda una redirección (con los mensajes)."""
    assert r.status_code == 302, texto(r)[:3000]
    return texto(client.get(r.headers["Location"]))


def stock(app, pid=1):
    with app.app_context():
        return stock_por_id([pid])[pid]


def lote_id(app, lote, pid=1):
    with app.app_context():
        return get_db().execute("SELECT id FROM lotes WHERE producto_id = ? AND lote = ?", (pid, lote)).fetchone()[0]


def sql(app, consulta, *params):
    with app.app_context():
        return [tuple(r) for r in get_db().execute(consulta, params)]


def en_pallets(app, lid):
    with app.app_context():
        return sorted(r["cantidad"] for r in pl.contenido(lote_ids=[lid]))


def cambios(app):
    return sql(app, "SELECT accion, movimiento_id, detalle FROM cambios ORDER BY id")


def editar(client, movimiento_id, tipo, items, remito="R-1", fecha="2026-11-10", **extra):
    """Como test_stock.movimiento, pero corrigiendo un remito guardado."""
    data = {"empresa_id": 1, "fecha": fecha, "remito": remito,
            "producto_id": [str(i[0]) for i in items], "cantidad": [str(i[1]) for i in items], **extra}
    if tipo == "ingreso":
        data["lote"] = [i[2] if len(i) > 2 else "" for i in items]
        data["vencimiento"] = [i[3] if len(i) > 3 else "" for i in items]
    else:
        data["lote_id"] = [str(i[2] if len(i) > 2 else i[0]) for i in items]
    return client.post(f"/movimientos/{movimiento_id}/editar", data=data)


def egreso_viejo(app, fecha, cantidad, lid, remito="E-viejo", pid=1):
    """Un egreso cargado antes de que el programa revisara las fechas (hoy ya no se puede cargar así)."""
    with app.app_context():
        registrar_movimiento("EGRESO", 1, fecha, remito, None, [(pid, lid, cantidad)])
        get_db().commit()


def producto_bolsas(client, codigo="BG-25", lotes=(), fecha="2026-11-27"):
    """Producto en bolsas (40 por pallet) con su stock inicial: lotes = [(lote, vencimiento, cantidad)]."""
    r = client.post("/productos/nuevo", data={
        "empresa_id": "1", "codigo": codigo, "descripcion": "Dióxido de titanio", "unidad": "Bolsas",
        "unidades_por_pallet": "40", "si_fecha": fecha,
        "si_lote": [l[0] for l in lotes], "si_vencimiento": [l[1] for l in lotes],
        "si_cantidad": [l[2] for l in lotes]})
    assert r.status_code == 302, texto(r)[:3000]


def editar_stock_inicial(client, lotes, fecha, pid=1, codigo="BG-25", **extra):
    return client.post(f"/productos/{pid}/editar", data={
        "codigo": codigo, "descripcion": "Dióxido de titanio", "unidad": "Bolsas", "unidades_por_pallet": "40",
        "activo": "1", "si_fecha": fecha, "si_lote": [l[0] for l in lotes],
        "si_vencimiento": [l[1] for l in lotes], "si_cantidad": [l[2] for l in lotes], **extra})


# ----------------------------------------------------------------- fechas


def test_no_se_puede_sacar_mercaderia_antes_de_que_entre(app, client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "100", "L1")], fecha="2026-11-27")
    lid = lote_id(app, "L1")
    r = movimiento(client, "egreso", [(1, "10", lid)], remito="E-1", fecha="2026-11-05")
    assert r.status_code == 200
    assert ("No hay stock de A1 - Caja chica (lote L1) el 05/11/2026: ese lote entró recién el 27/11/2026. "
            "Revisá la fecha de este remito, o la del ingreso de ese lote (por ejemplo, la del stock inicial).") in texto(r)
    assert stock(app) == 100
    # Lo que entra un día puede salir ese mismo día.
    assert movimiento(client, "egreso", [(1, "10", lid)], remito="E-1", fecha="2026-11-27").status_code == 302
    assert stock(app) == 90


def test_un_egreso_no_puede_dejar_sin_stock_a_los_egresos_de_despues(app, client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "100", "L1")], fecha="2026-11-01")
    lid = lote_id(app, "L1")
    assert movimiento(client, "egreso", [(1, "80", lid)], remito="E-1", fecha="2026-11-10").status_code == 302
    # Hoy quedan 20; con fecha 05/11 también se pueden sacar 20 como máximo (el 10/11 salen 80).
    r = movimiento(client, "egreso", [(1, "30", lid)], remito="E-2", fecha="2026-11-05")
    assert ("No hay stock suficiente de A1 - Caja chica (lote L1) el 05/11/2026: contando los remitos de ese día "
            "y de después, con esa fecha se pueden sacar como máximo 20 cajas y querés sacar 30.") in texto(r)
    assert movimiento(client, "egreso", [(1, "20", lid)], remito="E-2", fecha="2026-11-05").status_code == 302
    assert stock(app) == 0


def test_los_lotes_que_quedaron_en_negativo_se_avisan(app, client):
    crear_empresa(client)
    producto_bolsas(client, lotes=[("T-1", "", "500")], fecha="2026-11-27")
    egreso_viejo(app, "2026-11-05", 139, lote_id(app, "T-1"))
    pagina = texto(client.get("/"))
    assert 'id="aviso-negativos"' in pagina and "Hay 1 lote que quedó con saldo negativo" in pagina
    assert "negativo desde el 05/11/2026 (llega a -139 bolsas)" in pagina
    assert "/productos/1/editar?volver=" in pagina and "#tarjeta-stock-inicial" in pagina
    libro = texto(client.get("/movimientos?empresa_id=1&producto=BG-25"))
    assert "El saldo de este lote queda negativo" in libro and 'class="num saldo negativo"' in libro
    # Cada remito del historial tiene su lápiz para corregirlo.
    assert 'href="/movimientos/1/editar"' in libro and 'href="/movimientos/2/editar"' in libro


# ----------------------------------------------------- stock inicial desde el producto


def test_el_stock_inicial_con_la_fecha_mal_se_corrige_desde_el_producto(app, client):
    """El caso de Tiago: el stock inicial quedó con la fecha de hoy y los egresos son de antes."""
    crear_empresa(client)
    producto_bolsas(client, lotes=[("T-1", "2027-05-01", "500")], fecha="2026-11-27")
    lid = lote_id(app, "T-1")
    egreso_viejo(app, "2026-11-05", 139, lid)
    egreso_viejo(app, "2026-11-12", 100, lid, remito="E-viejo-2")
    pagina = texto(client.get("/productos/1/editar"))
    assert 'value="2026-11-27"' in pagina and 'value="T-1"' in pagina and 'value="500"' in pagina
    assert "Este producto ya tiene otros remitos" in pagina
    # Con otra fecha que todavía es tarde se guarda, pero avisa que sigue en negativo.
    r = editar_stock_inicial(client, [("T-1", "2027-05-01", "500")], "2026-11-10")
    pagina = seguir(client, r)
    assert "Producto guardado. Stock inicial corregido: 500 bolsas en 1 lote." in pagina
    assert "Ojo: el saldo de BG-25 (lote T-1) sigue quedando negativo desde el 05/11/2026" in pagina
    # Con la fecha buena ya no queda nada en negativo.
    r = editar_stock_inicial(client, [("T-1", "2027-05-01", "500")], "2026-11-01")
    assert "Ojo:" not in seguir(client, r)
    assert 'id="aviso-negativos"' not in texto(client.get("/"))
    assert sql(app, "SELECT fecha FROM movimientos WHERE remito = 'STOCK INICIAL'") == [("2026-11-01",)]
    assert stock(app) == 261
    detalles = [c[2] for c in cambios(app)]
    assert detalles[-1] == ("Stock inicial de BG-25, corregido desde el producto:\n"
                            "Fecha: 10/11/2026 → 01/11/2026")
    # Ahora no se puede volver a poner una fecha posterior a los egresos: dice desde cuándo hace falta.
    r = editar_stock_inicial(client, [("T-1", "2027-05-01", "500")], "2026-11-20")
    assert r.status_code == 200
    assert ("Stock inicial: el saldo de BG-25 - Dióxido de titanio (lote T-1) quedaría en -139 bolsas el 05/11/2026, "
            "porque ya salió mercadería de ese lote con esa fecha o antes.") in texto(r)
    assert "Stock inicial: si la mercadería ya estaba en el depósito el 05/11/2026, poné esa fecha o una anterior." in texto(r)
    # Ni tampoco menos de lo que ya salió (acá la fecha está bien: no sugiere cambiarla).
    r = editar_stock_inicial(client, [("T-1", "2027-05-01", "200")], "2026-11-01")
    assert "quedaría en -39 bolsas el 12/11/2026" in texto(r) and "poné esa fecha" not in texto(r)
    assert sql(app, "SELECT fecha FROM movimientos WHERE remito = 'STOCK INICIAL'") == [("2026-11-01",)]
    assert stock(app) == 261


def test_el_stock_inicial_se_corrige_lote_vencimiento_y_cantidad(app, client):
    crear_empresa(client)
    producto_bolsas(client, lotes=[("T-1", "2027-05-01", "100"), ("T-2", "", "12,25")], fecha="2026-11-01")
    r = editar_stock_inicial(client, [("T-1", "2027-06-30", "120"), ("T-3", "", "12,25")], "2026-11-01")
    assert "Stock inicial corregido: 132,25 bolsas en 2 lotes." in seguir(client, r)
    assert sql(app, "SELECT lote, vencimiento FROM lotes ORDER BY id") == [("T-1", "2027-06-30"), ("T-3", None)]
    detalle = cambios(app)[-1][2]
    assert "BG-25 - Dióxido de titanio (lote T-1): 100 → 120 bolsas" in detalle
    assert "BG-25 - Dióxido de titanio (lote T-1): vencimiento 01/05/2027 → 30/06/2027" in detalle
    assert "Se quitó BG-25 - Dióxido de titanio (lote T-2): 12,25 bolsas" in detalle
    # Los decimales que no se ven redondeados no cambian solos al guardar el producto.
    with app.app_context():
        get_db().execute("UPDATE movimiento_items SET cantidad = 120.1234 WHERE cantidad = 120")
        get_db().commit()
    pagina = texto(client.get("/productos/1/editar"))
    assert 'value="120,1234"' in pagina


def test_un_producto_con_remitos_puede_recibir_su_stock_inicial(app, client):
    crear_empresa(client)
    producto_bolsas(client)
    movimiento(client, "ingreso", [(1, "50", "T-1")], fecha="2026-11-20")
    # Se propone la fecha del primer remito del producto, para que ningún egreso quede antes.
    assert 'name="si_fecha" id="si_fecha" value="2026-11-20"' in texto(client.get("/productos/1/editar"))
    r = editar_stock_inicial(client, [("T-1", "", "30")], "2026-11-01")
    assert "Stock inicial: 30 bolsas en 1 lote (remito STOCK INICIAL)." in seguir(client, r)
    assert stock(app) == 80


def test_sacar_el_stock_inicial_desde_el_producto(app, client):
    crear_empresa(client)
    producto_bolsas(client, lotes=[("T-1", "", "100")], fecha="2026-11-01")
    lid = lote_id(app, "T-1")
    assert movimiento(client, "egreso", [(1, "30", lid)], remito="E-1", fecha="2026-11-10").status_code == 302
    # Si ya salió mercadería, no se puede sacar.
    r = editar_stock_inicial(client, [], "2026-11-01")
    assert "Stock inicial: el saldo de BG-25 - Dióxido de titanio (lote T-1) quedaría en -30 bolsas el 10/11/2026" in texto(r)
    assert stock(app) == 70
    # Sin el egreso, sí: se borra el remito STOCK INICIAL y queda anotado.
    assert client.post("/movimientos/2/borrar").status_code == 302
    r = editar_stock_inicial(client, [], "2026-11-01")
    assert "Producto guardado. Se borró su stock inicial." in seguir(client, r)
    assert sql(app, "SELECT COUNT(*) FROM movimientos") == [(0,)]
    assert sql(app, "SELECT COUNT(*) FROM lotes") == [(0,)]
    accion, _, detalle = cambios(app)[-1]
    assert accion == "BORRADO" and detalle.startswith("Ingreso del 01/11/2026, remito STOCK INICIAL, de Acme SA.")
    assert detalle.endswith("Se borró desde el producto: quedó sin stock inicial.")


def test_el_stock_inicial_de_importar_excel_se_separa_si_cambia_la_fecha(app, client):
    """Importar Excel arma un solo remito STOCK INICIAL por empresa con todos los productos (y sus pallets)."""
    archivo = planilla([("Acme SA", "BG-25", "Dióxido de titanio", "Bolsas", 40, "T-1", None, 100),
                        ("Acme SA", "UR-1", "Urea", "Bolsas", 40, "", None, 50)],
                       encabezados=("Empresa", "Código", "Descripción", "Envase", "Por pallet", "Lote",
                                    "Vencimiento", "Cantidad"))
    r = client.post("/importar", data={"fecha": "2026-11-27", "remito": "STOCK INICIAL", "crear_pallets": "1",
                                       "archivo": (archivo, "conteo.xlsx")}, content_type="multipart/form-data")
    assert "Importación terminada" in texto(r)
    lid = lote_id(app, "T-1")
    assert en_pallets(app, lid) == [20, 40, 40]
    pagina = texto(client.get("/productos/1/editar"))
    assert "Este stock inicial está en un remito junto con otros productos" in pagina
    r = editar_stock_inicial(client, [("T-1", "", "100")], "2026-11-01")
    assert "Stock inicial corregido" in seguir(client, r)
    # BG-25 pasó a su propio remito con la fecha nueva, con sus pallets; la urea quedó como estaba.
    assert sql(app, """SELECT m.id, m.fecha, p.codigo FROM movimientos m JOIN movimiento_items i ON i.movimiento_id = m.id
                       JOIN productos p ON p.id = i.producto_id ORDER BY m.id""") == [
        (1, "2026-11-27", "UR-1"), (2, "2026-11-01", "BG-25")]
    assert sql(app, "SELECT DISTINCT movimiento_id FROM pallets p JOIN pallet_movs pm ON pm.pallet_id = p.id "
                    "WHERE pm.lote_id = ?", lid) == [(2,)]
    assert en_pallets(app, lid) == [20, 40, 40]
    registro = cambios(app)
    assert registro[0][:2] == ("EDITADO", 1) and "Los renglones de BG-25 pasaron a un remito STOCK INICIAL propio" in registro[0][2]
    assert registro[1][:2] == ("EDITADO", 2) and "Fecha: 27/11/2026 → 01/11/2026" in registro[1][2]


# ------------------------------------------------------------- editar remitos


def test_editar_un_ingreso(app, client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "100", "L1", "2027-01-31")], fecha="2026-11-10")
    pagina = texto(client.get("/movimientos/1/editar"))
    assert "Editar ingreso · Remito R-1" in pagina and "Estás corrigiendo un remito ya guardado" in pagina
    r = editar(client, 1, "ingreso", [(1, "120", "L2", "2027-01-31")], remito="R-1B", fecha="2026-11-09",
               observaciones="Llegó con un pallet roto")
    pagina = seguir(client, r)
    assert "Remito corregido." in pagina and "Cambios de este remito" in pagina
    assert stock(app) == 120
    assert sql(app, "SELECT lote FROM lotes") == [("L2",)]  # el lote mal escrito no queda
    accion, mid, detalle = cambios(app)[-1]
    assert (accion, mid) == ("EDITADO", 1)
    for linea in ("Fecha: 10/11/2026 → 09/11/2026", "N° de remito: R-1 → R-1B",
                  "Observación: (vacío) → Llegó con un pallet roto", "Se quitó A1 - Caja chica (lote L1): 100 cajas",
                  "Se agregó A1 - Caja chica (lote L2): 120 cajas"):
        assert linea in detalle.split("\n"), linea
    assert "Llegó con un pallet roto" in texto(client.get("/movimientos/cambios"))


def test_un_ingreso_no_se_puede_corregir_si_ya_salio_esa_mercaderia(app, client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "100", "L1")], fecha="2026-11-10")
    lid = lote_id(app, "L1")
    assert movimiento(client, "egreso", [(1, "50", lid)], remito="E-1", fecha="2026-11-12").status_code == 302
    r = editar(client, 1, "ingreso", [(1, "30", "L1")])
    assert ("No se puede guardar: el saldo de A1 - Caja chica (lote L1) quedaría en -20 cajas el 12/11/2026, "
            "porque ya salió mercadería de ese lote con esa fecha o antes.") in texto(r)
    r = editar(client, 1, "ingreso", [(1, "100", "L1")], fecha="2026-11-13")
    assert "quedaría en -50 cajas el 12/11/2026" in texto(r)
    assert "si la mercadería ya estaba en el depósito el 12/11/2026, poné esa fecha o una anterior." in texto(r)
    assert sql(app, "SELECT fecha, cantidad FROM movimientos m JOIN movimiento_items i ON i.movimiento_id = m.id "
                    "WHERE m.id = 1") == [("2026-11-10", 100)]
    assert cambios(app) == []


def test_editar_un_egreso(app, client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "100", "L1")], fecha="2026-11-01")
    lid = lote_id(app, "L1")
    movimiento(client, "egreso", [(1, "30", lid)], remito="E-1", fecha="2026-11-10")
    pagina = texto(client.get("/movimientos/2/editar"))
    assert "Editar egreso · Remito E-1" in pagina and "En cada lote se muestra lo que hay sin contar este remito" in pagina
    # Lo que muestra el formulario no cuenta el mismo egreso (el & no puede quedar escapado como &amp;).
    assert "'&excluir=2'" in client.get("/movimientos/2/editar").get_data(as_text=True)
    lotes = client.get("/api/productos?empresa_id=1&excluir=2").get_json()[0]["lotes"]
    assert lotes[0]["stock"] == 100
    r = editar(client, 2, "egreso", [(1, "120", lid)], remito="E-1")
    assert "No hay stock suficiente de A1 - Caja chica (lote L1): hay 100 y querés sacar 120." in texto(r)
    r = editar(client, 2, "egreso", [(1, "30", lid)], remito="E-1", fecha="2026-10-30")
    assert "ese lote entró recién el 01/11/2026" in texto(r)
    assert stock(app) == 70
    assert "Remito corregido." in seguir(client, editar(client, 2, "egreso", [(1, "60", lid)], remito="E-1"))
    assert stock(app) == 40
    assert "A1 - Caja chica (lote L1): 30 → 60 cajas" in cambios(app)[-1][2]
    # Si no se cambia nada, no se anota nada.
    assert "No había nada para cambiar." in seguir(client, editar(client, 2, "egreso", [(1, "60", lid)], remito="E-1"))
    assert len(cambios(app)) == 1


def test_editar_un_ingreso_ajusta_los_pallets_que_armo(app, client):
    crear_empresa(client)
    producto_con_kilos(client, codigo="B1", kilos="25", por_pallet="40", unidad="Bolsas")
    movimiento(client, "ingreso", [(1, "100", "L1")], crear_pallets="1")
    lid = lote_id(app, "L1")
    assert en_pallets(app, lid) == [20, 40, 40]
    pagina = seguir(client, editar(client, 1, "ingreso", [(1, "60", "L1")]))
    assert "P000003 ya no hacía falta y se borró: si tenía etiqueta pegada, sacala." in pagina
    assert en_pallets(app, lid) == [20, 40]
    pagina = seguir(client, editar(client, 1, "ingreso", [(1, "130", "L1")]))
    assert "Hay pallets nuevos: imprimí sus etiquetas con el botón de abajo." in pagina
    assert en_pallets(app, lid) == [10, 40, 40, 40]
    detalle = cambios(app)[-1][2]
    assert "Se armaron los pallets P000004, P000005" in detalle


def test_editar_un_egreso_vuelve_a_elegir_los_pallets(app, client):
    crear_empresa(client)
    producto_con_kilos(client, codigo="B1", kilos="25", por_pallet="40", unidad="Bolsas")
    movimiento(client, "ingreso", [(1, "100", "L1")], crear_pallets="1")
    lid = lote_id(app, "L1")
    movimiento(client, "egreso", [(1, "30", lid)], remito="E-1", fecha="2026-11-12")
    assert sum(en_pallets(app, lid)) == 70
    seguir(client, editar(client, 2, "egreso", [(1, "50", lid)], remito="E-1", fecha="2026-11-12"))
    assert stock(app) == 50 and sum(en_pallets(app, lid)) == 50
    seguir(client, editar(client, 2, "egreso", [(1, "10", lid)], remito="E-1", fecha="2026-11-12"))
    assert stock(app) == 90 and sum(en_pallets(app, lid)) == 90


def test_un_remito_anulado_no_se_edita_pero_se_puede_borrar(app, client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "100", "L1")])
    client.post("/movimientos/1/anular", data={"motivo": "se canceló"})
    pagina = seguir(client, client.get("/movimientos/1/editar"))
    assert "Este remito está anulado: no se puede editar." in pagina
    assert seguir(client, client.post("/movimientos/1/borrar"))
    assert sql(app, "SELECT COUNT(*) FROM movimientos") == [(0,)]
    assert "Estaba anulado (se canceló)." in cambios(app)[-1][2]


# -------------------------------------------------------------- borrar remitos


def test_borrar_un_egreso_devuelve_la_mercaderia(app, client):
    crear_empresa(client)
    producto_con_kilos(client, codigo="B1", kilos="25", por_pallet="40", unidad="Bolsas")
    movimiento(client, "ingreso", [(1, "100", "L1")], crear_pallets="1")
    lid = lote_id(app, "L1")
    movimiento(client, "egreso", [(1, "30", lid)], remito="E-1", fecha="2026-11-12", chofer="Juan Pérez")
    pagina = texto(client.get("/movimientos/2/borrar"))
    assert "E-1" in pagina and "Sí, borrar" in pagina
    pagina = seguir(client, client.post("/movimientos/2/borrar"))
    assert "Egreso borrado (remito E-1 del 12/11/2026)." in pagina
    assert stock(app) == 100 and sum(en_pallets(app, lid)) == 100
    accion, mid, detalle = cambios(app)[-1]
    assert (accion, mid) == ("BORRADO", 2)
    assert detalle.split("\n")[:2] == ["Egreso del 12/11/2026, remito E-1, de Acme SA.",
                                       "B1 - Producto B1 (lote L1): 30 bolsas."]
    assert "Transporte: chofer Juan Pérez." in detalle
    lista = texto(client.get("/movimientos/cambios"))
    assert "E-1" in lista and "Egreso del 12/11/2026" in lista


def test_no_se_borra_un_ingreso_si_ya_salio_esa_mercaderia(app, client):
    crear_empresa(client)
    crear_producto(client)
    movimiento(client, "ingreso", [(1, "100", "L1")])
    movimiento(client, "egreso", [(1, "30", lote_id(app, "L1"))], remito="E-1", fecha="2026-11-12")
    pagina = texto(client.get("/movimientos/1/borrar"))
    assert ("No se puede borrar: el stock quedaría negativo para A1 (lote L1). "
            "Primero borrá los egresos que usaron esa mercadería.") in pagina
    r = client.post("/movimientos/1/borrar")
    assert r.status_code == 200 and stock(app) == 70
    assert cambios(app) == []


def test_las_fotos_de_un_remito_borrado_se_guardan_aparte(app, client):
    crear_empresa(client)
    crear_producto(client)
    r = client.post("/movimientos/nuevo/ingreso", data={
        "empresa_id": 1, "fecha": "2026-11-10", "remito": "R-1", "producto_id": ["1"], "cantidad": ["5"],
        "lote": [""], "vencimiento": [""], "fotos": [(BytesIO(JPG), "camion.jpg")]},
        content_type="multipart/form-data")
    assert r.status_code == 302
    archivo = sql(app, "SELECT archivo FROM movimiento_fotos")[0][0]
    carpeta = app.config["FOTOS"]
    assert os.path.exists(os.path.join(carpeta, *archivo.split("/")))
    seguir(client, client.post("/movimientos/1/borrar"))
    assert not os.path.exists(os.path.join(carpeta, *archivo.split("/")))
    assert os.path.exists(os.path.join(carpeta, "borradas", *archivo.split("/")))
    assert "Tenía 1 foto." in cambios(app)[-1][2]


# ------------------------------------------------------------------- trasvasos


def test_un_remito_de_trasvaso_se_corrige_desde_el_trasvaso(app, client):
    from test_trasvasos import TAMBORES, preparar, trasvaso

    preparar(client)
    r = trasvaso(client, app, [(TAMBORES, "8", "LT-1", "")])
    assert r.status_code == 302, texto(r)[:2000]
    trasvaso_id = sql(app, "SELECT id FROM trasvasos")[0][0]
    ingreso = sql(app, "SELECT id FROM movimientos WHERE trasvaso_id = ?", trasvaso_id)[0][0]
    # El remito del trasvaso manda a corregir el trasvaso completo.
    r = client.get(f"/movimientos/{ingreso}/editar")
    assert r.status_code == 302 and f"/trasvasos/{trasvaso_id}" in r.headers["Location"]
    assert "Editar el trasvaso" in texto(client.get(f"/movimientos/{ingreso}"))
    pagina = client.get(f"/trasvasos/{trasvaso_id}/editar").get_data(as_text=True)
    assert f"&excluir={ingreso}'" in pagina and f"&excluir_trasvaso={trasvaso_id}'" in pagina
    assert '"cantidad": "8", "lote": "LT-1"' in unescape(pagina)
    r = trasvaso(client, app, [(TAMBORES, "6", "LT-1", "")], orden="4522")  # otro, que no se toca
    assert r.status_code == 302
    data = {"empresa_id": 1, "fecha": "2026-11-21", "orden": "4521", "origen": "AFUERA",
            "producto_id": [str(TAMBORES)], "cantidad": ["10"], "lote": ["LT-1"], "vencimiento": [""]}
    r = client.post(f"/trasvasos/{trasvaso_id}/editar", data=data, content_type="multipart/form-data")
    assert "Trasvaso corregido" in seguir(client, r)
    assert stock(app, TAMBORES) == 16
    accion, _, detalle = cambios(app)[-1]
    assert accion == "EDITADO" and "Fecha: 20/11/2026 → 21/11/2026" in detalle
    assert sql(app, "SELECT tipo, fecha FROM cambios")[-1] == ("TRASVASO", "2026-11-21")
    # Y se borra entero.
    assert client.get(f"/trasvasos/{trasvaso_id}/borrar").status_code == 200
    seguir(client, client.post(f"/trasvasos/{trasvaso_id}/borrar"))
    assert stock(app, TAMBORES) == 6
    assert sql(app, "SELECT COUNT(*) FROM movimientos WHERE trasvaso_id = ?", trasvaso_id) == [(0,)]
    assert cambios(app)[-1][0] == "BORRADO"
