# Control de stock

Programa para llevar el stock de la mercadería que varias empresas clientes guardan en nuestro depósito.
Se usa desde el navegador (Chrome, Edge, Firefox) y funciona en la red de la oficina, sin internet y sin pagar servidores.

## Qué hace

- **Empresas**: alta y edición de las empresas clientes (nombre, CUIT, contacto, teléfono).
- **Productos**: cada empresa tiene sus productos (código, descripción, unidad y stock mínimo opcional).
  Dos empresas pueden usar el mismo código sin mezclarse.
- **Ingresos y egresos**: se cargan por remito (fecha, número de remito, observaciones) y pueden tener varios productos.
  - No deja sacar más de lo que hay.
  - Avisa si se carga dos veces el mismo remito de ingreso.
  - Los remitos mal cargados no se borran: se **anulan** con un motivo y quedan en el historial.
- **Stock actual** por empresa y producto, con aviso en amarillo cuando está por debajo del mínimo.
- **Ficha de cada producto** con todo su historial y el saldo después de cada movimiento.
- **Historial de movimientos** con filtros (empresa, tipo, fechas, remito, producto).
- **Exportar a Excel** el stock y los movimientos (respeta los filtros), e imprimir.

## Cómo instalarlo (Windows)

1. Instalar Python desde <https://www.python.org/downloads/>.
   **Importante:** en la primera pantalla del instalador marcar la casilla **"Add python.exe to PATH"**.
2. Bajar esta carpeta (en GitHub: botón verde **Code → Download ZIP**) y descomprimirla, por ejemplo en `C:\ControlStock`.
3. Hacer doble clic en **`iniciar.bat`**.
   La primera vez tarda un minuto porque instala lo necesario (requiere internet solo esa vez).
4. Se abre solo el navegador en <http://localhost:5000>. Si no se abre, escribir esa dirección a mano.

Para cerrarlo, cerrar la ventana negra. Para usarlo de nuevo, doble clic otra vez en `iniciar.bat`.

En Linux o Mac: `./iniciar.sh`.

### Usarlo desde otras PCs de la oficina

Al arrancar, la ventana negra muestra una dirección del tipo `http://192.168.0.10:5000`.
Desde cualquier otra PC conectada a la misma red, abrir esa dirección en el navegador.
La PC donde corre el programa tiene que estar prendida. Si Windows pregunta por el firewall, elegir **Permitir acceso** en redes privadas.

## Dónde quedan los datos y cómo hacer respaldo

Todo se guarda en un solo archivo: **`datos/stock.db`** dentro de la carpeta del programa.
Para hacer un respaldo, copiar ese archivo (por ejemplo a un pendrive o a Google Drive) con el programa cerrado.
Para restaurar, volver a ponerlo en su lugar.

## Primeros pasos

1. **Empresas → Nueva empresa**: cargar las empresas clientes.
2. **Productos → Nuevo producto**: cargar los productos de cada empresa.
3. **+ Ingreso**: cargar el stock inicial (por ejemplo, un remito "STOCK INICIAL" con la fecha de hoy).
4. A partir de ahí, cargar cada ingreso y egreso con su remito.

## Para quien programe

- Python 3.10+, Flask, SQLite, openpyxl. Se sirve con waitress.
- Código en `stock/` (`rutas.py` las pantallas, `db.py` la base, `templates/` el HTML).
- Pruebas: `pip install -r requirements-dev.txt` y `pytest`.
- Variables opcionales: `STOCK_DB` (ruta de la base), `STOCK_PUERTO` (por defecto 5000),
  `STOCK_SECRET_KEY`, `STOCK_NO_ABRIR=1` (no abrir el navegador al arrancar).
