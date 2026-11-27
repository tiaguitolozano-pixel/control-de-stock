"""Base de datos (SQLite): un solo archivo, fácil de respaldar."""
import os
import sqlite3
from datetime import datetime

from flask import current_app, g

# Esquema de la primera versión. No se modifica: los cambios posteriores van en MIGRACIONES,
# así las bases que ya tienen datos se actualizan solas al abrir el programa.
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


ENVASES_INICIALES = [
    "Bolsas", "Tambores de chapa", "Tambores de plástico", "Cisternas", "Bidones",
    "Cajas", "Baldes", "Big bags", "Unidades",
]

# Cada migración se aplica una sola vez, en orden. PRAGMA user_version guarda la última aplicada.
MIGRACIONES = [
    # 1: tipos de envase, unidades por pallet y datos del transporte en cada remito.
    """
    CREATE TABLE IF NOT EXISTS envases (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
        activo INTEGER NOT NULL DEFAULT 1
    );
    -- 'bolsas' -> 'Bolsas', para que la lista quede pareja con los tipos nuevos.
    UPDATE productos SET unidad = UPPER(SUBSTR(TRIM(unidad), 1, 1)) || SUBSTR(TRIM(unidad), 2)
        WHERE TRIM(unidad) <> '';
    INSERT OR IGNORE INTO envases (nombre)
        SELECT DISTINCT unidad FROM productos WHERE unidad <> '';
    """
    + "".join(f"INSERT OR IGNORE INTO envases (nombre) VALUES ('{e}');\n" for e in ENVASES_INICIALES)
    + """
    ALTER TABLE productos ADD COLUMN unidades_por_pallet REAL;
    ALTER TABLE movimientos ADD COLUMN chofer TEXT;
    ALTER TABLE movimientos ADD COLUMN transporte TEXT;
    ALTER TABLE movimientos ADD COLUMN patente_camion TEXT;
    ALTER TABLE movimientos ADD COLUMN patente_semi TEXT;
    """,
    # 2: ubicaciones del depósito (galpón, calle, estiba) y pallets con etiqueta.
    """
    CREATE TABLE IF NOT EXISTS ubicaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        galpon TEXT NOT NULL,
        calle INTEGER NOT NULL,
        estiba INTEGER NOT NULL,
        codigo TEXT NOT NULL UNIQUE COLLATE NOCASE,
        activa INTEGER NOT NULL DEFAULT 1,
        UNIQUE (galpon, calle, estiba)
    );
    -- Cada pallet tiene un código propio (P000001) que va en su etiqueta con QR.
    CREATE TABLE IF NOT EXISTS pallets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        codigo TEXT UNIQUE COLLATE NOCASE,
        empresa_id INTEGER NOT NULL REFERENCES empresas(id),
        ubicacion_id INTEGER REFERENCES ubicaciones(id),
        movimiento_id INTEGER REFERENCES movimientos(id),
        creado_en TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        observaciones TEXT
    );
    -- Lo que entra y sale de cada pallet. El contenido es la suma, sin contar los remitos anulados.
    -- item_id es el renglón del remito; queda vacío al armar, juntar o desarmar pallets.
    CREATE TABLE IF NOT EXISTS pallet_movs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pallet_id INTEGER NOT NULL REFERENCES pallets(id),
        item_id INTEGER REFERENCES movimiento_items(id),
        producto_id INTEGER NOT NULL REFERENCES productos(id),
        lote_id INTEGER NOT NULL REFERENCES lotes(id),
        cantidad REAL NOT NULL CHECK (cantidad <> 0),
        motivo TEXT NOT NULL,
        creado_en TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );
    -- Historial de dónde estuvo cada pallet.
    CREATE TABLE IF NOT EXISTS pallet_ubicaciones (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        pallet_id INTEGER NOT NULL REFERENCES pallets(id),
        ubicacion_id INTEGER REFERENCES ubicaciones(id),
        creado_en TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_pallets_empresa ON pallets(empresa_id);
    CREATE INDEX IF NOT EXISTS idx_pallets_ubicacion ON pallets(ubicacion_id);
    CREATE INDEX IF NOT EXISTS idx_pallets_movimiento ON pallets(movimiento_id);
    CREATE INDEX IF NOT EXISTS idx_pallet_movs_pallet ON pallet_movs(pallet_id);
    CREATE INDEX IF NOT EXISTS idx_pallet_movs_lote ON pallet_movs(lote_id);
    CREATE INDEX IF NOT EXISTS idx_pallet_movs_item ON pallet_movs(item_id);
    CREATE INDEX IF NOT EXISTS idx_pallet_ubic_pallet ON pallet_ubicaciones(pallet_id);
    """,
    # 3: kilos por unidad, contenedor, insumos adicionales (pallets, film stretch) y fotos de los remitos.
    """
    ALTER TABLE productos ADD COLUMN kilos_por_unidad REAL;
    ALTER TABLE movimientos ADD COLUMN contenedor TEXT;
    ALTER TABLE movimientos ADD COLUMN tipo_contenedor TEXT;
    CREATE TABLE IF NOT EXISTS insumos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        nombre TEXT NOT NULL UNIQUE COLLATE NOCASE,
        unidad TEXT NOT NULL DEFAULT 'unidades',
        -- 1: en cada remito se indica si son propios o del cliente; los del cliente llevan saldo.
        con_propietario INTEGER NOT NULL DEFAULT 0,
        activo INTEGER NOT NULL DEFAULT 1
    );
    INSERT OR IGNORE INTO insumos (nombre, unidad, con_propietario) VALUES ('Pallets', 'pallets', 1);
    INSERT OR IGNORE INTO insumos (nombre, unidad, con_propietario) VALUES ('Film stretch', 'rollos', 0);
    CREATE TABLE IF NOT EXISTS movimiento_insumos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        movimiento_id INTEGER NOT NULL REFERENCES movimientos(id),
        insumo_id INTEGER NOT NULL REFERENCES insumos(id),
        cantidad REAL NOT NULL CHECK (cantidad > 0),
        propietario TEXT CHECK (propietario IN ('PROPIO', 'CLIENTE'))
    );
    -- archivo: nombre dentro de datos/fotos (la foto no se guarda en la base).
    CREATE TABLE IF NOT EXISTS movimiento_fotos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        movimiento_id INTEGER NOT NULL REFERENCES movimientos(id),
        archivo TEXT NOT NULL,
        nombre_original TEXT,
        creado_en TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
    );
    CREATE INDEX IF NOT EXISTS idx_mov_insumos_mov ON movimiento_insumos(movimiento_id);
    CREATE INDEX IF NOT EXISTS idx_mov_fotos_mov ON movimiento_fotos(movimiento_id);
    """,
    # 4: trasvasos (de un isotanque, cisternas o tambores a otros envases) y el stock de envases
    #    vacíos y sucios del depósito.
    """
    -- 1: se lleva la cuenta de cuántos vacíos y sucios de este tipo hay en el depósito.
    ALTER TABLE envases ADD COLUMN lleva_vacios INTEGER NOT NULL DEFAULT 0;
    UPDATE envases SET lleva_vacios = 1
        WHERE LOWER(nombre) LIKE '%tambor%' OR LOWER(nombre) LIKE '%cisterna%'
           OR LOWER(nombre) LIKE '%bidon%' OR LOWER(nombre) LIKE 'bid_n%' OR LOWER(nombre) LIKE '%ibc%';
    -- Un trasvaso es una orden del cliente. Lo que sale del stock (si el producto ya estaba en el
    -- depósito) es un egreso y lo que queda envasado es un ingreso: los dos llevan trasvaso_id.
    CREATE TABLE IF NOT EXISTS trasvasos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        empresa_id INTEGER NOT NULL REFERENCES empresas(id),
        fecha TEXT NOT NULL,
        orden TEXT NOT NULL,
        remito TEXT,
        -- AFUERA: llega de afuera (isotanque del puerto, camión). DEPOSITO: sale del stock del depósito.
        origen TEXT NOT NULL CHECK (origen IN ('AFUERA', 'DEPOSITO')),
        kilos_origen REAL,
        observaciones TEXT,
        creado_en TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        anulado INTEGER NOT NULL DEFAULT 0,
        motivo_anulacion TEXT
    );
    ALTER TABLE movimientos ADD COLUMN trasvaso_id INTEGER REFERENCES trasvasos(id);
    -- Envases vacíos y sucios del depósito (no son mercadería de los clientes). El saldo es la suma.
    CREATE TABLE IF NOT EXISTS envase_movs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        envase_id INTEGER NOT NULL REFERENCES envases(id),
        estado TEXT NOT NULL CHECK (estado IN ('VACIO', 'SUCIO')),
        cantidad REAL NOT NULL CHECK (cantidad <> 0),
        motivo TEXT NOT NULL,
        fecha TEXT NOT NULL,
        trasvaso_id INTEGER REFERENCES trasvasos(id),
        -- Los renglones que se anotaron juntos (por ejemplo, un lavado: salen sucios y entran vacíos).
        grupo INTEGER,
        observaciones TEXT,
        creado_en TEXT NOT NULL DEFAULT (datetime('now', 'localtime')),
        anulado INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX IF NOT EXISTS idx_trasvasos_empresa_fecha ON trasvasos(empresa_id, fecha);
    CREATE INDEX IF NOT EXISTS idx_mov_trasvaso ON movimientos(trasvaso_id);
    CREATE INDEX IF NOT EXISTS idx_envase_movs_envase ON envase_movs(envase_id, estado);
    CREATE INDEX IF NOT EXISTS idx_envase_movs_trasvaso ON envase_movs(trasvaso_id);
    """,
    # 5: los envases vacíos y sucios son de cada cliente (no del depósito): entran con los ingresos,
    #    los usan los trasvasos y salen con los egresos.
    """
    ALTER TABLE envase_movs ADD COLUMN empresa_id INTEGER REFERENCES empresas(id);
    ALTER TABLE envase_movs ADD COLUMN movimiento_id INTEGER REFERENCES movimientos(id);
    UPDATE envase_movs SET empresa_id = (SELECT t.empresa_id FROM trasvasos t WHERE t.id = envase_movs.trasvaso_id)
        WHERE trasvaso_id IS NOT NULL;
    -- Lo anotado a mano en la versión 4 era del depósito, sin cliente: queda anulado (se ve tachado en el historial).
    UPDATE envase_movs SET anulado = 1,
        observaciones = TRIM(COALESCE(observaciones, '')
                             || ' (sin cliente: se anuló al pasar los envases a cada cliente)')
        WHERE empresa_id IS NULL;
    CREATE INDEX IF NOT EXISTS idx_envase_movs_empresa ON envase_movs(empresa_id, envase_id, estado);
    CREATE INDEX IF NOT EXISTS idx_envase_movs_movimiento ON envase_movs(movimiento_id);
    """,
]


