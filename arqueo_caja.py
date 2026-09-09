import io
import os
import re
import zipfile
import copy
import math
from uuid import uuid4
from datetime import date, datetime
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET
from xml.dom import minidom

import pandas as pd
import streamlit as st
from cajas_persistencia import ErrorPersistenciaCaja, huella_movimientos
ZONA_HORARIA_CAJA = ZoneInfo("America/Mexico_City")


MEDIOS_CAPTURA = [
    "TC AFIRME",
    "TC KUSHKI",
    "TC AMERICAN EXPRESS",
    "TC BANCOMER",
    "TC BANAMEX",
    "TC AFIRME - MESES SIN INTERESES",
    "TC KUSHKI - MESES SIN INTERESES",
    "TC AMERICAN EXPRESS - MESES SIN INTERESES",
    "TC BANCOMER - MESES SIN INTERESES",
    "TC BANAMEX - MESES SIN INTERESES",
    "BONO DEVOLUCION",
    "TD AFIRME",
    "TD KUSHKI",
    "TD BANCOMER",
    "TD BANAMEX",
    "TRANSFERENCIA BBVA",
    "EFECTIVO",
    "NOTA CREDITO",
]

MEDIOS_ERP = [
    "TC AFIRME",
    "TC AMERICAN EXPRESS",
    "TC BANCOMER",
    "TC BANAMEX",
    "BONO DEVOLUCION",
    "TD AFIRME",
    "TD BANCOMER",
    "TD BANAMEX",
    "TRANSFERENCIA BBVA",
    "EFECTIVO",
    "NOTA CREDITO",
]

# El Corte Z agrupa Kushki y los meses sin intereses dentro del banco o
# adquirente correspondiente. Conservamos el detalle para llenar el formato
# final, pero comparamos la suma contra el renglón agregado del ERP.
GRUPO_CORTE_Z = {
    "TC AFIRME": "TC AFIRME",
    "TC KUSHKI": "TC AFIRME",
    "TC AFIRME - MESES SIN INTERESES": "TC AFIRME",
    "TC KUSHKI - MESES SIN INTERESES": "TC AFIRME",
    "TC AMERICAN EXPRESS": "TC AMERICAN EXPRESS",
    "TC AMERICAN EXPRESS - MESES SIN INTERESES": "TC AMERICAN EXPRESS",
    "TC BANCOMER": "TC BANCOMER",
    "TC BANCOMER - MESES SIN INTERESES": "TC BANCOMER",
    "TC BANAMEX": "TC BANAMEX",
    "TC BANAMEX - MESES SIN INTERESES": "TC BANAMEX",
    "BONO DEVOLUCION": "BONO DEVOLUCION",
    "TD AFIRME": "TD AFIRME",
    "TD KUSHKI": "TD AFIRME",
    "TD BANCOMER": "TD BANCOMER",
    "TD BANAMEX": "TD BANAMEX",
    "TRANSFERENCIA BBVA": "TRANSFERENCIA BBVA",
    "EFECTIVO": "EFECTIVO",
    "NOTA CREDITO": "NOTA CREDITO",
}

ALIASES_CORTE_Z = {
    "TC AFIRME": "TC AFIRME",
    "TC AMERICAN EXPRES": "TC AMERICAN EXPRESS",
    "TC AMERICAN EXPRESS": "TC AMERICAN EXPRESS",
    "TC BANCOMER": "TC BANCOMER",
    "TC BBVA": "TC BANCOMER",
    "TC BANAMEX": "TC BANAMEX",
    "BONO DEVOLUCION": "BONO DEVOLUCION",
    "TD AFIRME": "TD AFIRME",
    "TD BANCOMER": "TD BANCOMER",
    "TD BBVA": "TD BANCOMER",
    "TD BANAMEX": "TD BANAMEX",
    "TRANSFERENCIA BBVA": "TRANSFERENCIA BBVA",
    "EFECTIVO": "EFECTIVO",
    "NOTA CREDITO": "NOTA CREDITO",
}


def _numero(texto):
    limpio = re.sub(r"[^0-9,.-]", "", str(texto)).replace(",", "")
    if not limpio or limpio in {"-", ".", "-."}:
        return None
    try:
        return float(limpio)
    except ValueError:
        return None


def interpretar_corte_z(texto):
    """Convierte el texto copiado del Corte Z en importes comparables."""
    resultado = {medio: 0.0 for medio in MEDIOS_ERP}
    fecha_reporte = None
    seccion_medios = False

    for linea_original in str(texto or "").splitlines():
        linea = " ".join(linea_original.replace("_", " ").split())
        mayusculas = linea.upper()

        if fecha_reporte is None and mayusculas.startswith("FECHA:"):
            coincidencia = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", linea)
            if coincidencia:
                fecha_reporte = date(*map(int, coincidencia.groups()))

        if "MEDIO DE PAGO" in mayusculas:
            seccion_medios = True
            continue
        if seccion_medios and "TOTALES" in mayusculas:
            seccion_medios = False

        if not seccion_medios:
            continue

        for alias, medio in ALIASES_CORTE_Z.items():
            if mayusculas.startswith(alias):
                importes = re.findall(r"-?\d[\d,]*\.\d{2}", linea)
                if importes:
                    resultado[medio] = _numero(importes[0]) or 0.0
                break

    if not any(abs(valor) > 0 for valor in resultado.values()):
        raise ValueError(
            "No pude encontrar la sección 'MEDIO DE PAGO' del Corte Z. "
            "Copia el reporte completo desde el ERP y vuelve a pegarlo."
        )

    return {"fecha": fecha_reporte, "medios": resultado}


def interpretar_corte_x(texto):
    """Extrae los datos de cierre del comprobante diario pegado desde el ERP."""
    lineas = [" ".join(linea.replace("_", " ").split()) for linea in str(texto or "").splitlines()]

    def buscar(prefijo, ocurrencia=0):
        coincidencias = [linea for linea in lineas if linea.upper().startswith(prefijo.upper())]
        return coincidencias[ocurrencia] if len(coincidencias) > ocurrencia else ""

    fecha = None
    linea_fecha = buscar("FECHA DEL COMPROBANTE")
    coincidencia_fecha = re.search(r"(20\d{2})[/-](\d{1,2})[/-](\d{1,2})", linea_fecha)
    if coincidencia_fecha:
        fecha = date(*map(int, coincidencia_fecha.groups()))

    linea_facturas = buscar("FACTURA DE VENTA")
    consecutivos = [int(valor) for valor in re.findall(r"\b\d+\b", linea_facturas)]
    linea_nc = buscar("NOTA CREDITO")
    datos_nc = [int(valor) for valor in re.findall(r"\b\d+\b", linea_nc)]

    def importe_de(prefijo):
        linea = buscar(prefijo)
        importes = re.findall(r"-?\d[\d,]*\.\d{2}", linea)
        return _numero(importes[-1]) if importes else None

    resultado = {
        "fecha": fecha,
        "consecutivo_inicial": consecutivos[0] if len(consecutivos) >= 1 else None,
        "consecutivo_final": consecutivos[1] if len(consecutivos) >= 2 else None,
        "transacciones_venta": consecutivos[2] if len(consecutivos) >= 3 else None,
        "notas_credito": datos_nc[2] if len(datos_nc) >= 3 else 0,
        "venta_bruta": importe_de("VENTA BRUTA"),
        "descuentos": importe_de("-DESCUENTOS"),
        "venta": importe_de("=VENTA"),
        "impuestos": importe_de("-IMPUESTOS"),
        "venta_neta": importe_de("=VENTA NETA"),
    }
    obligatorios = ["fecha", "consecutivo_inicial", "consecutivo_final", "transacciones_venta", "venta", "venta_neta"]
    faltantes = [campo for campo in obligatorios if resultado.get(campo) is None]
    if faltantes:
        raise ValueError(
            "No pude leer todos los datos del Corte X. Copia el comprobante completo "
            "desde el ERP. Faltan: " + ", ".join(faltantes)
        )
    resultado["tickets_efectivos"] = max(
        0, resultado["transacciones_venta"] - resultado["notas_credito"]
    )
    return resultado


def _ruta_plantilla(nombre):
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "plantillas_privadas", nombre)


NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
NS_REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
NS_REL_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
ET.register_namespace("", NS_MAIN)
ET.register_namespace("r", NS_REL_DOC)


def _hijos_xml(nodo, nombre):
    return [hijo for hijo in nodo.childNodes
            if hijo.nodeType == hijo.ELEMENT_NODE
            and hijo.namespaceURI == NS_MAIN and hijo.localName == nombre]


