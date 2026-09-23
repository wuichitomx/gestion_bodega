"""Lectura y análisis regional; persistencia inyectada desde la app autenticada."""
from io import BytesIO
from pathlib import Path
import json
import math
import re
import unicodedata

import pandas as pd
from openpyxl import load_workbook

DATA = Path(__file__).resolve().parent / "regional_data"
ADITIVOS = ("# Doc", "Unidades", "Venta Bruta", "Descuento", "Pagos No Venta",
            "Venta", "Impuestos", "ComiTarjeta", "ImpAsumido", "Venta Neta")
METRICAS = ("Venta", "Venta Neta", "Unidades", "# Doc", "UPT", "ATV", "ASP",
            "% Dcto", "Mt2", "Venta x Mt2")


def normalizar(valor):
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    return re.sub(r"[^a-z0-9]", "", texto.encode("ascii", "ignore").decode().lower())


def codigo(valor):
    if valor is None:
        return ""
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        if not math.isfinite(valor):
            return ""
        return str(int(valor)) if valor == int(valor) else str(valor)
    return str(valor).strip().upper()


def numero(valor, opcional=False):
    """Acepta números Excel y formatos 1,234.56 / 1.234,56; no oculta errores."""
    if valor is None or (isinstance(valor, str) and valor.strip().upper() in ("", "N/D", "N/A", "-")):
        if opcional:
            return float("nan")
        raise ValueError("Falta un importe o cantidad obligatorio.")
    if isinstance(valor, bool):
        raise ValueError("Un valor lógico no es un importe.")
    if isinstance(valor, str):
        v = re.sub(r"[\s$]", "", valor)
        negativo = v.startswith("(") and v.endswith(")")
        if negativo:
            v = v[1:-1]
        if "," in v and "." in v:
            decimal = "," if v.rfind(",") > v.rfind(".") else "."
            v = v.replace("." if decimal == "," else ",", "").replace(decimal, ".")
        elif "," in v:
            v = v.replace(",", "") if re.fullmatch(r"[+-]?\d{1,3}(,\d{3})+", v) else v.replace(",", ".")
        valor = ("-" if negativo else "") + v
    try:
        n = float(valor)
    except (ValueError, TypeError) as exc:
        raise ValueError("Valor numérico inválido.") from exc
    if not math.isfinite(n):
        raise ValueError("Valor numérico no finito.")
    return n


def catalogo():
    return pd.DataFrame(json.loads((DATA / "sucursales.json").read_text(encoding="utf-8")))


def cociente(n, d):
    return n / d if pd.notna(d) and d > 0 else float("nan")


def indicadores(fila):
    return {"UPT": cociente(fila["Unidades"], fila["# Doc"]),
            "ATV": cociente(fila["Venta"], fila["# Doc"]),
            "ASP": cociente(fila["Venta"], fila["Unidades"]),
            "% Dcto": cociente(fila["Descuento"], fila["Venta Bruta"]),
            "Venta x Mt2": cociente(fila["Venta"], fila["Mt2"])}


