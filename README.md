# Control de stock

Programa para llevar el stock de la mercadería que varias empresas clientes guardan en nuestro depósito.
Se usa desde el navegador (Chrome, Edge, Firefox) y funciona en la red de la oficina, sin internet y sin pagar servidores.

## Qué hace

- **Empresas**: alta y edición de las empresas clientes (nombre, CUIT, contacto, teléfono).
- **Productos**: cada empresa tiene sus productos (código, descripción, tipo de envase, unidades por pallet,
  kilos por unidad y stock mínimo opcional). Dos empresas pueden usar el mismo código sin mezclarse.
- **Kilos**: con los kilos por unidad de cada producto, el programa calcula los kilos totales (kilos por unidad ×
  cantidad) en el stock, en cada lote, en cada renglón de los remitos y en los totales.
- **Tipos de envase**: bolsas, tambores de chapa, tambores de plástico, cisternas, bidones, cajas… La lista se
  puede ampliar y corregir desde Productos → Tipos de envase.
- **Equivalencia en pallets**: con las unidades por pallet de cada producto, las cantidades se ven también en
  pallets completos (columna "Equivale a": por ejemplo, 100 bolsas = 2 pallets + 20 bolsas). Al cargar un remito
  se pueden escribir los pallets y las unidades sueltas, y la cantidad se calcula sola; si las sueltas completan
  pallets, se pasan solas a pallets.
- **Lotes y vencimientos**: en cada ingreso se carga el lote y la fecha de vencimiento (opcionales).
  En los egresos se elige de qué lote sale, y se ofrece primero el que vence antes.
- **Ingresos y egresos**: se cargan por remito (fecha, número de remito, datos del transporte: chofer, empresa de
  transporte, patente del camión y del semi, número y tipo de contenedor, y una observación general) y pueden tener
  varios productos. Los choferes ya usados se autocompletan con el transporte y las patentes de su último viaje.
  Si el contenedor tiene el formato de siempre (4 letras y 7 números), se revisa el dígito de control.
  - **Insumos adicionales** de cada remito: pallets (propios o del cliente), film stretch y los que se agreguen.
    Los pallets del cliente llevan saldo por empresa (no pueden salir más de los que hay); los propios y el film
    solo quedan anotados. Puede haber remitos solo de insumos, por ejemplo una devolución de pallets.
  - **Envases vacíos del cliente**: en un ingreso, los tambores, cisternas, etc. vacíos que manda el cliente para
    los trasvasos; en un egreso, los vacíos o sucios que se le devuelven. Llevan saldo por empresa (no pueden salir
    más de los que tiene en el depósito) y puede haber remitos solo de envases.
  - **Fotos** en las observaciones del remito (la mercadería al llegar, un daño, el camión cargado, el precinto…).
    Se eligen debajo de la observación general, se achican solas antes de guardarse y se pueden agregar más
    después, desde el remito. En el historial, al lado de la observación, se ve cuántas fotos tiene cada remito.
  - No deja sacar más de lo que hay (ni del producto ni del lote).
  - Avisa si se carga dos veces el mismo remito de ingreso.
  - Los remitos mal cargados no se borran: se **anulan** con un motivo y quedan en el historial.
