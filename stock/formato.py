"""Formatos para mostrar números, fechas y pallets."""
import math


def formato_numero(valor):
    """1234.5 -> '1.234,5' ; 10.0 -> '10' (formato argentino)."""
    if valor is None:
        return ""
    valor = float(valor)
    if abs(valor - round(valor)) < 1e-9:
        texto = f"{int(round(valor)):,}"
    else:
        texto = f"{valor:,.3f}".rstrip("0").rstrip(".")
    return texto.replace(",", "X").replace(".", ",").replace("X", ".")


def formato_fecha(valor):
    """'2026-11-16' -> '16/11/2026'."""
    if not valor:
        return ""
    partes = str(valor)[:10].split("-")
    if len(partes) != 3:
        return valor
    return f"{partes[2]}/{partes[1]}/{partes[0]}"


def cantidad_pallets(cantidad, por_pallet):
    """Cantidad expresada en pallets (con decimales), o None si el producto no tiene el dato."""
    if not por_pallet or cantidad is None:
        return None
    return round(float(cantidad) / float(por_pallet), 3)


def texto_pallets(cantidad, por_pallet, unidad=""):
    """100 bolsas con 40 por pallet -> '2 pallets + 20 bolsas'."""
    if not por_pallet or cantidad is None or cantidad <= 0:
        return ""
    cantidad, por_pallet = float(cantidad), float(por_pallet)
    enteros = math.floor(cantidad / por_pallet + 1e-9)
    resto = cantidad - enteros * por_pallet
    if resto < 1e-9:
        resto = 0
    palabra = "pallet" if enteros == 1 else "pallets"
    if not resto:
        return f"{enteros} {palabra}"
    if enteros == 0:
        return "menos de 1 pallet"
    return f"{enteros} {palabra} + {formato_numero(resto)} {(unidad or '').lower()}".rstrip()