def version_esquema(db):
    return db.execute("PRAGMA user_version").fetchone()[0]


def respaldar(ruta_db, carpeta, nombre):
    """Copia segura de la base (aunque esté en uso) a carpeta/nombre."""
    os.makedirs(carpeta, exist_ok=True)
    destino = os.path.join(carpeta, nombre)
    origen = sqlite3.connect(ruta_db)
    copia = sqlite3.connect(destino)
    try:
        origen.backup(copia)
    finally:
        copia.close()
        origen.close()
    return destino


def respaldo_diario(ruta_db, cantidad=30):
    """Guarda una copia por día en datos/respaldos y deja solo las últimas `cantidad`."""
    if not os.path.exists(ruta_db):
        return None
    carpeta = os.path.join(os.path.dirname(ruta_db), "respaldos")
    nombre = f"stock-{datetime.now():%Y-%m-%d}.db"
    destino = os.path.join(carpeta, nombre)
    if not os.path.exists(destino):
        respaldar(ruta_db, carpeta, nombre)
    diarios = sorted(f for f in os.listdir(carpeta) if f.startswith("stock-") and f.endswith(".db"))
    for viejo in diarios[:-cantidad]:
        os.remove(os.path.join(carpeta, viejo))
    return destino


def init_db():
    db = get_db()
    tenia_datos = db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'table' AND name = 'empresas'"
    ).fetchone()[0]
    db.executescript(ESQUEMA)
    version = version_esquema(db)
    if version >= len(MIGRACIONES):
        return
    ruta = current_app.config["DATABASE"]
    if tenia_datos and os.path.exists(ruta):
        # Antes de cambiar la estructura de una base con datos, guardar una copia.
        sello = datetime.now().strftime("%Y-%m-%d_%H%M%S")
        respaldar(ruta, os.path.join(os.path.dirname(ruta), "respaldos"),
                  f"antes-de-actualizar-v{version}_{sello}.db")
    for numero, sql in enumerate(MIGRACIONES[version:], start=version + 1):
        try:
            db.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {numero};\nCOMMIT;")
        except Exception:
            if db.in_transaction:
                db.rollback()
            raise