def _nuevo_xml(documento, padre, nombre):
    prefijo = padre.prefix
    return documento.createElementNS(NS_MAIN, f"{prefijo}:{nombre}" if prefijo else nombre)


def _columna_xml(referencia):
    numero = 0
    for letra in re.match(r"[A-Z]+", referencia).group():
        numero = numero * 26 + ord(letra) - ord("A") + 1
    return numero


def _editar_ooxml(ruta, cambios_por_hoja, limpiar_por_hoja=None):
    """Cambia celdas puntuales sin reconstruir el libro ni sus macros.

    limpiar_por_hoja permite borrar únicamente el contenido de celdas de captura
    antes de escribir los datos actuales, conservando formato, protección y
    estructura de la plantilla.
    """
    limpiar_por_hoja = limpiar_por_hoja or {}
    origen_zip = io.BytesIO(ruta) if isinstance(ruta, (bytes, bytearray)) else ruta
    with zipfile.ZipFile(origen_zip, "r") as origen:
        archivos = {nombre: origen.read(nombre) for nombre in origen.namelist()}

    libro = ET.fromstring(archivos["xl/workbook.xml"])
    relaciones = ET.fromstring(archivos["xl/_rels/workbook.xml.rels"])
    destinos = {
        relacion.attrib["Id"]: relacion.attrib["Target"]
        for relacion in relaciones.findall(f"{{{NS_REL_PKG}}}Relationship")
    }
    rutas_hojas = {}
    for hoja in libro.find(f"{{{NS_MAIN}}}sheets"):
        relacion_id = hoja.attrib[f"{{{NS_REL_DOC}}}id"]
        destino = destinos[relacion_id].replace("\\", "/")
        rutas_hojas[hoja.attrib["name"]] = (
            destino.lstrip("/") if destino.startswith("/xl/") else "xl/" + destino.lstrip("/")
        )

    for nombre_hoja, cambios in cambios_por_hoja.items():
        if nombre_hoja not in rutas_hojas:
            raise ValueError(f"La plantilla no contiene la pestaña {nombre_hoja}.")
        ruta_xml = rutas_hojas[nombre_hoja]
        # Preserve prefix declarations, including those referenced only by
        # mc:Ignorable or other QName-valued attributes. ElementTree drops them.
        documento = minidom.parseString(archivos[ruta_xml])
        datos = _hijos_xml(documento.documentElement, "sheetData")[0]
        celdas = {
            celda.getAttribute("r"): celda
            for fila in _hijos_xml(datos, "row")
            for celda in _hijos_xml(fila, "c")
        }

        for referencia in limpiar_por_hoja.get(nombre_hoja, []):
            celda = celdas.get(referencia)
            if celda is None:
                continue
            for nombre in ("f", "v", "is"):
                for hijo in _hijos_xml(celda, nombre):
                    celda.removeChild(hijo)
            if celda.hasAttribute("t"):
                celda.removeAttribute("t")

        for referencia, valor in cambios.items():
            celda = celdas.get(referencia)
            if celda is None:
                numero_fila = int(re.search(r"\d+", referencia).group())
                fila = next(
                    (item for item in _hijos_xml(datos, "row") if int(item.getAttribute("r")) == numero_fila),
                    None,
                )
                if fila is None:
                    fila = _nuevo_xml(documento, datos, "row")
                    fila.setAttribute("r", str(numero_fila))
                    siguiente = next((item for item in _hijos_xml(datos, "row")
                                      if int(item.getAttribute("r")) > numero_fila), None)
                    datos.insertBefore(fila, siguiente)
                celda = _nuevo_xml(documento, fila, "c")
                celda.setAttribute("r", referencia)
                siguiente = next((item for item in _hijos_xml(fila, "c")
                                  if _columna_xml(item.getAttribute("r")) > _columna_xml(referencia)), None)
                fila.insertBefore(celda, siguiente)
                celdas[referencia] = celda
            for nombre in ("f", "v", "is"):
                for hijo in _hijos_xml(celda, nombre):
                    celda.removeChild(hijo)
            if isinstance(valor, str):
                celda.setAttribute("t", "inlineStr")
                nodo_is = _nuevo_xml(documento, celda, "is")
                nodo_t = _nuevo_xml(documento, nodo_is, "t")
                nodo_t.setAttributeNS("http://www.w3.org/XML/1998/namespace", "xml:space", "preserve")
                nodo_t.appendChild(documento.createTextNode(valor))
                nodo_is.appendChild(nodo_t)
                celda.insertBefore(nodo_is, celda.firstChild)
            else:
                if celda.hasAttribute("t"):
                    celda.removeAttribute("t")
                nodo_v = _nuevo_xml(documento, celda, "v")
                nodo_v.appendChild(documento.createTextNode(str(valor)))
                celda.insertBefore(nodo_v, celda.firstChild)
        archivos[ruta_xml] = documento.toxml(encoding="utf-8")
        documento.unlink()

    documento_libro = minidom.parseString(archivos["xl/workbook.xml"])
    raiz_libro = documento_libro.documentElement
    calculos = _hijos_xml(raiz_libro, "calcPr")
    if calculos:
        calculo = calculos[0]
    else:
        calculo = _nuevo_xml(documento_libro, raiz_libro, "calcPr")
        posteriores = {"oleSize", "customWorkbookViews", "pivotCaches", "smartTagPr",
                       "smartTagTypes", "webPublishing", "fileRecoveryPr",
                       "webPublishObjects", "extLst"}
        siguiente = next((n for n in raiz_libro.childNodes
                          if n.nodeType == n.ELEMENT_NODE and n.localName in posteriores), None)
        raiz_libro.insertBefore(calculo, siguiente)
    calculo.setAttribute("fullCalcOnLoad", "1")
    calculo.setAttribute("forceFullCalc", "1")
    archivos["xl/workbook.xml"] = documento_libro.toxml(encoding="utf-8")
    documento_libro.unlink()

    if "xl/calcChain.xml" in archivos:
        del archivos["xl/calcChain.xml"]

        relaciones_libro = minidom.parseString(archivos["xl/_rels/workbook.xml.rels"])
        for nodo in list(relaciones_libro.documentElement.childNodes):
            if (
                nodo.nodeType == nodo.ELEMENT_NODE
                and nodo.getAttribute("Type").endswith("/calcChain")
            ):
                relaciones_libro.documentElement.removeChild(nodo)
        archivos["xl/_rels/workbook.xml.rels"] = relaciones_libro.toxml(encoding="utf-8")
        relaciones_libro.unlink()

        tipos = minidom.parseString(archivos["[Content_Types].xml"])
        for nodo in list(tipos.documentElement.childNodes):
            if (
                nodo.nodeType == nodo.ELEMENT_NODE
                and nodo.getAttribute("PartName") == "/xl/calcChain.xml"
            ):
                tipos.documentElement.removeChild(nodo)
        archivos["[Content_Types].xml"] = tipos.toxml(encoding="utf-8")
        tipos.unlink()

    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as destino:
        for nombre, contenido in archivos.items():
            destino.writestr(nombre, contenido)
    return salida.getvalue()


def _valor_celda_ooxml(archivos, celda):
    """Lee el valor visible de una celda OOXML sin depender de openpyxl."""
    if celda is None:
        return None
    tipo = celda.getAttribute("t")
    if tipo == "inlineStr":
        nodos_is = _hijos_xml(celda, "is")
        if not nodos_is:
            return ""
        textos = []
        for nodo in nodos_is[0].getElementsByTagNameNS(NS_MAIN, "t"):
            textos.append("".join(h.data for h in nodo.childNodes if h.nodeType == h.TEXT_NODE))
        return "".join(textos)

    nodos_v = _hijos_xml(celda, "v")
    if not nodos_v:
        return None
    valor = "".join(h.data for h in nodos_v[0].childNodes if h.nodeType == h.TEXT_NODE)

    if tipo == "s":
        try:
            indice = int(valor)
            compartidos = minidom.parseString(archivos.get("xl/sharedStrings.xml", b""))
            items = compartidos.getElementsByTagNameNS(NS_MAIN, "si")
            if indice < len(items):
                textos = []
                for nodo in items[indice].getElementsByTagNameNS(NS_MAIN, "t"):
                    textos.append("".join(h.data for h in nodo.childNodes if h.nodeType == h.TEXT_NODE))
                compartidos.unlink()
                return "".join(textos)
            compartidos.unlink()
        except Exception:
            return valor
    return valor


