"""Exportar listados a Excel (.xlsx)."""
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


def armar_excel(titulo, encabezados, filas, pie=None, hojas_extra=()):
    """pie: renglones de totales que van al final, en negrita y fuera del filtro.

    hojas_extra: otras hojas del mismo archivo, como (título, encabezados, filas).
    """
    wb = Workbook()
    _hoja(wb.active, titulo, encabezados, filas, pie)
    for extra in hojas_extra:
        _hoja(wb.create_sheet(), *extra)
    salida = BytesIO()
    wb.save(salida)
    salida.seek(0)
    return salida


def _hoja(ws, titulo, encabezados, filas, pie=None):
    filas = [list(f) for f in filas]
    ws.title = titulo[:31]
    ws.append(encabezados)
    for celda in ws[1]:
        celda.font = Font(bold=True, color="FFFFFF")
        celda.fill = PatternFill("solid", fgColor="1F4E79")
    for fila in filas:
        ws.append(fila)
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions
    if pie:
        ws.append([])
        for fila in pie:
            ws.append(list(fila))
            for celda in ws[ws.max_row]:
                celda.font = Font(bold=True)
    for i, enc in enumerate(encabezados, start=1):
        valores = [f[i - 1] for f in filas if i - 1 < len(f) and f[i - 1] is not None]
        largo = max([len(str(enc))] + [len(str(v)) for v in valores])
        ws.column_dimensions[get_column_letter(i)].width = min(max(10, largo + 2), 60)