# Stock = ingresos - egresos (sin contar remitos anulados).
SQL_STOCK = """
SELECT p.id, p.codigo, p.descripcion, p.unidad, p.stock_minimo, p.activo, p.unidades_por_pallet,
       p.kilos_por_unidad, e.id AS empresa_id, e.nombre AS empresa,
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
       p.unidades_por_pallet, p.kilos_por_unidad, e.id AS empresa_id, e.nombre AS empresa,
       COALESCE(SUM(CASE WHEN m.tipo = 'INGRESO' THEN i.cantidad
                         WHEN m.tipo = 'EGRESO' THEN -i.cantidad END), 0) AS stock
FROM lotes l
JOIN productos p ON p.id = l.producto_id
JOIN empresas e ON e.id = p.empresa_id
LEFT JOIN movimiento_items i ON i.lote_id = l.id
LEFT JOIN movimientos m ON m.id = i.movimiento_id AND m.anulado = 0
"""


def stock_de_lotes(empresa_id=None, buscar=None, producto_id=None, lote_ids=None,
                   solo_con_stock=True, vence_hasta=None, lote=None):
    """Stock por lote. Ordenado por vencimiento (primero lo que vence antes)."""
    where, params = [], []
    if lote:
        where.append("l.lote LIKE ?")
        params.append(f"%{lote}%")
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
        v = r["vencimiento"]
        if v and (r["producto_id"] not in res or v < res[r["producto_id"]]):
            res[r["producto_id"]] = v
    return res


def envases(solo_activos=False):
    sql = "SELECT * FROM envases"
    if solo_activos:
        sql += " WHERE activo = 1"
    return get_db().execute(sql + " ORDER BY nombre").fetchall()


def asegurar_envase(nombre):
    """Agrega el tipo de envase a la lista si todavía no está. No hace commit."""
    nombre = (nombre or "").strip()
    if nombre:
        get_db().execute("INSERT OR IGNORE INTO envases (nombre) VALUES (?)", (nombre,))
