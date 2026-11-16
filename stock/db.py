"""Base de datos (SQLite): un solo archivo, fácil de respaldar."""
import os
import sqlite3

from flask import current_app, g

ESQUEMA = """
CREATE TABLE IF NOT EXISTS empresas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
    cuit TEXT,
    contacto TEXT,
    telefono TEXT,
    activa INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS productos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    codigo TEXT NOT NULL COLLATE NOCASE,
    descripcion TEXT NOT NULL,
    unidad TEXT NOT NULL DEFAULT 'unidades',
    stock_minimo REAL NOT NULL DEFAULT 0,
    activo INTEGER NOT NULL DEFAULT 1,
    UNIQUE (empresa_id, codigo)
);

-- Un movimiento es un remito (de ingreso o de egreso) con uno o más renglones.
CREATE TABLE IF NOT EXISTS movimientos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo TEXT NOT NULL CHECK (tipo IN ('INGRESO', 'EGRESO')),
    fecha TEXT NOT NULL,
    remito TEXT NOT NULL,
    empresa_id INTEGER NOT NULL REFERENCES empresas(id),
    observaciones TEXT,
    creado_en TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
    anulado INTEGER NOT NULL DEFAULT 0,
    motivo_anulacion TEXT
);

-- Cada producto puede tener varios lotes, cada uno con su vencimiento.
-- Si la mercadería no tiene lote, se usa el lote '' (sin lote).
CREATE TABLE IF NOT EXISTS lotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    lote TEXT NOT NULL DEFAULT '' COLLATE NOCASE,
    vencimiento TEXT,
    UNIQUE (producto_id, lote)
);

CREATE TABLE IF NOT EXISTS movimiento_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    movimiento_id INTEGER NOT NULL REFERENCES movimientos(id),
    producto_id INTEGER NOT NULL REFERENCES productos(id),
    lote_id INTEGER NOT NULL REFERENCES lotes(id),
    cantidad REAL NOT NULL CHECK (cantidad > 0)
);

CREATE INDEX IF NOT EXISTS idx_items_producto ON movimiento_items(producto_id);
CREATE INDEX IF NOT EXISTS idx_items_lote ON movimiento_items(lote_id);
CREATE INDEX IF NOT EXISTS idx_mov_empresa_fecha ON movimientos(empresa_id, fecha);
"""


def get_db():
    if "db" not in g:
        ruta = current_app.config["DATABASE"]
        carpeta = os.path.dirname(ruta)
        if carpeta:
            os.makedirs(carpeta, exist_ok=True)
        g.db = sqlite3.connect(ruta)
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
    return g.db


def close_db(_e=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    get_db().executescript(ESQUEMA)


# Stock = ingresos - egresos (sin contar remitos anulados).
SQL_STOCK = """
SELECT p.id, p.codigo, p.descripcion, p.unidad, p.stock_minimo, p.activo,
       e.id AS empresa_id, e.nombre AS empresa,
       COALESCE(SUM(CASE WHEN m.tipo = 'INGRESO' THEN i.cantidad
                         WHEN m.tipo = 'EGRESO' THEN -i.cantidad END), 0) AS stock,
       MAX(m.fecha) AS ultimo_mov
FROM productos p
JOIN empresas e ON e.id = p.empresa_id
LEFT JOIN movimiento_items i ON i.producto_id = p.id
LEFT JOIN movimientos m ON m.id = i.movimiento_id AND m.anulado = 0
"""


def stock_de_productos(empresa_id=None, buscar=None, solo_con_stock=False, producto_ids=None):
    where, params = [], []
    if empresa_id:
        where.append("p.empresa_id = ?")
        params.append(empresa_id)
    if buscar:
        where.append("(p.codigo LIKE ? OR p.descripcion LIKE ?)")
        params += [f"%{buscar}%", f"%{buscar}%"]
    if producto_ids is not None:
        if not producto_ids:
            return []
        where.append(f"p.id IN ({','.join('?' * len(producto_ids))})")
        params += list(producto_ids)
    sql = SQL_STOCK
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY p.id"
    if solo_con_stock:
        sql += " HAVING stock <> 0"
    sql += " ORDER BY e.nombre, p.codigo"
    return get_db().execute(sql, params).fetchall()


def stock_por_id(producto_ids):
    return {r["id"]: r["stock"] for r in stock_de_productos(producto_ids=list(producto_ids))}


SQL_LOTES = """
SELECT l.id, l.lote, l.vencimiento, p.id AS producto_id, p.codigo, p.descripcion, p.unidad,
       e.id AS empresa_id, e.nombre AS empresa,
       COALESCE(SUM(CASE WHEN m.tipo = 'INGRESO' THEN i.cantidad
                         WHEN m.tipo = 'EGRESO' THEN -i.cantidad END), 0) AS stock
FROM lotes l
JOIN productos p ON p.id = l.producto_id
JOIN empresas e ON e.id = p.empresa_id
LEFT JOIN movimiento_items i ON i.lote_id = l.id
LEFT JOIN movimientos m ON m.id = i.movimiento_id AND m.anulado = 0
"""


def stock_de_lotes(empresa_id=None, buscar=None, producto_id=None, lote_ids=None,
                   solo_con_stock=True, vence_hasta=None):
    """Stock por lote. Ordenado por vencimiento (primero lo que vence antes)."""
    where, params = [], []
    if empresa_id:
        where.append("p.empresa_id = ?")
        params.append(empresa_id)
    if producto_id:
        where.append("p.id = ?")
        params.append(producto_id)
    if buscar:
        where.append("(p.codigo LIKE ? OR p.descripcion LIKE ? OR l.lote LIKE ?)")
        params += [f"%{buscar}%"] * 3
    if vence_hasta:
        where.append("l.vencimiento IS NOT NULL AND l.vencimiento <= ?")
        params.append(vence_hasta)
    if lote_ids is not None:
        if not lote_ids:
            return []
        where.append(f"l.id IN ({','.join('?' * len(lote_ids))})")
        params += list(lote_ids)
    sql = SQL_LOTES
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " GROUP BY l.id"
    if solo_con_stock:
        sql += " HAVING stock > 0.0000001"
    sql += " ORDER BY e.nombre, l.vencimiento IS NULL, l.vencimiento, p.codigo, l.lote"
    return get_db().execute(sql, params).fetchall()


def stock_por_lote_id(lote_ids):
    filas = stock_de_lotes(lote_ids=list(lote_ids), solo_con_stock=False)
    return {r["id"]: r["stock"] for r in filas}


def proximo_vencimiento_por_producto(empresa_id=None):
    """{producto_id: fecha de vencimiento más cercana entre los lotes con stock}."""
    res = {}
    for r in stock_de_lotes(empresa_id=empresa_id):
        if r["vencimiento"] and r["producto_id"] not in res:
            res[r["producto_id"]] = r["vencimiento"]
        elif r["vencimiento"] and r["vencimiento"] < res[r["producto_id"]]:
            res[r["producto_id"]] = r["vencimiento"]
    return res