def leer_reporte(origen):
    """Detecta cabecera en cualquier hoja, exige una fila por almacén y controla TOTALES."""
    if isinstance(origen, bytes):
        origen = BytesIO(origen)
    try:
        wb = load_workbook(origen, read_only=True, data_only=True)
    except Exception as exc:
        raise ValueError("No se pudo abrir el archivo XLSX.") from exc
    aliases = {normalizar(n): n for n in (*ADITIVOS, "Mt2")}
    aliases.update({"codigodealmacen": "codigo", "nombredealmacen": "nombre"})
    encontrados = []
    try:
        for ws in wb:
            filas = iter(ws.values)
            for indice, fila in enumerate(filas, 1):
                claves = [aliases.get(normalizar(c)) for c in fila]
                if "codigo" in claves and "Venta" in claves:
                    necesarios = {"codigo", "nombre", *ADITIVOS, "Mt2"}
                    if not necesarios.issubset(claves):
                        raise ValueError("Faltan columnas: " + ", ".join(sorted(necesarios - set(claves))))
                    usadas = [c for c in claves if c]
                    if len(usadas) != len(set(usadas)):
                        raise ValueError("La cabecera contiene columnas duplicadas.")
                    registros, totales = [], []
                    for nf, valores in enumerate(filas, indice + 1):
                        if all(v is None or str(v).strip() == "" for v in valores):
                            continue
                        if "codigodealmacen" in [normalizar(v) for v in valores]:
                            raise ValueError("Hay cabeceras repetidas; exporta un único extracto por almacén.")
                        datos = {k: v for k, v in zip(claves, valores) if k}
                        es_total = any(normalizar(v) == "totales" for v in valores)
                        if not es_total and not codigo(datos.get("codigo")):
                            raise ValueError(f"Fila {nf}: falta Código de Almacén.")
                        try:
                            registro = {k: numero(datos.get(k), opcional=(k == "Mt2")) for k in (*ADITIVOS, "Mt2")}
                        except ValueError as exc:
                            raise ValueError(f"Fila {nf}: {exc}") from exc
                        if not es_total:
                            registro.update(codigo=codigo(datos["codigo"]), nombre=str(datos.get("nombre") or "").strip())
                            if not registro["nombre"]:
                                raise ValueError(f"Fila {nf}: falta Nombre de Almacén.")
                        (totales if es_total else registros).append(registro)
                    encontrados.append((registros, totales, ws.title))
                    break
    finally:
        wb.close()
    if len(encontrados) != 1:
        raise ValueError("Se requiere exactamente una hoja con el extracto por almacén.")
    registros, totales, hoja = encontrados[0]
    return reconstruir_reporte(registros, totales, hoja)


def reconstruir_reporte(registros, totales=None, hoja=""):
    """Única ruta de validación, KPIs y catálogo para Excel y Supabase."""
    totales = totales or []
    if not registros:
        raise ValueError("El reporte no contiene sucursales.")
    if len(totales) > 1:
        raise ValueError("Hay varias filas TOTALES; no se pueden distinguir los bloques.")
    limpios = []
    for fila in registros:
        cod = codigo(fila.get("codigo"))
        nombre = str(fila.get("nombre") or "").strip()
        if not cod or not nombre or normalizar(cod) == "totales" or normalizar(nombre) == "totales":
            raise ValueError("Código o nombre de almacén inválido; TOTALES no es una tienda.")
        limpios.append({"codigo": cod, "nombre": nombre,
                        **{k: numero(None if k == "Mt2" and pd.isna(fila.get(k)) else fila.get(k),
                                     opcional=k == "Mt2") for k in (*ADITIVOS, "Mt2")}})
    totales = [{k: numero(None if k == "Mt2" and pd.isna(t.get(k)) else t.get(k),
                          opcional=k == "Mt2") for k in (*ADITIVOS, "Mt2")} for t in totales]
    df = pd.DataFrame(limpios)
    if df.codigo.duplicated().any():
        raise ValueError("Código de Almacén duplicado; no se suman bloques o periodos automáticamente.")
    for k in ("# Doc", "Unidades"):
        if (df[k] % 1 != 0).any():
            raise ValueError(f"{k} contiene cantidades fraccionarias.")
    control = []
    if totales:
        for k in (*ADITIVOS, "Mt2"):
            calculado = df[k].sum(min_count=1)
            esperado = totales[0][k]
            diferencia = calculado - esperado
            control.append({"Campo": k, "Calculado": calculado, "TOTALES": esperado,
                            "Diferencia": diferencia, "Coincide": bool(abs(diferencia) <= .011)})
    df = pd.concat([df, df.apply(lambda r: pd.Series(indicadores(r)), axis=1)], axis=1)
    df["Mt2 origen"] = df.Mt2
    df.loc[df.Mt2 <= 0, "Mt2"] = float("nan")
    cat = catalogo().drop(columns="nombre")
    df = df.merge(cat, on="codigo", how="left", validate="one_to_one")
    df["estado"] = df.estado.fillna("Ubicación pendiente")
    df["ciudad"] = df.ciudad.fillna("Por confirmar")
    avisos = []
    if not totales:
        avisos.append("No hay fila TOTALES: la conciliación no está disponible.")
    elif not all(r["Coincide"] for r in control):
        avisos.append("Hay diferencias contra TOTALES. Revisa la conciliación antes de usar los resultados.")
    faltan = set(cat.codigo) - set(df.codigo)
    nuevos = set(df.codigo) - set(cat.codigo)
    if faltan:
        avisos.append("Sin registro en este archivo: " + ", ".join(sorted(faltan)))
    if nuevos:
        avisos.append("Códigos fuera del catálogo, sin ubicación: " + ", ".join(sorted(nuevos)))
    return df, pd.DataFrame(control), avisos, hoja


