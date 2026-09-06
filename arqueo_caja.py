import io
import os
import re
import zipfile
from datetime import date, datetime
from xml.etree import ElementTree as ET

import pandas as pd
import streamlit as st


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
        raiz = ET.fromstring(archivos[ruta_xml])
        datos = raiz.find(f"{{{NS_MAIN}}}sheetData")
        celdas = {
            celda.attrib.get("r"): celda
            for fila in datos.findall(f"{{{NS_MAIN}}}row")
            for celda in fila.findall(f"{{{NS_MAIN}}}c")
        }
        for referencia, valor in cambios.items():
            celda = celdas.get(referencia)
            if celda is None:
                numero_fila = int(re.search(r"\d+", referencia).group())
                fila = next(
                    (item for item in datos.findall(f"{{{NS_MAIN}}}row") if int(item.attrib["r"]) == numero_fila),
                    None,
                )
                if fila is None:
                    fila = ET.SubElement(datos, f"{{{NS_MAIN}}}row", {"r": str(numero_fila)})
                celda = ET.SubElement(fila, f"{{{NS_MAIN}}}c", {"r": referencia})
                celdas[referencia] = celda
            for hijo in list(celda):
                if hijo.tag in {f"{{{NS_MAIN}}}f", f"{{{NS_MAIN}}}v", f"{{{NS_MAIN}}}is"}:
                    celda.remove(hijo)
            if isinstance(valor, str):
                celda.set("t", "inlineStr")
                nodo_is = ET.SubElement(celda, f"{{{NS_MAIN}}}is")
                nodo_t = ET.SubElement(nodo_is, f"{{{NS_MAIN}}}t")
                nodo_t.text = valor
            else:
                celda.attrib.pop("t", None)
                nodo_v = ET.SubElement(celda, f"{{{NS_MAIN}}}v")
                nodo_v.text = str(valor)
        archivos[ruta_xml] = ET.tostring(raiz, encoding="utf-8", xml_declaration=True)

    calculo = libro.find(f"{{{NS_MAIN}}}calcPr")
    if calculo is None:
        calculo = ET.SubElement(libro, f"{{{NS_MAIN}}}calcPr")
    calculo.set("fullCalcOnLoad", "1")
    calculo.set("forceFullCalc", "1")
    archivos["xl/workbook.xml"] = ET.tostring(libro, encoding="utf-8", xml_declaration=True)

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
    if "arqueo_caja" not in st.session_state:
        st.session_state.arqueo_caja = {
            "fecha": hoy,
            "vouchers": [],
            "cortes": [],
            "ultimo_corte": None,
        }
    return st.session_state.arqueo_caja


def _reiniciar_si_cambia_fecha(estado, fecha_trabajo):
    fecha_iso = fecha_trabajo.isoformat()
    if estado["fecha"] != fecha_iso:
        st.session_state.arqueo_caja = {
            "fecha": fecha_iso,
            "vouchers": [],
            "cortes": [],
            "ultimo_corte": None,
        }
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


