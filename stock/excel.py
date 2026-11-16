"""Exportar listados a Excel (.xlsx)."""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


def armar_excel(titulo, encabezados, filas):
    wb = Workbook()
    ws = wb.active
    ws.title = titulo[:31]
    ws.append(encabezados)
    for celda in ws[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1F4E79")
    for fila in filas:
        ws.append(list(fila))
    for i, enc in enumerate(encabezados, start=1):
        largo = max([len(str(enc))] + [len(str(f[i - 1])) for f in filas if f[i - 1] is not None])
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, largo + 2), 60)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    salida = BytesIO()
    wb.save(salida)
    salida.seek(0)
    return salida