def referencia_regional(df):
    """Montos/cantidades: promedio por tienda. Ratios: cociente de sumas elegibles."""
    total = {k: df[k].sum() for k in ADITIVOS}
    validos = df[df.Mt2 > 0]
    referencia = {k: df[k].mean() for k in ("Venta", "Venta Neta", "Unidades", "# Doc", "Mt2")}
    total["Mt2"] = validos.Mt2.sum()
    referencia.update(indicadores(total))
    referencia["Venta x Mt2"] = cociente(validos.Venta.sum(), validos.Mt2.sum())
    return referencia


def filtrar_estado(df, estado):
    return df if estado == "Toda la región" else df[df.estado == estado]


def estado_seleccionado(evento, features):
    seleccion = evento.get("selection", {}).get("estado", [])
    if isinstance(seleccion, dict):
        seleccion = [seleccion]
    if seleccion:
        clave = seleccion[0].get("cve_ent")
        if isinstance(clave, list):
            clave = clave[-1] if clave else None
        return next((f["properties"]["estado"] for f in features
                     if f["properties"]["cve_ent"] == clave), None)
    return None


def datos_mapa(df):
    geo = json.loads((DATA / "mexico_estados.geojson").read_text(encoding="utf-8"))
    for feature in geo["features"]:
        p = feature["properties"]
        filas = df[df.cve_ent == p["cve_ent"]]
        p["sucursales"] = len(filas)
        # Vega descarta geometrías con color numérico nulo, aun dentro de una
        # condición. El cero es solo auxiliar; el tooltip distingue la ausencia.
        p["venta"] = float(filas.Venta.sum()) if len(filas) else 0.0
        p["venta_texto"] = f"{p['venta']:,.2f}" if len(filas) else "Sin tiendas ubicadas en este archivo"
        # Campos planos facilitan la selección que devuelve Streamlit.
        feature.update(cve_ent=p["cve_ent"], estado=p["estado"],
                       sucursales=p["sucursales"], venta=p["venta"], venta_texto=p["venta_texto"])
    return geo["features"]


def formato(v, porcentaje=False):
    return "N/D" if pd.isna(v) else f"{v:.2%}" if porcentaje else f"{v:,.2f}"


def grafica_kpi(visibles, metrica, referencia):
    """Barras del filtro actual y referencia ya calculada sobre toda la región."""
    import altair as alt

    comparables = visibles[visibles.Mt2 > 0] if metrica == "Venta x Mt2" else visibles
    datos = comparables.sort_values([metrica, "codigo"], ascending=[False, True], na_position="last").copy()
    datos["tienda"] = datos["codigo"] + " · " + datos["nombre"]
    datos["valor"] = datos[metrica]
    datos["etiqueta"] = datos.valor.map(lambda v: formato(v, metrica == "% Dcto"))
    # La posición de N/D solo sirve para su texto: nunca dibujar una barra cero.
    datos["posicion_texto"] = datos.valor.fillna(0)
    valores = datos.valor.dropna().tolist() + ([referencia] if pd.notna(referencia) else []) + [0]
    minimo, maximo = min(valores), max(valores)
    margen = (maximo - minimo) * .24 or 1
    escala = alt.Scale(domain=[minimo - (margen if minimo < 0 else 0), maximo + margen])
    base = alt.Chart(datos).encode(
        y=alt.Y("tienda:N", sort=datos.tienda.tolist(), title=None,
                axis=alt.Axis(labelLimit=350, labelFontSize=12)),
        tooltip=[alt.Tooltip("codigo:N", title="Código de Almacén"),
                 alt.Tooltip("nombre:N", title="Sucursal"),
                 alt.Tooltip("etiqueta:N", title=metrica)])
    barras = base.transform_filter("isValid(datum.valor)").mark_bar(cornerRadiusEnd=3).encode(
        x=alt.X("valor:Q", title=metrica, scale=escala,
                axis=alt.Axis(format=".0%" if metrica == "% Dcto" else ",.2f")),
        color=alt.value("#00b8c9"))
    etiquetas = base.mark_text(align="left", dx=5, color="#00b8c9", fontWeight="bold").encode(
        x=alt.X("posicion_texto:Q", scale=escala), text="etiqueta:N")
    grafica = barras + etiquetas
    if pd.notna(referencia):
        linea = alt.Chart(pd.DataFrame({"referencia": [referencia]})).mark_rule(
            color="#e6ae00", strokeDash=[6, 4], strokeWidth=2).encode(
                x=alt.X("referencia:Q", scale=escala),
                tooltip=[alt.Tooltip("referencia:Q", title="Benchmark regional completo",
                                     format=".2%" if metrica == "% Dcto" else ",.2f")])
        grafica = grafica + linea
    return grafica.properties(height=max(150, len(datos) * 27))


