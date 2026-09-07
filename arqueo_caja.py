import io
import os
import re
import zipfile
import copy
from uuid import uuid4
from datetime import date, datetime
from xml.etree import ElementTree as ET
from xml.dom import minidom

import pandas as pd
import streamlit as st
from cajas_persistencia import ErrorPersistenciaCaja, huella_movimientos


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


def _editar_ooxml(ruta, cambios_por_hoja):
    """Cambia celdas puntuales sin reconstruir el libro ni sus macros."""
    with zipfile.ZipFile(ruta, "r") as origen:
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

    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as destino:
        for nombre, contenido in archivos.items():
            destino.writestr(nombre, contenido)
    return salida.getvalue()


def generar_formato_corte(fecha_trabajo, corte_x, vouchers, responsable, observaciones=""):
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
    cambios.update({"C53": rango_tickets, "C72": rango_tickets, "B78": observaciones.strip(), "D82": responsable.strip()})
    return _editar_ooxml(ruta, {hoja_nombre: cambios})


MESES_ESTADILLO = {
    1: "ENERO", 2: "FEBRERO", 3: "MARZO", 4: "ABRIL", 5: "MAYO", 6: "JUNIO",
    7: "JULIO", 8: "AGOSTO", 9: "SEPTIEMBRE", 10: "OCTUBRE", 11: "NOVIEMBRE", 12: "DICIEMBRE ",
}


def generar_estadillo(fecha_trabajo, corte_x, piezas, tickets):
    ruta = _ruta_plantilla("estadillo_2026.xlsm")
    if not os.path.exists(ruta):
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
    hoy = date.today().isoformat()
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


def _limpiar_campos_caja():
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


def mostrar_arqueo_caja(repositorio=None):
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
    if repositorio is not None:
        if st.button("Recargar datos guardados"):
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
            medio = st.selectbox("Medio de pago", MEDIOS_CAPTURA)
        with col2:
            importe = st.number_input(
                "Importe", min_value=0.0, value=None, step=0.01,
                format="%.2f", placeholder="Escribe el importe", key="importe_voucher",
            )
        with col3:
            folio = st.text_input(
                "Folio o referencia (opcional)",
                help="Puedes repetir una referencia con otro importe o medio de pago. "
                     "Si usas una tarjeta como referencia, captura sólo sus últimos cuatro dígitos.",
            )
        guardar = st.form_submit_button("Agregar movimiento", type="primary", disabled=cerrada)

    if guardar:
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
                "hora": datetime.now().strftime("%H:%M"),
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
                key="movimiento_a_eliminar",
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
        key="texto_corte_z",
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
        if abs(diferencia_total) < 0.01 and all(
            abs(valor) < 0.01 for valor in comparacion.get("Diferencia", [])
        ):
            st.success("Caja cuadrada: no hay diferencias por medio de pago.")
        elif abs(diferencia_total) < 0.01:
            st.warning(
                "El total general cuadra, pero hay movimientos clasificados en medios "
                "de pago diferentes. Revisa las filas con diferencia."
            )
        else:
            st.error(f"La caja tiene una diferencia total de ${diferencia_total:,.2f}.")

        confirmar_fecha = True
        if fecha_corte and fecha_corte != fecha_trabajo:
            confirmar_fecha = st.checkbox(
                "Confirmo que este Corte Z pertenece a la fecha de trabajo seleccionada."
            )
        if st.button("Guardar este arqueo", disabled=not confirmar_fecha or cerrada):
            estado["cortes"].append({
                "hora": datetime.now().strftime("%H:%M"),
                "corte_z": sum(corte["medios"].values()),
                "capturado": sum(capturados.values()),
                "diferencia": diferencia_total,
                "cuadrado": abs(diferencia_total) < 0.01 and all(abs(v) < 0.01 for v in comparacion.get("Diferencia", [])),
                "huella": huella_movimientos(estado["vouchers"]),
                "desglose": corte["medios"],
            })
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
            "Guarda primero un arqueo final cuadrado para habilitar la generación de documentos."
        )

    st.session_state.setdefault("texto_corte_x", estado.get("texto_x", ""))
    texto_corte_x = st.text_area(
        "Pega aquí el Corte X completo",
        height=220,
        placeholder="Este reporte se pega una sola vez al finalizar el día.",
        key="texto_corte_x",
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
        st.session_state.setdefault("cierre_piezas", datos_guardados.get("piezas", 0))
        st.session_state.setdefault("cierre_tickets", datos_guardados.get("tickets", int(corte_x["tickets_efectivos"])))
        st.session_state.setdefault("cierre_observaciones", datos_guardados.get("observaciones", ""))
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
        )
        confirmar_fecha_x = True
        if fecha_x_distinta:
            confirmar_fecha_x = st.checkbox(
                "Confirmo que este Corte X pertenece a la fecha de trabajo seleccionada.",
                key="confirmar_fecha_corte_x",
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
                estado["documentos_cierre"] = {
                    "corte": generar_formato_corte(
                        fecha_trabajo,
                        corte_x,
                        estado["vouchers"],
                        responsable,
                        observaciones,
                    ),
                    "estadillo": generar_estadillo(
                        fecha_trabajo,
                        corte_x,
                        piezas,
                        tickets,
                    ),
                }
                estado["cierre_datos"] = {
                    "piezas": int(piezas), "tickets": int(tickets), "observaciones": observaciones,
                    "responsable": responsable, "fecha_confirmada": confirmar_fecha_x,
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
            estado["documentos_cierre"] = {
                "corte": generar_formato_corte(fecha_trabajo, estado["corte_x"], estado["vouchers"], datos["responsable"], datos["observaciones"]),
                "estadillo": generar_estadillo(fecha_trabajo, estado["corte_x"], datos["piezas"], datos["tickets"]),
            }
            st.session_state.arqueo_caja["documentos_cierre"] = estado["documentos_cierre"]
        except Exception:
            st.error("La jornada está guardada, pero no se pudieron reconstruir sus documentos. Revisa las plantillas privadas.")

    documentos = estado.get("documentos_cierre")
    if documentos:
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
                data=documentos["estadillo"],
                file_name=f"Estadillo_actualizado_{fecha_archivo}.xlsm",
                mime="application/vnd.ms-excel.sheet.macroEnabled.12",
                use_container_width=True,
            )
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
                if _guardar_cambio(estado, repositorio, "cerrar", cerrar=True):
                    st.rerun()
                st.stop()