def leer_celdas_ooxml(ruta, hoja_nombre, referencias):
    """Devuelve valores de celdas concretas de una hoja del libro."""
    origen_zip = io.BytesIO(ruta) if isinstance(ruta, (bytes, bytearray)) else ruta
    with zipfile.ZipFile(origen_zip, "r") as origen:
        archivos = {nombre: origen.read(nombre) for nombre in origen.namelist()}

    libro = ET.fromstring(archivos["xl/workbook.xml"])
    relaciones = ET.fromstring(archivos["xl/_rels/workbook.xml.rels"])
    destinos = {
        relacion.attrib["Id"]: relacion.attrib["Target"]
        for relacion in relaciones.findall(f"{{{NS_REL_PKG}}}Relationship")
    }
    ruta_xml = None
    for hoja in libro.find(f"{{{NS_MAIN}}}sheets"):
        if hoja.attrib.get("name") == hoja_nombre:
            relacion_id = hoja.attrib[f"{{{NS_REL_DOC}}}id"]
            destino = destinos[relacion_id].replace("\\", "/")
            ruta_xml = destino.lstrip("/") if destino.startswith("/xl/") else "xl/" + destino.lstrip("/")
            break
    if not ruta_xml or ruta_xml not in archivos:
        raise ValueError(f"El libro no contiene la pestaña {hoja_nombre}.")

    documento = minidom.parseString(archivos[ruta_xml])
    datos = _hijos_xml(documento.documentElement, "sheetData")[0]
    celdas = {
        celda.getAttribute("r"): celda
        for fila in _hijos_xml(datos, "row")
        for celda in _hijos_xml(fila, "c")
    }
    resultado = {ref: _valor_celda_ooxml(archivos, celdas.get(ref)) for ref in referencias}
    documento.unlink()
    return resultado


def _texto_limpio(valor):
    if valor is None:
        return ""
    return "" if pd.isna(valor) else str(valor).strip()


def _importe_factura(valor):
    if valor is None or valor == "":
        return None
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numero):
        raise ValueError("El importe debe ser un número finito.")
    return round(numero, 2)


def _rangos_consecutivos(numeros):
    numeros = sorted(set(int(n) for n in numeros))
    if not numeros:
        return []
    rangos = []
    inicio = anterior = numeros[0]
    for numero in numeros[1:]:
        if numero == anterior + 1:
            anterior = numero
            continue
        rangos.append(str(inicio) if inicio == anterior else f"{inicio}-{anterior}")
        inicio = anterior = numero
    rangos.append(str(inicio) if inicio == anterior else f"{inicio}-{anterior}")
    return rangos


def normalizar_facturacion(facturacion, corte_x, exigir_global=False):
    """Valida facturas de clientes y calcula los rangos de público general."""
    facturacion = facturacion or {}
    inicio = int(corte_x["consecutivo_inicial"])
    fin = int(corte_x["consecutivo_final"])
    if inicio > fin:
        raise ValueError("El rango de tickets del Corte X no es válido.")
    clientes = []
    tickets_usados = set()

    for fila in facturacion.get("clientes", []) or []:
        ticket_raw = fila.get("ticket")
        folio = _texto_limpio(fila.get("folio"))
        importe = _importe_factura(fila.get("importe"))
        vacia = (ticket_raw in (None, "")) and not folio and importe is None
        if vacia:
            continue
        if ticket_raw in (None, ""):
            raise ValueError("Hay una factura de cliente sin número de ticket.")
        try:
            numero_ticket = float(ticket_raw)
            if not math.isfinite(numero_ticket) or not numero_ticket.is_integer():
                raise ValueError("El ticket debe ser entero.")
            ticket = int(numero_ticket)
        except (TypeError, ValueError):
            raise ValueError(f"El ticket {ticket_raw!r} no es válido.") from None
        if ticket < inicio or ticket > fin:
            raise ValueError(f"El ticket {ticket} está fuera del rango {inicio}-{fin} del Corte X.")
        if ticket in tickets_usados:
            raise ValueError(f"El ticket {ticket} está repetido en facturas de clientes.")
        if not folio:
            raise ValueError(f"Falta el folio de factura para el ticket {ticket}.")
        if importe is None or abs(importe) < 0.005:
            raise ValueError(f"Falta un importe válido para el ticket {ticket}.")
        tickets_usados.add(ticket)
        clientes.append({"ticket": ticket, "folio": folio, "importe": importe})

    if len(clientes) > 11:
        raise ValueError("El formato admite como máximo 11 facturas de cliente por día.")

    folio_global = _texto_limpio(facturacion.get("folio_global"))
    tickets_publico = [numero for numero in range(inicio, fin + 1) if numero not in tickets_usados]
    rangos_publico = _rangos_consecutivos(tickets_publico)
    if len(rangos_publico) > 6:
        raise ValueError(
            "La separación de tickets de público general genera más de 6 bloques. "
            "El machote sólo tiene espacio en C70:C75; revisa las facturas de cliente."
        )
    if folio_global and not tickets_publico:
        raise ValueError("No quedan tickets para público general, por lo que no corresponde capturar una factura global.")
    if exigir_global and tickets_publico and not folio_global:
        raise ValueError("Falta el folio de la factura de público general.")

    return {
        "clientes": sorted(clientes, key=lambda x: x["ticket"]),
        "folio_global": folio_global,
        "rangos_publico": rangos_publico,
        "tickets_publico": tickets_publico,
    }


def leer_facturacion_formato_corte(contenido, fecha_trabajo, corte_x=None):
    """Lee la facturación ya escrita en el machote mensual."""
    hoja = f"{fecha_trabajo.day:02d}"
    referencias = []
    for fila in range(58, 69):
        referencias.extend((f"C{fila}", f"E{fila}", f"G{fila}"))
    referencias.extend([f"C{fila}" for fila in range(70, 76)])
    referencias.append("E72")
    valores = leer_celdas_ooxml(contenido, hoja, referencias)

    clientes = []
    for fila in range(58, 69):
        ticket = valores.get(f"C{fila}")
        folio = _texto_limpio(valores.get(f"E{fila}"))
        importe = _importe_factura(valores.get(f"G{fila}"))
        if ticket in (None, "") and not folio and importe is None:
            continue
        try:
            ticket_num = int(float(ticket)) if ticket not in (None, "") else None
        except (TypeError, ValueError):
            ticket_num = None
        clientes.append({"ticket": ticket_num, "folio": folio, "importe": importe})

    rangos = [_texto_limpio(valores.get(f"C{fila}")) for fila in range(70, 76)]
    rangos = [r for r in rangos if r]
    resultado = {
        "clientes": clientes,
        "folio_global": _texto_limpio(valores.get("E72")),
        "rangos_publico": rangos,
    }
    if corte_x is not None:
        try:
            normalizada = normalizar_facturacion(resultado, corte_x)
            resultado.update(normalizada)
        except ValueError:
            pass
    return resultado


def _cambios_facturacion_excel(facturacion, corte_x):
    """Convierte la facturación validada al mapa exacto de celdas del machote."""
    normalizada = normalizar_facturacion(facturacion, corte_x)
    cambios = {}
    clientes = normalizada["clientes"]
    for indice, cliente in enumerate(clientes, start=58):
        cambios[f"C{indice}"] = cliente["ticket"]
        cambios[f"E{indice}"] = cliente["folio"]
        cambios[f"G{indice}"] = cliente["importe"]

    # Público general sólo se escribe cuando ya existe el folio global.
    if normalizada["folio_global"]:
        for indice, rango in enumerate(normalizada["rangos_publico"], start=70):
            cambios[f"C{indice}"] = rango
        cambios["E72"] = normalizada["folio_global"]
    return cambios, normalizada

def actualizar_facturacion_corte(contenido, fecha_trabajo, corte_x, facturacion):
    """Modifica sólo la facturación del día, conservando el cierre y otras hojas."""
    cambios, _ = _cambios_facturacion_excel(facturacion, corte_x)
    hoja = f"{fecha_trabajo.day:02d}"
    limpiar = ([f"{col}{fila}" for fila in range(58, 69) for col in ("C", "E", "G")]
               + [f"C{fila}" for fila in range(70, 76)] + ["E72"])
    return _editar_ooxml(contenido, {hoja: cambios}, {hoja: limpiar})


