"""Arranca el programa de control de stock.

Uso: python iniciar.py   (o doble clic en iniciar.bat en Windows)
Después se abre en el navegador: http://localhost:5000
Desde otras PCs de la misma red: http://IP-DE-ESTA-PC:5000
"""
import os
import socket
import sys
import threading
import webbrowser

# Los componentes necesarios (Flask, openpyxl, waitress) vienen incluidos en la carpeta
# "vendor", así no hace falta descargar nada de internet para usar el programa.
CARPETA = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(CARPETA, "vendor"))
sys.path.insert(0, CARPETA)

from waitress import serve  # noqa: E402

from stock import create_app  # noqa: E402
from stock.db import respaldo_diario  # noqa: E402

PUERTO = int(os.environ.get("STOCK_PUERTO", "5000"))


def ip_local():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "IP-DE-ESTA-PC"


if __name__ == "__main__":
    app = create_app()
    try:
        respaldo = respaldo_diario(app.config["DATABASE"])
    except OSError as error:  # que un problema con la copia no impida usar el programa
        respaldo = None
        print(f" ATENCIÓN: no se pudo guardar la copia de respaldo del día ({error}).")
    print("=" * 60)
    print(" Control de stock funcionando.")
    print(f" En esta PC:          http://localhost:{PUERTO}")
    print(f" Desde otras PCs:     http://{ip_local()}:{PUERTO}")
    print(f" Datos guardados en:  {app.config['DATABASE']}")
    if respaldo:
        print(f" Copia del día en:    {respaldo}")
    print(" Para cerrar el programa, cerrá esta ventana.")
    print("=" * 60)
    if os.environ.get("STOCK_NO_ABRIR") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PUERTO}")).start()
    serve(app, host="0.0.0.0", port=PUERTO)