def mostrar_dashboard(visibles, region):
    import streamlit as st

    referencia = referencia_regional(region)
    st.subheader("Dashboard regional de KPIs")
    st.caption(f"Comparando {len(visibles)} de {len(region)} sucursales. Línea amarilla: benchmark regional "
               "completo, ponderado según el indicador; incluye todas las tiendas del archivo "
               "y no cambia con el filtro por estado.")
    una_columna = st.toggle("Ampliar gráficas a una por fila", key="regional_graficas_amplias",
                             help="Activa esta opción si los nombres no caben en dos columnas.")
    principales = (("UPT", "UPT · Items x Doc."), ("ATV", "ATV · Venta x Documento"),
                   ("ASP", "ASP · Precio x Unidad"), ("Venta x Mt2", "Rendimiento por m² · Venta x Mt2"))
    formulas = {"UPT": "Σ Unidades / Σ # Doc.", "ATV": "Σ Venta / Σ # Doc.",
                "ASP": "Σ Venta / Σ Unidades"}
    for inicio in (0, 2):
        contenedores = [st.container(), st.container()] if una_columna else st.columns(2)
        for columna, (metrica, titulo) in zip(contenedores, principales[inicio:inicio + 2]):
            with columna:
                st.metric(titulo + " · Benchmark regional completo", formato(referencia[metrica]))
                if metrica == "Venta x Mt2" and not (visibles.Mt2 > 0).any():
                    st.info("Sin tiendas con Mt2 > 0 en este filtro. Rendimiento: N/D.")
                else:
                    st.altair_chart(grafica_kpi(visibles, metrica, referencia[metrica]), width="stretch")
                if metrica == "Venta x Mt2":
                    st.caption(f"Σ Venta / Σ Mt2, solo tiendas con Mt2 > 0: "
                               f"{int((region.Mt2 > 0).sum())}/{len(region)} comparables en toda la región. "
                               f"Excluidas del gráfico por área inválida en este filtro: {int((~(visibles.Mt2 > 0)).sum())}. "
                               "Se conservan como N/D en las tablas.")
                else:
                    st.caption(formulas[metrica] + " · Toda la región, sin aplicar el filtro estatal.")
                    if visibles[metrica].isna().any():
                        st.caption("N/D: denominador no positivo; no comparable.")
    with st.expander("Indicadores secundarios: Venta, Venta Neta y % Dcto"):
        metrica = st.selectbox("Indicador secundario", ("Venta", "Venta Neta", "% Dcto"), key="regional_secundario")
        st.caption("Promedio regional completo: " + formato(referencia[metrica], metrica == "% Dcto") +
                   (" · Descuento / Venta Bruta; un porcentaje mayor no implica mejor desempeño."
                    if metrica == "% Dcto" else " · Suma regional / número de sucursales presentes."))
        st.altair_chart(grafica_kpi(visibles, metrica, referencia[metrica]), width="stretch")