def generar_formato_corte(
    fecha_trabajo,
    corte_x,
    vouchers,
    responsable,
    observaciones="",
    plantilla_bytes=None,
    facturacion=None,
):
    if plantilla_bytes is not None:
        ruta = plantilla_bytes
    else:
        ruta = _ruta_plantilla("formato_corte_caja.xlsx")
        if not os.path.exists(ruta):
            raise FileNotFoundError("Falta instalar la plantilla privada del formato de Corte de Caja.")
    hoja_nombre = f"{fecha_trabajo.day:02d}"
    detalle = _totales_capturados(vouchers)
    cambios = {
        "G3": fecha_trabajo.strftime("%d/%m/%Y"),
        "C5": corte_x["venta"],
        "E11": detalle.get("EFECTIVO", 0.0),
        "E17": detalle.get("TRANSFERENCIA BBVA", 0.0),
        "E22": detalle.get("TC AFIRME", 0.0),
        "E23": detalle.get("TD AFIRME", 0.0),
        "E25": (
        detalle.get("TC AFIRME - MESES SIN INTERESES", 0.0)
        + detalle.get("TC AMERICAN EXPRESS - MESES SIN INTERESES", 0.0)
        ),
        "E26": detalle.get("TC AMERICAN EXPRESS", 0.0),
        "E29": (
        detalle.get("TC KUSHKI", 0.0)
        + detalle.get("TC KUSHKI - MESES SIN INTERESES", 0.0)
        ),
        "E30": detalle.get("TD KUSHKI", 0.0),
        "E35": detalle.get("TC BANCOMER", 0.0),
        "E36": detalle.get("TD BANCOMER", 0.0),
        "E38": detalle.get("TC BANCOMER - MESES SIN INTERESES", 0.0),
        "E41": detalle.get("TC BANAMEX", 0.0),
        "E42": detalle.get("TD BANAMEX", 0.0),
        "E44": detalle.get("TC BANAMEX - MESES SIN INTERESES", 0.0),
    }
    rango_tickets = f"{corte_x['consecutivo_inicial']}-{corte_x['consecutivo_final']}"
    cambios.update({
        "C53": rango_tickets,
        "B78": observaciones.strip(),
        "D82": responsable.strip(),
    })

    limpiar = [
        "G3", "C5",
        "E11", "E17", "E22", "E23", "E25", "E26",
        "E29", "E30", "E35", "E36", "E38", "E41", "E42", "E44",
        "C53",
        *[f"C{fila}" for fila in range(58, 69)],
        *[f"E{fila}" for fila in range(58, 69)],
        *[f"G{fila}" for fila in range(58, 69)],
        "C71", "C72", "C73", "E71", "E72", "E73", "G71", "G73",
        "B78", "D82",
    ]
    cambios_facturacion, _ = _cambios_facturacion_excel(facturacion or {}, corte_x)
    cambios.update(cambios_facturacion)
    limpiar.extend(f"C{fila}" for fila in range(70, 76))
    return _editar_ooxml(
        ruta,
        {hoja_nombre: cambios},
        limpiar_por_hoja={hoja_nombre: limpiar},
    )


MESES_ESTADILLO = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL", 5: "MAYO", 6: "JUNIO",
    7: "JULIO", 8: "AGOSTO", 9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE ",
}


def generar_estadillo(fecha_trabajo, corte_x, piezas, tickets, plantilla_bytes=None):
    ruta = plantilla_bytes if plantilla_bytes is not None else _ruta_plantilla("estadillo_2026.xlsm")
    if plantilla_bytes is None and not os.path.exists(ruta):
        raise FileNotFoundError("Falta instalar la plantilla privada del estadillo.")
    fila = 14 + fecha_trabajo.day
    return _editar_ooxml(
        ruta,
        {MESES_ESTADILLO[fecha_trabajo.month]: {
            f"K{fila}": corte_x["venta_neta"],
            f"M{fila}": int(piezas),
            f"N{fila}": int(tickets),
        }},
    )


def _estado_inicial():
    hoy = datetime.now(ZONA_HORARIA_CAJA).date().isoformat()
    usuario = st.session_state.get("usuario_actual", "")
    if "caja_usuario" not in st.session_state:
        # An existing session from before persistence has no ownership marker.
        # Keep its data so _cargar_jornada can offer to import it explicitly.
        st.session_state["caja_usuario"] = usuario
    elif st.session_state["caja_usuario"] != usuario:
        st.session_state.pop("arqueo_caja", None)
        _limpiar_campos_caja()
        st.session_state["caja_usuario"] = usuario
    if "arqueo_caja" not in st.session_state:
        st.session_state.arqueo_caja = {
            "fecha": hoy,
            "vouchers": [],
            "cortes": [],
            "ultimo_corte": None,
        }
    for voucher in st.session_state.arqueo_caja["vouchers"]:
        voucher.setdefault("id", uuid4().hex)
    return st.session_state.arqueo_caja


def _limpiar_campos_facturacion():
    for clave in list(st.session_state):
        if str(clave).startswith(("editor_facturas_clientes_", "folio_global_")):
            st.session_state.pop(clave, None)


def _limpiar_campos_caja():
    _limpiar_campos_facturacion()
    for clave in ("arqueo_fecha_trabajo", "texto_corte_z", "texto_corte_x", "cierre_piezas",
                  "cierre_tickets", "cierre_observaciones", "confirmar_fecha_corte_x",
                  "importe_voucher", "movimiento_a_eliminar", "caja_cargada"):
        st.session_state.pop(clave, None)


def _guardar_cambio(estado, repositorio, accion, cerrar=False):
    if repositorio is not None:
        try:
            guardado = repositorio.guardar(estado, accion, cerrar=cerrar)
            if "documentos_cierre" in estado:
                guardado["documentos_cierre"] = estado["documentos_cierre"]
            st.session_state.arqueo_caja = guardado
        except ErrorPersistenciaCaja as error:
            st.error(str(error))
            return False
    else:
        st.session_state.arqueo_caja = estado
    return True


def _cargar_jornada(estado, repositorio):
    contexto = (repositorio.usuario, estado["fecha"])
    if st.session_state.get("caja_cargada") == contexto:
        return estado
    try:
        guardado = repositorio.cargar(estado["fecha"])
    except ErrorPersistenciaCaja as error:
        st.error(str(error))
        st.stop()
    local_con_datos = bool(estado.get("vouchers") or estado.get("cortes") or estado.get("corte_x"))
    if local_con_datos and not st.session_state.pop("caja_forzar_recarga", False):
        if guardado is None:
            st.warning("Esta sesión contiene datos que todavía no están guardados en Supabase.")
            if st.button("Guardar la sesión actual en Supabase"):
                if _guardar_cambio(estado, repositorio, "importar_sesion"):
                    st.session_state["caja_cargada"] = contexto
                    st.rerun()
        else:
            st.warning("Hay datos en esta sesión y también una jornada guardada. Puedes recuperar la guardada; se conservará una copia temporal de esta sesión mientras la aplicación siga abierta.")
            if st.button("Recuperar jornada de Supabase"):
                st.session_state["caja_respaldo_sesion"] = copy.deepcopy(estado)
                st.session_state.arqueo_caja = guardado
                st.session_state["caja_cargada"] = contexto
                st.rerun()
        st.stop()
    estado = guardado or {"fecha": estado["fecha"], "vouchers": [], "cortes": [], "ultimo_corte": None, "_version": 0}
    st.session_state.arqueo_caja = estado
    st.session_state["caja_cargada"] = contexto
    return estado


def _reiniciar_si_cambia_fecha(estado, fecha_trabajo):
    fecha_iso = fecha_trabajo.isoformat()
    if estado["fecha"] != fecha_iso:
        _limpiar_campos_facturacion()
        st.session_state.arqueo_caja = {
            "fecha": fecha_iso,
            "vouchers": [],
            "cortes": [],
            "ultimo_corte": None,
        }
        for clave in ("caja_cargada", "texto_corte_z", "texto_corte_x", "cierre_piezas",
                      "cierre_tickets", "cierre_observaciones", "confirmar_fecha_corte_x",
                      "movimiento_a_eliminar"):
            st.session_state.pop(clave, None)
        st.rerun()


def _totales_capturados(vouchers):
    totales = {medio: 0.0 for medio in MEDIOS_CAPTURA}
    for voucher in vouchers:
        totales[voucher["medio"]] += float(voucher["importe"])
    return totales


def _totales_para_corte_z(vouchers):
    totales = {medio: 0.0 for medio in MEDIOS_ERP}
    for voucher in vouchers:
        grupo = GRUPO_CORTE_Z[voucher["medio"]]
        totales[grupo] += float(voucher["importe"])
    return totales