def mostrar_arqueo_caja():
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

    st.subheader("1. Registrar movimiento")
    with st.form("form_nuevo_voucher", clear_on_submit=True):
        col1, col2, col3 = st.columns([2, 1, 2])
        with col1:
            medio = st.selectbox("Medio de pago", MEDIOS_CAPTURA)
        with col2:
            importe = st.number_input("Importe", min_value=0.0, step=0.01, format="%.2f")
        with col3:
            folio = st.text_input("Folio o referencia (opcional)")
        guardar = st.form_submit_button("Agregar movimiento", type="primary")

    if guardar:
        if importe <= 0:
            st.error("Escribe un importe mayor a cero.")
        elif folio.strip() and any(
            item.get("folio", "").strip().lower() == folio.strip().lower()
            for item in estado["vouchers"]
        ):
            st.error("Ese folio ya fue registrado. Revisa el movimiento antes de continuar.")
        else:
            estado["vouchers"].append({
                "hora": datetime.now().strftime("%H:%M"),
                "medio": medio,
                "importe": -float(importe) if medio == "NOTA CREDITO" else float(importe),
                "folio": folio.strip(),
            })
            st.success("Movimiento agregado al arqueo del día.")
            st.rerun()

    if estado["vouchers"]:
        tabla_vouchers = pd.DataFrame(estado["vouchers"])
        tabla_vouchers.index = tabla_vouchers.index + 1
        st.dataframe(
            tabla_vouchers,
            use_container_width=True,
            column_config={
                "hora": "Hora",
                "medio": "Medio de pago",
                "importe": st.column_config.NumberColumn("Importe", format="$ %.2f"),
                "folio": "Folio / referencia",
            },
        )
        col_total, col_eliminar = st.columns([2, 1])
        with col_total:
            st.metric("Total capturado", f"${tabla_vouchers['importe'].sum():,.2f}")
        with col_eliminar:
            numero = st.number_input(
                "Número de movimiento a eliminar",
                min_value=1,
                max_value=len(estado["vouchers"]),
                step=1,
            )
            if st.button("Eliminar movimiento"):
                estado["vouchers"].pop(int(numero) - 1)
                st.rerun()
    else:
        st.info("Todavía no hay movimientos registrados para este día.")

    st.divider()
    st.subheader("2. Comparar con el Corte Z")
    texto_corte = st.text_area(
        "Pega aquí el Corte Z completo",
        height=220,
        placeholder="Copia el contenido del reporte en el ERP y pégalo aquí.",
        key="texto_corte_z",
    )

    if st.button("Analizar Corte Z", type="primary"):
        try:
            corte = interpretar_corte_z(texto_corte)
            estado["ultimo_corte"] = corte
            st.rerun()
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
        if st.button("Guardar este arqueo", disabled=not confirmar_fecha):
            estado["cortes"].append({
                "hora": datetime.now().strftime("%H:%M"),
                "corte_z": sum(corte["medios"].values()),
                "capturado": sum(capturados.values()),
                "diferencia": diferencia_total,
            })
            estado["ultimo_corte"] = None
            st.success("Arqueo guardado. Puedes seguir agregando vouchers durante el día.")
            st.rerun()

    if estado["cortes"]:
        st.divider()
        st.subheader("3. Historial del día")
        historial = pd.DataFrame(estado["cortes"])
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
    ultimo_arqueo_cuadrado = bool(estado["cortes"]) and abs(
        float(estado["cortes"][-1]["diferencia"])
    ) < 0.01
    if not ultimo_arqueo_cuadrado:
        st.info(
            "Guarda primero un arqueo final cuadrado para habilitar la generación de documentos."
        )

    texto_corte_x = st.text_area(
        "Pega aquí el Corte X completo",
        height=220,
        placeholder="Este reporte se pega una sola vez al finalizar el día.",
        key="texto_corte_x",
    )
    if st.button("Analizar Corte X", disabled=not ultimo_arqueo_cuadrado):
        try:
            estado["corte_x"] = interpretar_corte_x(texto_corte_x)
            estado.pop("documentos_cierre", None)
            st.rerun()
        except ValueError as ex:
            st.error(str(ex))

    corte_x = estado.get("corte_x")
    if corte_x:
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
            )
        with col_tickets:
            tickets = st.number_input(
                "Número de tickets para el estadillo",
                min_value=0,
                value=int(corte_x["tickets_efectivos"]),
                step=1,
                key="cierre_tickets",
                help="Sinapsis descuenta las notas de crédito de las transacciones de venta.",
            )
        observaciones = st.text_area(
            "Observaciones para el formato de corte (opcional)",
            key="cierre_observaciones",
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
            disabled=not confirmar_fecha_x or piezas <= 0,
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
                st.success("Documentos generados. Descárgalos y revísalos antes de enviarlos.")
            except Exception as ex:
                st.error(f"No pude generar los documentos: {ex}")

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
