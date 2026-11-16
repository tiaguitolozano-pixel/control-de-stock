"""Arranca el programa de control de stock.

Uso: python iniciar.py   (o doble clic en iniciar.bat en Windows)
Después se abre en el navegador: http://localhost:5000
Desde otras PCs de la misma red: http://IP-DE-ESTA-PC:5000
"""
import os
import socket
import threading
import webbrowser

from waitress import serve

from stock import create_app

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
    print("=" * 60)
    print(" Control de stock funcionando.")
    print(f" En esta PC:          http://localhost:{PUERTO}")
    print(f" Desde otras PCs:     http://{ip_local()}:{PUERTO}")
    print(f" Datos guardados en:  {app.config['DATABASE']}")
    print(" Para cerrar el programa, cerrá esta ventana.")
    print("=" * 60)
    if os.environ.get("STOCK_NO_ABRIR") != "1":
        threading.Timer(1.5, lambda: webbrowser.open(f"http://localhost:{PUERTO}")).start()
    serve(app, host="0.0.0.0", port=PUERTO)