def _observacion_diferencias_clasificacion(comparacion):
    """Describe diferencias por medio cuando el total general sí cuadra."""
    diferencias = []
    for _, fila in comparacion.iterrows():
        diferencia = float(fila["Diferencia"])
        if abs(diferencia) >= 0.01:
            diferencias.append(
                f"{fila['Medio de pago']}: capturado ${float(fila['Capturado']):,.2f}, "
                f"Corte Z ${float(fila['Corte Z']):,.2f}, diferencia {diferencia:+,.2f}"
            )
    if not diferencias:
        return ""
    return (
        "Diferencia de clasificación de medios de pago detectada. "
        "El total general sí cuadra y se validaron los vouchers físicos. "
        + "; ".join(diferencias)
        + "."
    )


def _dataframe_facturas_clientes(facturacion):
    filas = []
    clientes = (facturacion or {}).get("clientes", []) or []
    for indice in range(11):
        cliente = clientes[indice] if indice < len(clientes) else {}
        filas.append({
            "Ticket": cliente.get("ticket"),
            "Folio factura": cliente.get("folio", ""),
            "Importe": cliente.get("importe"),
        })
    return pd.DataFrame(filas).astype({"Ticket": "float64", "Folio factura": "string", "Importe": "float64"})


def _facturacion_desde_editor(tabla, folio_global):
    clientes = []
    for _, fila in tabla.iterrows():
        ticket = fila.get("Ticket")
        folio = fila.get("Folio factura")
        importe = fila.get("Importe")
        ticket_vacio = pd.isna(ticket) if not isinstance(ticket, str) else not ticket.strip()
        importe_vacio = pd.isna(importe) if not isinstance(importe, str) else not importe.strip()
        if ticket_vacio and not _texto_limpio(folio) and importe_vacio:
            continue
        clientes.append({
            "ticket": None if ticket_vacio else ticket,
            "folio": _texto_limpio(folio),
            "importe": None if importe_vacio else importe,
        })
    return {"clientes": clientes, "folio_global": _texto_limpio(folio_global)}


def _mostrar_preview_corte(fecha_trabajo, corte_x, vouchers, cierre_datos, facturacion):
    st.markdown("#### Vista previa del Corte de Caja")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Fecha", fecha_trabajo.strftime("%d/%m/%Y"))
    col2.metric("Venta total", f"${float(corte_x['venta']):,.2f}")
    col3.metric("Tickets", f"{corte_x['consecutivo_inicial']}-{corte_x['consecutivo_final']}")
    col4.metric("Piezas", str(cierre_datos.get("piezas", 0)))

    detalle = _totales_capturados(vouchers)
    pagos = pd.DataFrame([
        {"Medio de pago": medio, "Importe": importe}
        for medio, importe in detalle.items()
        if abs(float(importe)) >= 0.005
    ])
    if not pagos.empty:
        st.markdown("##### Medios de pago")
        st.dataframe(
            pagos,
            use_container_width=True,
            hide_index=True,
            column_config={"Importe": st.column_config.NumberColumn("Importe", format="$ %.2f")},
        )

    clientes = (facturacion or {}).get("clientes", []) or []
    st.markdown("##### Facturación")
    if clientes:
        tabla = pd.DataFrame([
            {
                "Ticket": item.get("ticket"),
                "Folio factura": item.get("folio", ""),
                "Importe": item.get("importe"),
            }
            for item in clientes
        ])
        st.dataframe(
            tabla,
            use_container_width=True,
            hide_index=True,
            column_config={"Importe": st.column_config.NumberColumn("Importe", format="$ %.2f")},
        )
    else:
        st.caption("Sin facturas de cliente capturadas.")

    folio_global = _texto_limpio((facturacion or {}).get("folio_global"))
    rangos = (facturacion or {}).get("rangos_publico", []) or []
    importe_clientes = sum(float(item.get("importe") or 0) for item in clientes)
    importe_global_estimado = float(corte_x["venta"]) - importe_clientes
    col_global1, col_global2 = st.columns(2)
    with col_global1:
        st.markdown("**Factura de público general**")
        st.write(f"Folio: {folio_global or 'Pendiente'}")
        st.write(f"Tickets: {', '.join(rangos) if rangos else 'Pendiente'}")
    with col_global2:
        st.markdown("**Importe calculado por el machote**")
        st.write(f"${importe_global_estimado:,.2f}")
        st.caption("El importe real permanece calculado por la fórmula G72 del Excel.")

    responsable = _texto_limpio(cierre_datos.get("responsable"))
    observaciones = _texto_limpio(cierre_datos.get("observaciones"))
    st.caption(f"Responsable: {responsable or 'Sin responsable'}")
    if observaciones:
        st.caption(f"Observaciones: {observaciones}")

