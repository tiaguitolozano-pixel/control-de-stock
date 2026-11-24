"""Fotos de los remitos (la mercadería al llegar, el camión cargado, el precinto del contenedor...).

Las fotos se guardan como archivos en la carpeta datos/fotos, al lado de la base de datos, y en la base
queda solo el nombre del archivo. Para respaldar todo hay que copiar la carpeta datos completa.
"""
import os
import secrets
from datetime import date

from flask import current_app

from .db import get_db

TIPOS = {".jpg": "image/jpeg", ".png": "image/png", ".gif": "image/gif", ".webp": "image/webp",
         ".heic": "image/heic"}
_MARCAS_HEIC = (b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1")


def extension_de(cabecera):
    """Reconoce el tipo de imagen por sus primeros bytes (no por el nombre del archivo)."""
    if cabecera.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if cabecera.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if cabecera[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if cabecera[:4] == b"RIFF" and cabecera[8:12] == b"WEBP":
        return ".webp"
    if cabecera[4:8] == b"ftyp" and cabecera[8:12] in _MARCAS_HEIC:
        return ".heic"
    return None


def leer_subidas(archivos):
    """archivos: los FileStorage del formulario. Devuelve (subidas, errores); subidas = [(archivo, extensión)]."""
    subidas, errores = [], []
    for archivo in archivos:
        if not archivo or not archivo.filename:
            continue
        cabecera = archivo.stream.read(16)
        archivo.stream.seek(0)
        extension = extension_de(cabecera)
        if extension is None:
            errores.append(f"'{archivo.filename}' no es una foto (tiene que ser JPG, PNG, WEBP o HEIC).")
        else:
            subidas.append((archivo, extension))
    return subidas, errores


def guardar(movimiento_id, subidas):
    """Guarda las fotos en la carpeta y las anota en la base. No hace commit. Devuelve cuántas guardó."""
    if not subidas:
        return 0
    mes = date.today().strftime("%Y-%m")
    carpeta = os.path.join(current_app.config["FOTOS"], mes)
    os.makedirs(carpeta, exist_ok=True)
    db = get_db()
    for archivo, extension in subidas:
        nombre = f"remito-{movimiento_id}-{secrets.token_hex(4)}{extension}"
        archivo.save(os.path.join(carpeta, nombre))
        db.execute(
            "INSERT INTO movimiento_fotos (movimiento_id, archivo, nombre_original) VALUES (?,?,?)",
            (movimiento_id, f"{mes}/{nombre}", (archivo.filename or "")[:200]),
        )
    return len(subidas)


def de_movimiento(movimiento_id):
    return get_db().execute(
        "SELECT * FROM movimiento_fotos WHERE movimiento_id = ? ORDER BY id", (movimiento_id,)
    ).fetchall()


def buscar(foto_id):
    return get_db().execute("SELECT * FROM movimiento_fotos WHERE id = ?", (foto_id,)).fetchone()


def tipo_mime(fila):
    return TIPOS.get(os.path.splitext(fila["archivo"])[1].lower(), "application/octet-stream")


def borrar(fila):
    """Borra la foto de la base (con commit) y después su archivo."""
    db = get_db()
    db.execute("DELETE FROM movimiento_fotos WHERE id = ?", (fila["id"],))
    db.commit()
    ruta = os.path.join(current_app.config["FOTOS"], *fila["archivo"].split("/"))
    try:
        os.remove(ruta)
    except FileNotFoundError:
        pass
