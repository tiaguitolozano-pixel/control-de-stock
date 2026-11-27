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


def contar_pallets(cantidad, por_pallet):
    """(pallets, completos, resto): en cuántos pallets va la cantidad, contando el último aunque no esté lleno.

    80 bolsas con 48 por pallet -> (2, 1, 32): un pallet completo y otro con 32 bolsas.
    """
    cantidad, por_pallet = abs(float(cantidad)), float(por_pallet)
    completos = math.floor(cantidad / por_pallet + 1e-9)
    resto = round(cantidad - completos * por_pallet, 6)
    if resto < 1e-9:
        resto = 0
    return completos + (1 if resto else 0), completos, resto


def cantidad_pallets(cantidad, por_pallet):
    """En cuántos pallets va la cantidad (contando el incompleto), o None si el producto no tiene el dato.

    Para salidas (cantidad negativa) el número es negativo.
    """
    if not por_pallet or cantidad is None:
        return None
    pallets = contar_pallets(cantidad, por_pallet)[0]
    return -pallets if float(cantidad) < 0 else pallets


def texto_kilos(cantidad, kilos_por_unidad):
    """10 tambores de 200 kg -> '2.000 kg' ('' si el producto no tiene cargados los kilos)."""
    if not kilos_por_unidad or cantidad is None:
        return ""
    return f"{formato_numero(round(float(cantidad) * float(kilos_por_unidad), 3))} kg"


def texto_pallets(cantidad, por_pallet, unidad=""):
    """En cuántos pallets va la cantidad, contando el último aunque no esté lleno (así se arman y se cargan).

    80 bolsas con 48 por pallet -> '2 pallets (1 completo + 1 de 32 bolsas)'; 96 -> '2 pallets';
    32 -> '1 pallet de 32 bolsas'.
    """
    if not por_pallet or cantidad is None or cantidad <= 0:
        return ""
    pallets, completos, resto = contar_pallets(cantidad, por_pallet)
    unidad = f" {unidad.lower()}" if unidad else ""
    if not resto:
        return f"{pallets} {'pallet' if pallets == 1 else 'pallets'}"
    if not completos:
        return f"1 pallet de {formato_numero(resto)}{unidad}"
    return (f"{pallets} pallets ({completos} {'completo' if completos == 1 else 'completos'} + "
            f"1 de {formato_numero(resto)}{unidad})")