def mostrar_vista_regional(repositorio=None):
    import streamlit as st
    import altair as alt
    from regional_persistencia import ErrorRegional

    st.header("🌎 Vista Regional")
    if repositorio is None:
        st.info("Abre Vista Regional desde Sinapsis para consultar o actualizar el reporte compartido.")
        return
    try:
        vigente = repositorio.cargar()
    except ErrorRegional as exc:
        st.error(str(exc))
        return
    if vigente:
        st.caption(f"Fecha del reporte: {vigente['fecha']:%d/%m/%Y}")
    else:
        st.info("No hay reporte regional vigente. Carga el primer reporte para consultar los resultados.")
    # El widget nativo muestra el nombre seleccionado; ocultarlo sólo en esta carga.
    st.markdown('''<style>
        .st-key-regional_carga [data-testid="stFileUploaderFile"],
        .st-key-regional_carga [data-testid="stFileChip"] {display:none}
        .st-key-regional_carga [data-testid="stFileChips"]::before {
            content:"Selección lista. Guarda para validar el reporte.";
        }
        </style>''', unsafe_allow_html=True)
    with st.container(key="regional_carga"):
        with st.expander("Actualizar reporte regional", expanded=vigente is None):
            version = st.session_state.get("regional_carga_version", 0)
            with st.form(f"regional_form_{version}"):
                fecha = st.date_input("Fecha del reporte", value=None, format="DD/MM/YYYY")
                archivo = st.file_uploader("Seleccionar Excel regional", type=["xlsx"], key=f"regional_archivo_{version}")
                enviar = st.form_submit_button("Guardar reporte regional")
            if enviar:
                if fecha is None or archivo is None:
                    st.error("Selecciona la Fecha del reporte y el Excel regional.")
                else:
                    try:
                        nuevo, conciliacion, _, _ = leer_reporte(archivo.getvalue())
                        repositorio.guardar(fecha, nuevo, conciliacion)
                    except (ValueError, ErrorRegional) as exc:
                        st.error(str(exc))
                    else:
                        st.session_state["regional_carga_version"] = version + 1
                        for clave in ("regional_estado", "regional_click", "regional_tienda"):
                            st.session_state.pop(clave, None)
                        st.rerun()
    if vigente is None:
        st.dataframe(catalogo().fillna("Por confirmar"), hide_index=True, width="stretch")
        return
    df, control, avisos = vigente["df"], vigente["control"], vigente["avisos"]
    for aviso in avisos:
        st.warning(aviso)
    cols = st.columns(4)
    for col, etiqueta, valor in zip(cols, ("Venta regional", "Venta neta regional", "Unidades", "Documentos"),
                                   (df.Venta.sum(), df["Venta Neta"].sum(), df.Unidades.sum(), df["# Doc"].sum())):
        col.metric(etiqueta, formato(valor))
    referencia = referencia_regional(df)
    st.caption(f"Venta por m² regional: {formato(referencia['Venta x Mt2'])} · "
               f"Cobertura: {int((df.Mt2 > 0).sum())}/{len(df)} tiendas con área válida. "
               "Se excluyen la venta y los metros de tiendas sin área válida.")
    with st.expander("Bases de cálculo y conciliación"):
        st.markdown("UPT = Unidades / # Doc. ATV = Venta / # Doc. ASP = Venta / Unidades. "
                    "% Dcto = Descuento / Venta Bruta. Venta x Mt2 = Venta / Mt2. "
                    "Venta Neta se toma del ERP; no se reconstruye con ImpAsumido. "
                    "Denominadores no positivos → N/D. Referencia regional: montos y cantidades "
                    "son promedios por tienda; ratios se calculan sobre sumas, ponderados por su denominador. "
                    "Mt2 compara el promedio de áreas válidas. Se incluye la propia tienda y toda la región, "
                    "aunque el mapa esté filtrado. TOTALES solo controla la suma, nunca se agrega como tienda.")
        st.dataframe(control.style.format({k: "{:,.2f}" for k in ("Calculado", "TOTALES", "Diferencia")}, na_rep="N/D"), hide_index=True, width="stretch")
    st.subheader("México por estado")
    st.caption("Selecciona un estado en el mapa o en la lista. Gris: sin tiendas ubicadas en este archivo. "
               "Las ubicaciones deducidas de ciudades explícitas requieren validación del catálogo comercial.")
    try:
        features = datos_mapa(df)
        selection = alt.selection_point(name="estado", fields=["cve_ent"], on="click", clear="dblclick", toggle=False)
        chart = (alt.Chart(alt.Data(values=features)).mark_geoshape(stroke="white", strokeWidth=.6)
                 .encode(color=alt.condition("datum.sucursales > 0", alt.Color("venta:Q", title="Venta", scale=alt.Scale(scheme="tealblues")), alt.value("#e2e8f0")),
                         tooltip=[alt.Tooltip("estado:N", title="Estado"), alt.Tooltip("sucursales:Q", title="Tiendas"), alt.Tooltip("venta_texto:N", title="Venta")],
                         opacity=alt.condition(selection, alt.value(1), alt.value(.55)))
                 .project(type="mercator").properties(height=400).add_params(selection))
        # Mantener GeoJSON dentro de la capa evita que Streamlit lo convierta a Arrow,
        # conversión que pierde la estructura anidada de la geometría.
        capa = chart.to_dict()
        parametros = capa.pop("params")
        capa.pop("$schema", None)
        configuracion = capa.pop("config", {})
        especificacion = {"layer": [capa], "params": parametros, "config": configuracion}
        evento = st.vega_lite_chart(spec=especificacion, width="stretch", on_select="rerun", key="regional_mapa")
        estado_mapa = estado_seleccionado(evento, features)
        anterior = st.session_state.get("regional_click")
        if estado_mapa and estado_mapa != anterior:
            st.session_state["regional_estado"] = estado_mapa
        st.session_state["regional_click"] = estado_mapa
        estados = sorted({f["properties"]["estado"] for f in features})
    except (OSError, ValueError, KeyError):
        st.warning("No se pudo cargar el mapa local. Puedes navegar por la lista de estados.")
        estados = sorted(set(df.estado) - {"Ubicación pendiente"})
    st.caption("Límites: INEGI, Marco Geoestadístico, simplificados para visualización; no representan direcciones de tiendas.")
    opciones = ["Toda la región", *estados, "Ubicación pendiente"]
    if st.session_state.get("regional_estado") not in opciones:
        st.session_state["regional_estado"] = opciones[0]
    estado = st.selectbox("Estado", opciones, key="regional_estado")
    visibles = filtrar_estado(df, estado)
    pendientes = df[df.cve_ent.isna() | df.estado.eq("Ubicación pendiente") | df.ciudad.eq("Por confirmar")]
    if len(pendientes):
        st.warning("Ubicación pendiente: " + "; ".join(pendientes.codigo + " · " + pendientes.nombre) +
                   ". Se incluyen en los resultados regionales. Confirmar ciudad y estado por Código de Almacén.")
    if visibles.empty:
        st.info("Este estado no tiene sucursales ubicadas en el archivo cargado.")
        return
    mostrar_dashboard(visibles, df)
    st.subheader("Ranking de sucursales")
    metrica = st.selectbox("Ordenar de mayor a menor", METRICAS, key="regional_orden")
    ranking = visibles.sort_values([metrica, "codigo"], ascending=[False, True], na_position="last")
    tabla = ranking[["codigo", "nombre", "estado", *METRICAS]].copy()
    formatos = {k: "{:.2%}" if k == "% Dcto" else "{:,.2f}" for k in METRICAS}
    st.dataframe(tabla.style.format(formatos, na_rep="N/D"), hide_index=True, width="stretch")
    st.caption("Orden descendente; un descuento mayor no implica mejor rendimiento. N/D queda al final.")
    tienda = st.selectbox("Detalle por sucursal", ranking.codigo.tolist(),
                          format_func=lambda c: f"{c} · {df.set_index('codigo').loc[c, 'nombre']}", key="regional_tienda")
    fila = df.set_index("codigo").loc[tienda]
    st.subheader(fila["nombre"])
    st.caption(f"{fila['ciudad']} · {fila['estado']}")
    comparacion = []
    for k in METRICAS:
        valor, ref = fila[k], referencia[k]
        diferencia = valor - ref
        comparacion.append({"Indicador": k, "Sucursal": formato(valor, k == "% Dcto"),
                            "Referencia regional": formato(ref, k == "% Dcto"),
                            "Diferencia": (formato(diferencia * 100) + " pp") if k == "% Dcto" and pd.notna(diferencia) else formato(diferencia)})
    st.dataframe(pd.DataFrame(comparacion), hide_index=True, width="stretch")


if __name__ == "__main__":
    import streamlit as st
    st.set_page_config(page_title="Sinapsis · Vista Regional", layout="wide")
    mostrar_vista_regional()