def mostrar_arqueo_caja(
    repositorio=None,
    cargar_maestro_corte=None,
    guardar_documentos_drive=None,
    guardar_corte_drive=None,
    cargar_maestro_estadillo=None,
):
    estado = _estado_inicial()

    st.header("Arqueo de caja")
    st.caption(
        "Registra cada voucher una sola vez. Cuando quieras revisar la caja, "
        "pega el Corte Z acumulado del ERP."
    )

    fecha_trabajo = st.date_input(
        "Fecha de trabajo",
        value=date.fromisoformat(estado["fecha"]),
        key="arqueo_fecha_trabajo",
    )
    _reiniciar_si_cambia_fecha(estado, fecha_trabajo)

    def _obtener_base_corte_drive():
        if cargar_maestro_corte is None:
            return None
        maestro = cargar_maestro_corte(fecha_trabajo)
        if maestro and maestro.get("contenido"):
            return maestro
        return None

    if repositorio is not None:
        if st.button("Recargar datos guardados"):
            _limpiar_campos_facturacion()
            st.session_state.pop("caja_cargada", None)
            st.session_state["caja_forzar_recarga"] = True
            for clave in ("texto_corte_z", "texto_corte_x", "cierre_piezas", "cierre_tickets", "cierre_observaciones"):
                st.session_state.pop(clave, None)
        estado = _cargar_jornada(estado, repositorio)
        st.caption("Guardado en Supabase por fecha y usuario. Cada cambio se confirma antes de mostrarse como guardado.")
    else:
        st.warning("Modo de prueba: los movimientos sólo viven en esta sesión. El guardado permanente todavía no está activado.")
    estado = copy.deepcopy(estado)
    cerrada = estado.get("_cerrada", False)
    if cerrada:
        st.info("Esta jornada está cerrada. Puedes consultar sus movimientos y descargar los documentos.")

    st.subheader("1. Registrar movimiento")
    if st.session_state.pop("limpiar_importe_voucher", False):
        st.session_state["importe_voucher"] = None
    with st.form("form_nuevo_voucher", clear_on_submit=True):
        col1, col2, col3 = st.columns([2, 1, 2])
        with col1:
            medio = st.selectbox("Medio de pago", MEDIOS_CAPTURA, disabled=cerrada)
        with col2:
            importe = st.number_input(
                "Importe", min_value=0.0, value=None, step=0.01,
                format="%.2f", placeholder="Escribe el importe", key="importe_voucher", disabled=cerrada,
            )
        with col3:
            folio = st.text_input(
                "Folio o referencia (opcional)", disabled=cerrada,
                help="Puedes repetir una referencia con otro importe o medio de pago. "
                     "Si usas una tarjeta como referencia, captura sólo sus últimos cuatro dígitos.",
            )
        guardar = st.form_submit_button("Agregar movimiento", type="primary", disabled=cerrada)

    if guardar and not cerrada:
        if importe is None or importe <= 0:
            st.error("Escribe un importe mayor a cero.")
        elif folio.strip() and any(
            item.get("folio", "").strip().lower() == folio.strip().lower()
            and item["medio"] == medio
            and round(abs(float(item["importe"])) * 100) == round(float(importe) * 100)
            for item in estado["vouchers"]
        ):
            st.error(
                "Ya existe un movimiento con la misma referencia, medio de pago e importe. "
                "Si es otro pago, utiliza su folio o autorización para distinguirlo."
            )
        else:
            estado["vouchers"].append({
                "id": uuid4().hex,
                "hora":datetime.now(ZONA_HORARIA_CAJA).strftime("%H:%M"), 
                "medio": medio,
                "importe": -float(importe) if medio == "NOTA CREDITO" else float(importe),
                "folio": folio.strip(),
            })
            estado.pop("documentos_cierre", None)
            estado.pop("cierre_datos", None)
            if _guardar_cambio(estado, repositorio, "agregar_movimiento"):
                st.session_state["limpiar_importe_voucher"] = True
                st.rerun()
            st.stop()

    if estado["vouchers"]:
        for voucher in estado["vouchers"]:
            if "id" not in voucher:
                voucher["id"] = uuid4().hex
        tabla_vouchers = pd.DataFrame(estado["vouchers"])
        tabla_vouchers = tabla_vouchers.drop(columns=["id"])
        tabla_vouchers.index = tabla_vouchers.index + 1
        grupos = {
            medio: [v["importe"] for v in estado["vouchers"] if v["medio"] == medio]
            for medio in MEDIOS_CAPTURA
            if any(v["medio"] == medio for v in estado["vouchers"])
        }
        tabla_agrupada = pd.DataFrame({
            medio: pd.Series(importes, dtype=float)
            for medio, importes in grupos.items()
        })
        tabla_agrupada.index = [str(i + 1) for i in range(len(tabla_agrupada))]
        tabla_agrupada.index.name = "Posición en cada tipo de pago"
        tabla_agrupada.loc["Subtotal"] = tabla_agrupada.sum()
        st.subheader("Movimientos por tipo de pago")
        st.dataframe(
            tabla_agrupada,
            use_container_width=True,
            column_config={
                medio: st.column_config.NumberColumn(medio, format="$ %.2f")
                for medio in grupos
            },
        )
        st.caption("Cada columna reúne un tipo de pago. Los espacios vacíos no representan movimientos.")
        with st.expander("Detalle de movimientos y folios"):
            st.caption("Consulta el medio de pago, importe, folio y hora de cada movimiento.")
            st.dataframe(
                tabla_vouchers.sort_values("medio", kind="stable"),
                use_container_width=True,
                column_config={
                    "hora": "Hora",
                    "medio": "Medio de pago",
                    "importe": st.column_config.NumberColumn("Importe", format="$ %.2f"),
                    "folio": "Folio / referencia",
                },
            )
        col_total, col_eliminar = st.columns([1, 2])
        with col_total:
            st.metric("Total capturado", f"${tabla_vouchers['importe'].sum():,.2f}")
        with col_eliminar:
            etiquetas = {
                v["id"]: (
                    f"{v['medio']} | ${v['importe']:,.2f} | "
                    f"Folio: {v.get('folio') or 'Sin folio'} | "
                    f"{v['hora']} | Registro {i + 1}"
                )
                for i, v in enumerate(estado["vouchers"])
            }
            if st.session_state.get("movimiento_a_eliminar") not in etiquetas:
                st.session_state["movimiento_a_eliminar"] = None
            movimiento_id = st.selectbox(
                "Movimiento a eliminar",
                options=list(etiquetas),
                format_func=etiquetas.get,
                index=None,
                placeholder="Selecciona el movimiento por tipo de pago, importe y folio",
                key="movimiento_a_eliminar", disabled=cerrada,
            )
            if st.button("Eliminar movimiento", disabled=movimiento_id is None or cerrada):
                estado["vouchers"] = [
                    v for v in estado["vouchers"] if v["id"] != movimiento_id
                ]
                estado.pop("documentos_cierre", None)
                estado.pop("cierre_datos", None)
                if _guardar_cambio(estado, repositorio, "eliminar_movimiento"):
                    st.rerun()
                st.stop()
    else:
        st.info("Todavía no hay movimientos registrados para este día.")

    st.divider()
    st.subheader("2. Comparar con el Corte Z")
    st.session_state.setdefault("texto_corte_z", estado.get("texto_z", ""))
    texto_corte = st.text_area(
        "Pega aquí el Corte Z completo",
        height=220,
        placeholder="Copia el contenido del reporte en el ERP y pégalo aquí.",
        key="texto_corte_z", disabled=cerrada,
    )

    if st.button("Analizar Corte Z", type="primary", disabled=cerrada):
        try:
            corte = interpretar_corte_z(texto_corte)
            estado["ultimo_corte"] = corte
            estado["texto_z"] = texto_corte
            if _guardar_cambio(estado, repositorio, "analizar_z"):
                st.rerun()
            st.stop()
        except ValueError as ex:
            st.error(str(ex))

    corte = estado.get("ultimo_corte")
    if corte:
        fecha_corte = corte.get("fecha")
        if fecha_corte and fecha_corte != fecha_trabajo:
            st.warning(
                f"El Corte Z muestra {fecha_corte.strftime('%d/%m/%Y')} y el arqueo está "
                f"abierto para {fecha_trabajo.strftime('%d/%m/%Y')}. Confirma que elegiste "
                "la fecha correcta antes de guardar."
            )

        capturados_detalle = _totales_capturados(estado["vouchers"])
        capturados = _totales_para_corte_z(estado["vouchers"])
        filas = []
        for medio in MEDIOS_ERP:
            erp = float(corte["medios"].get(medio, 0.0))
            caja = float(capturados.get(medio, 0.0))
            if erp or caja:
                filas.append({
                    "Medio de pago": medio,
                    "Capturado": caja,
                    "Corte Z": erp,
                    "Diferencia": caja - erp,
                })
        comparacion = pd.DataFrame(filas)
        with st.expander("Ver desglose capturado para el formato de cierre"):
            desglose = pd.DataFrame([
                {"Tipo de pago": medio, "Importe": importe}
                for medio, importe in capturados_detalle.items()
                if importe
            ])
            st.dataframe(
                desglose,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Importe": st.column_config.NumberColumn("Importe", format="$ %.2f")
                },
            )
        st.dataframe(
            comparacion,
            use_container_width=True,
            hide_index=True,
            column_config={
                columna: st.column_config.NumberColumn(columna, format="$ %.2f")
                for columna in ["Capturado", "Corte Z", "Diferencia"]
            },
        )
        diferencia_total = float(comparacion["Diferencia"].sum()) if not comparacion.empty else 0.0
        total_cuadra = abs(diferencia_total) < 0.01
        medios_cuadran = all(
            abs(valor) < 0.01 for valor in comparacion.get("Diferencia", [])
        )
        diferencias_clasificacion = [
            {
                "medio": str(fila["Medio de pago"]),
                "capturado": float(fila["Capturado"]),
                "corte_z": float(fila["Corte Z"]),
                "diferencia": float(fila["Diferencia"]),
            }
            for _, fila in comparacion.iterrows()
            if abs(float(fila["Diferencia"])) >= 0.01
        ]
        observacion_automatica = _observacion_diferencias_clasificacion(comparacion)

        aceptar_diferencias = False
        if total_cuadra and medios_cuadran:
            st.success("Caja cuadrada: no hay diferencias por medio de pago.")
        elif total_cuadra:
            st.warning(
                "El total general cuadra, pero hay movimientos clasificados en medios "
                "de pago diferentes. Si los vouchers físicos son correctos, puedes "
                "confirmar la revisión y continuar."
            )
            aceptar_diferencias = st.checkbox(
                "Ya revisé los vouchers físicos y confirmo que son correctos. "
                "Deseo continuar aunque el Corte Z tenga medios de pago cruzados.",
                key="confirmar_diferencias_medios",
                disabled=cerrada,
            )
            if observacion_automatica:
                st.caption("La incidencia queda registrada en el arqueo; las observaciones del formato son manuales.")
                st.info(observacion_automatica)
        else:
            st.error(
                f"La caja tiene una diferencia total de ${diferencia_total:,.2f}. "
                "El total general debe cuadrar antes de continuar."
            )

        confirmar_fecha = True
        if fecha_corte and fecha_corte != fecha_trabajo:
            confirmar_fecha = st.checkbox(
                "Confirmo que este Corte Z pertenece a la fecha de trabajo seleccionada.", disabled=cerrada
            )

        arqueo_aceptable = total_cuadra and (medios_cuadran or aceptar_diferencias)
        if st.button(
            "Guardar este arqueo",
            disabled=not confirmar_fecha or not arqueo_aceptable or cerrada,
        ):
            estado["cortes"].append({
                "hora": datetime.now(ZONA_HORARIA_CAJA).strftime("%H:%M"),
                "corte_z": sum(corte["medios"].values()),
                "capturado": sum(capturados.values()),
                "diferencia": diferencia_total,
                "cuadrado": arqueo_aceptable,
                "cuadrado_estricto": total_cuadra and medios_cuadran,
                "aceptado_con_diferencias": total_cuadra and not medios_cuadran and aceptar_diferencias,
                "diferencias_clasificacion": diferencias_clasificacion,
                "observacion_automatica": observacion_automatica if aceptar_diferencias else "",
                "huella": huella_movimientos(estado["vouchers"]),
                "desglose": corte["medios"],
            })
            if aceptar_diferencias and observacion_automatica:
                estado["observacion_automatica"] = observacion_automatica
            elif medios_cuadran:
                estado.pop("observacion_automatica", None)
            estado["ultimo_corte"] = None
            if _guardar_cambio(estado, repositorio, "guardar_arqueo"):
                st.rerun()
            st.stop()

    if estado["cortes"]:
        st.divider()
        st.subheader("3. Historial del día")
        historial = pd.DataFrame(estado["cortes"])[["hora", "corte_z", "capturado", "diferencia"]]
        st.dataframe(
            historial,
            use_container_width=True,
            hide_index=True,
            column_config={
                "hora": "Hora",
                "corte_z": st.column_config.NumberColumn("Corte Z", format="$ %.2f"),
                "capturado": st.column_config.NumberColumn("Capturado", format="$ %.2f"),
                "diferencia": st.column_config.NumberColumn("Diferencia", format="$ %.2f"),
            },
        )

    st.divider()
    st.subheader("Cierre del día")
    ultimo_arqueo_cuadrado = bool(estado["cortes"]) and estado["cortes"][-1].get("cuadrado", False) and (
        estado["cortes"][-1].get("huella") == huella_movimientos(estado["vouchers"]))
    if not ultimo_arqueo_cuadrado:
        st.info(
            "Guarda primero un arqueo final válido para habilitar la generación de documentos. "
            "Puede ser un arqueo sin diferencias o uno con total general cuadrado y diferencias "
            "de clasificación confirmadas contra vouchers físicos."
        )

    st.session_state.setdefault("texto_corte_x", estado.get("texto_x", ""))
    texto_corte_x = st.text_area(
        "Pega aquí el Corte X completo",
        height=220,
        placeholder="Este reporte se pega una sola vez al finalizar el día.",
        key="texto_corte_x", disabled=cerrada,
    )
    if st.button("Analizar Corte X", disabled=not ultimo_arqueo_cuadrado or cerrada):
        try:
            estado["corte_x"] = interpretar_corte_x(texto_corte_x)
            estado.pop("documentos_cierre", None)
            estado.pop("cierre_datos", None)
            estado["texto_x"] = texto_corte_x
            if _guardar_cambio(estado, repositorio, "analizar_x"):
                st.rerun()
            st.stop()
        except ValueError as ex:
            st.error(str(ex))

    corte_x = estado.get("corte_x")
    if corte_x:
        datos_guardados = estado.get("cierre_datos", {})
        observaciones_guardadas = str(datos_guardados.get("observaciones", "")).strip()
        st.session_state.setdefault("cierre_piezas", datos_guardados.get("piezas", 0))
        st.session_state.setdefault("cierre_tickets", datos_guardados.get("tickets", int(corte_x["tickets_efectivos"])))
        st.session_state.setdefault("cierre_observaciones", observaciones_guardadas)
        st.markdown("##### Información detectada")
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Venta con IVA", f"${corte_x['venta']:,.2f}")
        col2.metric("Venta sin IVA", f"${corte_x['venta_neta']:,.2f}")
        col3.metric("Transacciones", f"{corte_x['transacciones_venta']}")
        col4.metric("Tickets efectivos", f"{corte_x['tickets_efectivos']}")
        st.caption(
            f"Consecutivos: {corte_x['consecutivo_inicial']}-{corte_x['consecutivo_final']} · "
            f"Notas de crédito: {corte_x['notas_credito']}"
        )

        fecha_x_distinta = corte_x["fecha"] != fecha_trabajo
        if fecha_x_distinta:
            st.warning(
                f"El Corte X corresponde a {corte_x['fecha'].strftime('%d/%m/%Y')} y la "
                f"fecha seleccionada es {fecha_trabajo.strftime('%d/%m/%Y')}."
            )

        col_piezas, col_tickets = st.columns(2)
        with col_piezas:
            piezas = st.number_input(
                "Número de piezas vendidas",
                min_value=0,
                step=1,
                key="cierre_piezas",
                disabled=cerrada,
            )
        with col_tickets:
            tickets = st.number_input(
                "Número de tickets para el estadillo",
                min_value=0,
                step=1,
                key="cierre_tickets",
                disabled=cerrada,
                help="Sinapsis descuenta las notas de crédito de las transacciones de venta.",
            )
        observaciones = st.text_area(
            "Observaciones para el formato de corte (opcional)",
            key="cierre_observaciones",
            disabled=cerrada,
            help="Escribe aquí, con tus propias palabras, cualquier aclaración que deba aparecer en el formato.",
        )
        confirmar_fecha_x = True
        if fecha_x_distinta:
            confirmar_fecha_x = st.checkbox(
                "Confirmo que este Corte X pertenece a la fecha de trabajo seleccionada.",
                key="confirmar_fecha_corte_x", disabled=cerrada,
            )

        if st.button(
            "Generar documentos para revisión",
            type="primary",
            disabled=not confirmar_fecha_x or piezas <= 0 or not ultimo_arqueo_cuadrado or cerrada,
        ):
            try:
                usuario_info = st.session_state.get("usuario_info", {})
                responsable = str(
                    usuario_info.get("nombre_completo")
                    or st.session_state.get("usuario_actual", "")
                )
                maestro_drive = _obtener_base_corte_drive()
                maestro_estadillo = cargar_maestro_estadillo(fecha_trabajo) if cargar_maestro_estadillo else None
                if maestro_drive:
                    st.caption(
                        "Corte de caja generado sobre la última versión mensual disponible en Google Drive."
                    )
                estado["documentos_cierre"] = {
                    "corte": generar_formato_corte(
                        fecha_trabajo,
                        corte_x,
                        estado["vouchers"],
                        responsable,
                        observaciones,
                        plantilla_bytes=(maestro_drive or {}).get("contenido"),
                        facturacion=estado.get("cierre_datos", {}).get("facturacion", {}),
                    ),
                    "estadillo": generar_estadillo(
                        fecha_trabajo,
                        corte_x,
                        piezas,
                        tickets,
                        plantilla_bytes=(maestro_estadillo or {}).get("contenido"),
                    ),
                }
                estado["cierre_datos"] = {
                    "piezas": int(piezas), "tickets": int(tickets), "observaciones": observaciones,
                    "responsable": responsable, "fecha_confirmada": confirmar_fecha_x,
                    "facturacion": estado.get("cierre_datos", {}).get("facturacion", {}),
                    "huella": huella_movimientos(estado["vouchers"]),
                }
                if _guardar_cambio(estado, repositorio, "preparar_documentos"):
                    st.rerun()
                st.stop()
            except Exception as ex:
                st.error(f"No pude generar los documentos: {ex}")

    if estado.get("cierre_datos") and not estado.get("documentos_cierre"):
        datos = estado["cierre_datos"]
        try:
            maestro_drive = _obtener_base_corte_drive()
            maestro_estadillo = cargar_maestro_estadillo(fecha_trabajo) if cargar_maestro_estadillo else None
            if cerrada and not maestro_drive:
                raise ValueError("No se encontró el Corte mensual cerrado en Google Drive.")
            estado["documentos_cierre"] = {
                "corte": maestro_drive["contenido"] if cerrada else generar_formato_corte(
                    fecha_trabajo, estado["corte_x"], estado["vouchers"],
                    datos["responsable"], datos["observaciones"],
                    plantilla_bytes=(maestro_drive or {}).get("contenido"),
                    facturacion=datos.get("facturacion", {}),
                ),
                "estadillo": (maestro_estadillo or {}).get("contenido") if cerrada else generar_estadillo(
                    fecha_trabajo, estado["corte_x"], datos["piezas"], datos["tickets"],
                    plantilla_bytes=(maestro_estadillo or {}).get("contenido"),
                ),
            }
            st.session_state.arqueo_caja["documentos_cierre"] = estado["documentos_cierre"]
        except Exception as ex:
            st.error(f"La jornada está guardada, pero no se pudieron cargar sus documentos: {ex}")

    documentos = estado.get("documentos_cierre")
    if documentos:
        datos_drive = estado.get("documentos_drive", {})
        if datos_drive.get("guardado"):
            st.success(
                "Cierre archivado en Google Drive: "
                f"{datos_drive.get('ruta', 'CORTES DE CAJA')}"
            )
        datos_cierre = estado.get("cierre_datos", {})
        try:
            facturacion_documento = leer_facturacion_formato_corte(
                documentos["corte"], fecha_trabajo, estado.get("corte_x")
            )
        except Exception as ex:
            st.error(f"No se pudo leer la facturación existente: {ex}")
            st.stop()

        facturacion_guardada = datos_cierre.get("facturacion") or {}
        if facturacion_guardada and not cerrada:
            try:
                facturacion_preview = normalizar_facturacion(
                    facturacion_guardada, estado["corte_x"]
                )
            except ValueError:
                facturacion_preview = facturacion_documento
        else:
            facturacion_preview = facturacion_documento

        _mostrar_preview_corte(
            fecha_trabajo,
            estado["corte_x"],
            estado["vouchers"],
            datos_cierre,
            facturacion_preview,
        )

        st.divider()
        st.subheader("Facturación del Corte de Caja")
        if cerrada:
            st.caption(
                "El arqueo está cerrado, pero esta sección permanece habilitada para completar "
                "las facturas posteriores. El Estadillo no se modifica."
            )
        else:
            st.caption(
                "Esta captura es opcional durante el cierre nocturno. Si las facturas todavía no "
                "existen, puedes dejarla vacía y completarla al día siguiente."
            )

        editor_base = _dataframe_facturas_clientes(facturacion_preview)
        with st.form(f"form_facturacion_{fecha_trabajo.isoformat()}"):
            st.markdown("##### Facturas de clientes")
            st.caption(
                "Puedes capturar hasta 11 facturas de clientes. "
                "El importe puede ser negativo cuando corresponda a una nota de crédito."
            )
            tabla_facturas = st.data_editor(
                editor_base,
                use_container_width=True,
                hide_index=True,
                num_rows="fixed",
                key=f"editor_facturas_clientes_{fecha_trabajo.isoformat()}",
                column_config={
                    "Ticket": st.column_config.NumberColumn("Ticket", step=1, format="%d"),
                    "Folio factura": st.column_config.TextColumn("Folio factura"),
                    "Importe": st.column_config.NumberColumn("Importe", step=0.01, format="$ %.2f"),
                },
            )
            st.markdown("##### Factura de público general")
            folio_global = st.text_input(
                "Folio de factura global",
                value=_texto_limpio(facturacion_preview.get("folio_global")),
                help="Sinapsis calcula automáticamente los rangos restantes de público general.",
                key=f"folio_global_{fecha_trabajo.isoformat()}",
            )
            aplicar_facturacion = st.form_submit_button(
                "Aplicar facturación y actualizar vista previa",
                type="primary",
            )

        if aplicar_facturacion:
            try:
                captura = _facturacion_desde_editor(tabla_facturas, folio_global)
                normalizada = normalizar_facturacion(captura, estado["corte_x"])
                nuevo_corte = actualizar_facturacion_corte(
                    documentos["corte"], fecha_trabajo, estado["corte_x"], normalizada,
                )
                estado["documentos_cierre"]["corte"] = nuevo_corte
                estado.setdefault("cierre_datos", {})["facturacion"] = normalizada

                if cerrada:
                    estado["facturacion_pendiente_drive"] = True
                    st.session_state.arqueo_caja = estado
                    st.success(
                        "Facturación aplicada a la vista previa. Revisa los datos y, cuando estén correctos, "
                        "guarda el Corte actualizado en Google Drive."
                    )
                    st.rerun()
                else:
                    if _guardar_cambio(estado, repositorio, "actualizar_facturacion"):
                        st.rerun()
                    st.stop()
            except ValueError as ex:
                st.error(str(ex))
            except Exception as ex:
                st.error(f"No pude aplicar la facturación al Corte de Caja: {ex}")

        try:
            propuesta = normalizar_facturacion(
                _facturacion_desde_editor(tabla_facturas, folio_global),
                estado["corte_x"],
            )
            if propuesta.get("rangos_publico"):
                st.caption(
                    "Rangos calculados para público general: "
                    + ", ".join(propuesta["rangos_publico"])
                )
            if propuesta.get("rangos_publico") and not propuesta.get("folio_global"):
                st.info(
                    "La factura global todavía está pendiente. Los rangos se muestran como referencia, "
                    "pero no se escribirán en el Corte hasta capturar el folio global."
                )
        except ValueError:
            pass

        if cerrada:
            if estado.get("facturacion_pendiente_drive"):
                st.warning("La vista previa tiene cambios de facturación pendientes de guardar en Drive.")
            if guardar_corte_drive is None or cargar_maestro_corte is None:
                st.error("Falta conectar la carga y actualización del Corte en Google Drive.")
            elif st.button("Guardar Corte actualizado en Google Drive", type="primary"):
                try:
                    # Take the submitted form values as well, so no edits are silently omitted.
                    captura = normalizar_facturacion(
                        _facturacion_desde_editor(tabla_facturas, folio_global), estado["corte_x"],
                    )
                    aplicada = normalizar_facturacion(facturacion_preview, estado["corte_x"])
                    if captura != aplicada:
                        raise ValueError("Aplica primero la facturación y revisa la vista previa antes de guardarla.")
                    maestro = _obtener_base_corte_drive()
                    if not maestro:
                        raise ValueError("No se encontró el maestro mensual. No se creará otro archivo.")
                    nuevo_corte = actualizar_facturacion_corte(
                        maestro["contenido"], fecha_trabajo, estado["corte_x"], aplicada,
                    )
                    resultado_drive = guardar_corte_drive(fecha_trabajo, nuevo_corte)
                    estado["documentos_cierre"]["corte"] = nuevo_corte
                    estado.setdefault("cierre_datos", {})["facturacion"] = aplicada
                    estado["facturacion_pendiente_drive"] = False
                    estado["documentos_drive"] = {
                        **estado.get("documentos_drive", {}),
                        "ruta": resultado_drive.get("ruta", ""),
                        "nombre_corte": resultado_drive.get("nombre_corte", ""),
                        "guardado": True,
                        "actualizacion_facturacion": True,
                    }
                    st.session_state.arqueo_caja = estado
                    st.success("Corte de Caja actualizado en Google Drive. El Estadillo no fue modificado.")
                except Exception as ex:
                    st.error(f"No se pudo actualizar el Corte de Caja en Google Drive: {ex}")

        st.divider()
        fecha_archivo = fecha_trabajo.strftime("%Y-%m-%d")
        col_descarga1, col_descarga2 = st.columns(2)
        with col_descarga1:
            st.download_button(
                "Descargar Corte de Caja",
                data=documentos["corte"],
                file_name=f"Corte_de_Caja_{fecha_archivo}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                use_container_width=True,
            )
        with col_descarga2:
            st.download_button(
                "Descargar Estadillo",
                data=documentos.get("estadillo") or b"",
                disabled=not documentos.get("estadillo"),
                file_name=f"Estadillo_actualizado_{fecha_archivo}.xlsm",
                mime="application/vnd.ms-excel.sheet.macroEnabled.12",
                use_container_width=True,
            )
        if not cerrada:
            st.warning("Estos archivos son borradores para revisión. Sinapsis todavía no envía correos.")
        if repositorio is not None and not cerrada:
            st.caption("El cierre definitivo guarda esta jornada y bloquea nuevas capturas y eliminaciones.")
            datos = estado.get("cierre_datos", {})
            total_coincide = abs(sum(v["importe"] for v in estado["vouchers"]) - estado.get("corte_x", {}).get("venta", 0)) < 0.01
            listo = ultimo_arqueo_cuadrado and total_coincide and datos.get("fecha_confirmada", False) and (
                datos.get("huella") == huella_movimientos(estado["vouchers"]))
            if not total_coincide:
                st.error("La venta del Corte X no coincide con los movimientos. Corrige la diferencia antes del cierre definitivo.")
            if st.button("Confirmar cierre definitivo", disabled=not listo):
                if guardar_documentos_drive is not None:
                    try:
                        resultado_drive = guardar_documentos_drive(fecha_trabajo, documentos)
                        estado["documentos_drive"] = {
                            "ruta": resultado_drive.get("ruta", ""),
                            "nombre_corte": resultado_drive.get("nombre_corte", ""),
                            "nombre_estadillo": resultado_drive.get("nombre_estadillo", ""),
                            "guardado": True,
                        }
                    except Exception as ex:
                        st.error(
                            "No se pudo guardar el cierre en Google Drive. "
                            f"La jornada no se cerró para evitar perder el archivo mensual. Detalle: {ex}"
                        )
                        st.stop()
                if _guardar_cambio(estado, repositorio, "cerrar", cerrar=True):
                    st.rerun()
                st.stop()
