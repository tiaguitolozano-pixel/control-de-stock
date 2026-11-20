"""Códigos QR dibujados en SVG: no necesitan internet ni imágenes y se imprimen nítidos."""
import qrcode
from markupsafe import Markup
from qrcode.constants import ERROR_CORRECT_Q


def qr_svg(texto):
    """Devuelve el QR de `texto` como un <svg> listo para poner en la página."""
    # Nivel Q: se sigue leyendo aunque la etiqueta esté un poco gastada o sucia.
    qr = qrcode.QRCode(error_correction=ERROR_CORRECT_Q, border=4, box_size=1)
    qr.add_data(str(texto).upper())
    qr.make(fit=True)
    matriz = qr.get_matrix()  # ya incluye el margen blanco
    n = len(matriz)
    trazos = []
    for y, fila in enumerate(matriz):
        x = 0
        while x < n:
            if not fila[x]:
                x += 1
                continue
            inicio = x
            while x < n and fila[x]:
                x += 1
            trazos.append(f"M{inicio} {y}h{x - inicio}v1h-{x - inicio}z")
    return Markup(
        f'<svg class="qr" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {n} {n}" '
        f'shape-rendering="crispEdges" role="img" aria-label="QR {Markup.escape(texto)}">'
        f'<rect width="{n}" height="{n}" fill="#fff"/><path d="{"".join(trazos)}" fill="#000"/></svg>'
    )