- **Trasvasos**: pasar un producto de un envase a otro por orden del cliente (descargar un isotanque del puerto en
  tambores o cisternas, pasar cisternas a tambores o al revés). Se cargan como un ingreso, con el número de orden, de
  dónde sale el producto (de afuera o del stock del depósito), los envases vacíos que se usan y los sucios que quedan.
  Lleva la cuenta de los **envases vacíos y sucios de cada cliente**: cada trasvaso usa sus vacíos y le deja los
  sucios. Ver [Trasvasos](#trasvasos).
- **Stock actual** por empresa y producto, con aviso en amarillo cuando está por debajo del mínimo
  y la fecha de vencimiento más próxima. Se puede abrir el detalle por lote debajo de cada código y buscar por lote.
- **Stock por lote**, con los vencidos en rojo y los que vencen en los próximos 60 días en amarillo.
- **Importar desde Excel** el conteo inicial (por ejemplo, lo que sale del programa Cristal):
  crea solas las empresas y productos que falten. Hay una planilla de ejemplo para bajar.
- **Ficha de cada producto** con su stock en cantidad y kilos, y todo su historial separado por lote.
- **Historial de movimientos** tipo debe y haber (ingresos y egresos en columnas separadas), que se lee de arriba
  hacia abajo (primero lo más viejo) y separado por producto y lote: cada producto muestra su cantidad general y,
  debajo, los movimientos de cada lote con el saldo del lote después de cada remito (en cantidad y en kilos).
  Arriba, los totales por tipo de envase y en kilos. Filtros por empresa, tipo, fechas, remito, producto/lote y
  transporte (chofer, patente o contenedor). Abajo, los insumos adicionales y los envases del cliente de esos remitos.
- **Exportar a Excel** el stock y los movimientos (respeta los filtros), e imprimir.
- **Pallets con etiqueta QR**: al cargar un ingreso se arman solos los pallets (completos y uno con lo que sobra)
  y se imprimen sus etiquetas en hojas A4. Cada pallet sabe qué tiene (producto, lote, vencimiento y cantidad),
  puede tener dos lotes, y se puede sacar una parte.
- **Depósito y ubicaciones**: el mapa del galpón con sus calles y estibas numeradas, qué pallets hay en cada una
  y cuáles faltan ubicar. Cada estiba tiene su etiqueta con QR.
- **Terminal**: una pantalla para el lector de códigos o el celular, con tres tareas: consultar un pallet o una
  estiba, ubicar pallets y despachar (arma el egreso con los pallets que suben al camión).

## Cómo instalarlo (Windows)

1. Instalar Python desde <https://www.python.org/downloads/>.
   **Importante:** en la primera pantalla del instalador marcar la casilla **"Add python.exe to PATH"**.
2. Bajar esta carpeta (en GitHub: botón verde **Code → Download ZIP**) y descomprimirla, por ejemplo en `C:\ControlStock`.
3. Hacer doble clic en **`iniciar.bat`**. No necesita internet: todo lo que usa viene incluido en la carpeta `vendor`.
4. Se abre solo el navegador en <http://localhost:5000>. Si no se abre, escribir esa dirección a mano.

Para cerrarlo, cerrar la ventana negra. Para usarlo de nuevo, doble clic otra vez en `iniciar.bat`.

En Linux o Mac: `./iniciar.sh`.

### Usarlo desde otras PCs de la oficina

Al arrancar, la ventana negra muestra una dirección del tipo `http://192.168.0.10:5000`.
Desde cualquier otra PC conectada a la misma red, abrir esa dirección en el navegador.
La PC donde corre el programa tiene que estar prendida. Si Windows pregunta por el firewall, elegir **Permitir acceso** en redes privadas.

## Cómo actualizar a una versión nueva sin perder datos

Los datos están en la carpeta **`datos`** dentro de la carpeta del programa. Para actualizar:

1. Cerrar el programa (cerrar la ventana negra).
2. Descomprimir el zip nuevo en una carpeta nueva.
3. Copiar la carpeta `datos` de la versión vieja a la carpeta nueva.
4. Abrir el programa nuevo con `iniciar.bat`.

Al abrir la versión nueva, el programa actualiza solo la base de datos. Antes de hacerlo guarda una copia en
`datos/respaldos/antes-de-actualizar-….db`, por si algo sale mal.

## Dónde quedan los datos y cómo hacer respaldo

Todo se guarda en la carpeta **`datos`** dentro de la carpeta del programa: la base de datos es el archivo
`datos/stock.db` y las fotos de los remitos están en `datos/fotos`.
Conviene que la carpeta del programa **no** esté dentro de OneDrive o Google Drive, porque la sincronización
mientras el programa está abierto puede dañar la base. Para hacer un respaldo, copiar **la carpeta `datos` completa**
(por ejemplo a un pendrive o a Google Drive) con el programa cerrado. Para restaurar, volver a ponerla en su lugar.

Además, cada vez que se abre el programa se guarda una copia del día en `datos/respaldos/stock-AAAA-MM-DD.db`
(se guardan las últimas 30).

## Primeros pasos

**Opción rápida:** en **Importar Excel**, bajar la planilla de ejemplo, completarla con el conteo
(una fila por producto y lote: Empresa, Código, Descripción, Envase, Por pallet, Kilos por unidad, Lote, Vencimiento,
Cantidad) y subirla. Las columnas Envase, Por pallet, Kilos por unidad, Lote y Vencimiento son opcionales.

**Pallets de los clientes:** si una empresa ya tiene pallets suyos en el depósito, cargar un ingreso de esa empresa
solo con esos pallets en **Insumos adicionales → Pallets del cliente** (remito "Saldo inicial"). Los saldos de cada
empresa se ven en **Stock → Pallets de clientes en el depósito**.

**Envases vacíos y sucios de los clientes:** si una empresa ya tiene tambores, cisternas, etc. en el depósito,
cargarlos antes de su primer trasvaso: en **Trasvasos → Envases vacíos y sucios**, elegir la empresa, **Conteo** y
escribir cuántos vacíos y cuántos sucios tiene de cada tipo. Los vacíos también se pueden cargar con un ingreso de
esa empresa solo con esos envases (apartado **Envases vacíos del cliente**, remito "Saldo inicial").

**Opción a mano:**
1. **Empresas → Nueva empresa**: cargar las empresas clientes.
2. **Productos → Nuevo producto**: cargar los productos de cada empresa.
3. **+ Ingreso**: cargar el stock inicial (por ejemplo, un remito "STOCK INICIAL" con la fecha de hoy).

A partir de ahí, cargar cada ingreso y egreso con su remito.

## Trasvasos

En **⇄ Trasvasos → + Cargar trasvaso** se carga igual que un ingreso (empresa, fecha, transporte, observación y
fotos, insumos adicionales), con estas diferencias:

- **N° de orden** (obligatorio): la orden del cliente. El número de remito es opcional. Avisa si la orden ya se usó.
- **Origen**: *Llega de afuera* (un isotanque del puerto, un camión) o *Ya está en el depósito* (cisternas o
  tambores del stock de la empresa). Si llega de afuera se pueden anotar los kilos que llegan, solo para compararlos
  con lo envasado (no entran al stock). Si ya está en el depósito, se elige producto, lote y cantidad como en un
  egreso, y eso sale del stock.
- **Destino**: cómo queda envasado (producto, lote, vencimiento, cantidad). Entra al stock como un ingreso y se
  arman sus pallets con etiqueta. Si sale de un solo lote del depósito, el lote y el vencimiento se completan solos.
  Debajo se ve la comparación de kilos: origen, envasado y diferencia.
- **Envases vacíos y sucios del cliente**: los vacíos que se usan (se completan solos con lo envasado) y los sucios
  que quedan (con lo que se vació del depósito). Se pueden cambiar a mano. Al lado de cada tipo se ve cuántos tiene
  la empresa en el depósito, y no deja usar más vacíos de los que tiene.

Al guardar quedan dos remitos con el número "Orden …": el ingreso de lo envasado y, si salió del depósito, el
egreso de lo que se vació. Aparecen en el historial de movimientos con la marca *trasvaso*. La lista de trasvasos
se filtra por empresa, fechas y orden, y se exporta a Excel. Un trasvaso mal cargado se anula completo (no sus
remitos sueltos): vuelve todo a como estaba, incluidos los envases.

### Envases vacíos y sucios

Son de cada cliente: el depósito se los guarda. Los vacíos que manda el cliente entran con un **+ Ingreso** (apartado
**Envases vacíos del cliente**; el remito puede tener solo envases). Cada trasvaso usa los vacíos de esa empresa y le
deja los sucios de lo que se vació. Los que vuelven al cliente, vacíos o sucios, salen con un **− Egreso** (apartado
**Envases que se devuelven al cliente**). No pueden usarse ni salir más de los que la empresa tiene en el depósito.

En **Trasvasos → Envases vacíos y sucios** se ve cuántos tiene cada empresa (se puede ver una sola) y se anota lo que
no lleva remito: lavados (los sucios pasan a vacíos), descartes o devoluciones sin remito. Para cargar lo que una
empresa ya tiene, o corregir después de contar, se usa **Conteo**: se escribe cuántos hay de verdad y el programa
anota la diferencia. En **Stock** se ven los totales (de todos los clientes, o de la empresa elegida), y en la
lista de trasvasos, al elegir una empresa, sus vacíos y sucios.

Qué tipos de envase llevan esta cuenta se elige en **Productos → Tipos de envase** (columna "Vacíos y sucios").
Al actualizar a esta versión quedan marcados los tambores, cisternas, bidones y contenedores IBC.

En la primera versión de trasvasos los envases eran del depósito. Al actualizar, los de cada trasvaso pasan a su
empresa, y los conteos y movimientos anotados a mano quedan anulados (no se sabe de qué cliente eran): hay que
cargarlos de nuevo para cada empresa.

## Pallets, etiquetas y ubicaciones

### Etiquetar el stock que ya está en el depósito

Lo que se cargó antes (por ejemplo, el conteo inicial desde Excel) no tiene pallets con etiqueta.
En **Pallets → Armar pallets para el stock sin etiqueta** aparece cada lote con lo que falta etiquetar:
se revisan las unidades por pallet, se aprieta **Armar** y se imprimen las etiquetas.
Si un pallet tiene dos lotes, se arman por separado y después se juntan desde la ficha del pallet (**Juntar con otro pallet**).

De ahí en adelante, cada ingreso nuevo arma sus pallets solo (casilla **Armar los pallets con su etiqueta QR**).

### Imprimir etiquetas

- Pallets: desde el remito (**Imprimir etiquetas de los pallets**), desde la ficha de un pallet, o marcando varios en **Pallets**.
  Se eligen 4, 2 o 1 etiqueta por hoja A4.
- Estibas: en **Depósito**, botón de imprimir de cada calle (2 o 4 por hoja).
- En la ventana de impresión elegir papel **A4**, márgenes **"Ninguno"** y escala **100%** (sin "ajustar a la página").

### Cargar las estibas del galpón

En **Depósito → Agregar estibas** se elige galpón, calle y desde qué número hasta cuál (por ejemplo, calle 2, de la 1 a la 24).
Cada estiba queda con un código como `G16-C2-E07` (galpón 16, calle 2, estiba 7). Si una estiba deja de existir,
se desactiva desde su ficha (primero hay que mover sus pallets).

### La terminal (lector de códigos o celular)

1. Conectar el lector o el celular al WiFi del galpón.
2. Abrir en su navegador la dirección que muestra la ventana negra, agregando `/terminal`.
   Por ejemplo: `http://192.168.0.10:5000/terminal`. Conviene guardarla como acceso directo en la pantalla.
3. El lector tiene que estar configurado como **teclado** (modo "keyboard wedge" o "emulación de teclado")
   y mandar **Enter** después de cada código. Así funciona en la mayoría de los lectores tipo celular.

Tareas:
- **Consultar**: escanear un pallet (qué tiene y dónde está) o una estiba (qué pallets hay).
- **Ubicar pallets**: escanear el pallet y después la estiba donde se deja. Para dejar varios en la misma estiba,
  escanear primero la estiba y después los pallets, uno atrás del otro.
- **Despachar**: escanear los pallets que suben al camión. Si de un pallet sale solo una parte, se cambia la cantidad.
  Al final se escribe el número de remito y se confirma: queda cargado el egreso, igual que desde la oficina.

El teclado de la pantalla queda escondido para que no tape; con el botón ⌨ se muestra para escribir un código a mano.

## Para quien programe

- Python 3.10+, Flask, SQLite, openpyxl. Se sirve con waitress.
- Las dependencias (puro Python) están copiadas en `vendor/` para que funcione sin internet;
  `iniciar.py` las agrega al `sys.path`. Para desarrollo se puede usar un venv con `requirements-dev.txt`.
- Código en `stock/`: `rutas.py` las pantallas de stock y remitos, `deposito.py` las de pallets, depósito y terminal,
  `pallets.py` la lógica de pallets y ubicaciones, `historial.py` el historial por lote con saldos, `insumos.py` los
  insumos adicionales y el saldo de pallets de los clientes, `fotos.py` las fotos de los remitos (archivos en
  `datos/fotos`, carpeta configurable con `FOTOS`), `trasvasos.py` las pantallas de trasvasos, `vacios.py` el stock
  de envases vacíos y sucios, `remitos.py` la lectura y revisión de los renglones de un remito (la usan los remitos y
  los trasvasos), `qr.py` los QR (en SVG, con la librería `qrcode`), `db.py` la base y sus migraciones,
  `templates/` el HTML, `static/remito.js` el formulario de remito (renglones, pallets, kilos, transporte, insumos y
  fotos; lo usan el ingreso, el egreso y el trasvaso) y `static/fotos.js` el achicado de las fotos en el navegador.
- Un trasvaso es una fila de `trasvasos` más uno o dos `movimientos` con `trasvaso_id` (el ingreso y, si sale del
  depósito, el egreso). Los envases vacíos y sucios son movimientos en `envase_movs` con su `empresa_id` (y
  `trasvaso_id` o `movimiento_id` si vienen de un trasvaso o de un remito): el saldo de cada empresa es la suma de
  los que no están anulados, igual que el stock.
- Los kilos no se guardan en los remitos: siempre se calculan con los kilos por unidad actuales del producto.
- Lo que tiene cada pallet sale de los movimientos de la tabla `pallet_movs` (nunca se pisa un saldo), así que
  anular un egreso devuelve sola la mercadería a sus pallets.
- Pruebas: `pip install -r requirements-dev.txt` y `pytest`.
- Variables opcionales: `STOCK_DB` (ruta de la base), `STOCK_PUERTO` (por defecto 5000),
  `STOCK_SECRET_KEY`, `STOCK_DIAS_AVISO` (días para avisar vencimientos, por defecto 60), `STOCK_NO_ABRIR=1` (no abrir el navegador al arrancar).
