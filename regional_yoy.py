"""Comparación de reportes regionales en sesión, sin acceso a persistencia."""
import math

import pandas as pd

from vista_regional import ADITIVOS, indicadores, leer_reporte

KPIS = ("Venta", "Venta Neta", "Unidades", "# Doc", "UPT", "ATV", "ASP", "% Dcto")
MIN_DOCUMENTOS = 30


def resumen(df):
    if df.empty:
        return {k: float("nan") for k in KPIS}
    totales = {k: df[k].sum() for k in ADITIVOS}
    totales["Mt2"] = float("nan")
    totales.update(indicadores(totales))
    return {k: totales[k] for k in KPIS}


def variacion(actual, anterior, documentos):
    if documentos < MIN_DOCUMENTOS or not all(pd.notna(v) and math.isfinite(v) for v in (actual, anterior)) or anterior <= 0:
        return float("nan")
    return (actual - anterior) / anterior


def comparar(actual, anterior, solo_comparables=False):
    """Unión exacta por código; no interpreta nombres ni empareja aperturas."""
    comunes = set(actual.codigo) & set(anterior.loc[anterior["# Doc"].ge(MIN_DOCUMENTOS), "codigo"])
    a = actual[actual.codigo.isin(comunes)] if solo_comparables else actual
    b = anterior[anterior.codigo.isin(comunes)] if solo_comparables else anterior
    ra, rb = resumen(a), resumen(b)
    regional = pd.DataFrame([
        {"KPI": k, "Actual": ra[k], "Anterior": rb[k],
         "Diferencia": ra[k] - rb[k],
         "YoY %": variacion(ra[k], rb[k], b["# Doc"].sum())}
        for k in KPIS])
    columnas = ["codigo", "nombre", *KPIS]
    tiendas = a[columnas].merge(b[columnas], on="codigo", how="outer",
                               suffixes=(" actual", " anterior"), indicator=True,
                               validate="one_to_one")
    tiendas["Tienda"] = tiendas["nombre actual"].combine_first(tiendas["nombre anterior"])
    tiendas["Estado"] = tiendas["_merge"].map({
        "both": "Comparable", "left_only": "Nueva / sin base comparable",
        "right_only": "Sin dato actual"}).astype(str)
    ambas = tiendas["_merge"].eq("both")
    tiendas.loc[ambas & tiendas["# Doc anterior"].lt(MIN_DOCUMENTOS), "Estado"] = "Base insuficiente"
    for k in KPIS:
        tiendas[k + " diferencia"] = tiendas[k + " actual"] - tiendas[k + " anterior"]
        tiendas[k + " YoY %"] = [
            variacion(x, y, docs) if presente else float("nan")
            for x, y, docs, presente in zip(tiendas[k + " actual"], tiendas[k + " anterior"],
                                           tiendas["# Doc anterior"], ambas)]
    return regional, tiendas.drop(columns="_merge")


def ranking(tiendas, kpi):
    datos = tiendas[["codigo", "Tienda", "Estado", kpi + " actual", kpi + " anterior",
                     kpi + " diferencia", kpi + " YoY %"]].copy()
    datos.columns = ["Código", "Tienda", "Estado", "Actual", "Anterior", "Diferencia", "YoY %"]
    insuficiente = datos.Estado.eq("Comparable") & (datos.Anterior.le(0) | datos.Anterior.isna())
    datos.loc[insuficiente, "Estado"] = "Base insuficiente"
    datos.loc[datos.Estado.eq("Comparable") & datos.Actual.isna(), "Estado"] = "KPI actual no disponible"
    # Para descuento se muestra cambio en puntos porcentuales, con la misma regla de base.
    datos["Cambio"] = datos["Diferencia"] * 100 if kpi == "% Dcto" else datos["YoY %"] * 100
    datos.loc[datos.Estado.ne("Comparable"), "Cambio"] = float("nan")
    return datos.sort_values(["Cambio", "Código"], ascending=[False, True], na_position="last")


def grafica_ranking(datos, kpi):
    import altair as alt

    datos = datos[datos.Cambio.notna()].copy()
    datos["Sucursal"] = datos["Código"] + " · " + datos.Tienda
    unidad = "pp" if kpi == "% Dcto" else "%"
    datos["Etiqueta"] = datos.Cambio.map(lambda v: f"{v:+.1f} {unidad}")
    extremos = [0, *datos.Cambio.tolist()]
    margen = (max(extremos) - min(extremos)) * .2 or 1
    escala = alt.Scale(domain=[min(extremos) - margen, max(extremos) + margen])
    base = alt.Chart(datos).encode(
        y=alt.Y("Sucursal:N", sort=datos.Sucursal.tolist(), title=None,
                axis=alt.Axis(labelLimit=360, labelFontSize=12)),
        x=alt.X("Cambio:Q", title=f"{kpi} · cambio en {unidad}", scale=escala),
        tooltip=["Código:N", "Tienda:N", alt.Tooltip("Actual:Q", format=".2%" if kpi == "% Dcto" else ",.2f"),
                 alt.Tooltip("Anterior:Q", format=".2%" if kpi == "% Dcto" else ",.2f"), "Etiqueta:N"])
    color = alt.value("#00b8c9") if kpi == "% Dcto" else alt.condition(
        "datum.Cambio >= 0", alt.value("#00b8c9"), alt.value("#e07860"))
    barras = base.mark_bar(cornerRadiusEnd=3).encode(color=color)
    positivas = base.transform_filter("datum.Cambio >= 0").mark_text(
        align="left", dx=5, color="#00b8c9").encode(text="Etiqueta:N")
    negativas = base.transform_filter("datum.Cambio < 0").mark_text(
        align="right", dx=-5, color="#e07860" if kpi != "% Dcto" else "#00b8c9").encode(text="Etiqueta:N")
    cero = alt.Chart(pd.DataFrame({"cero": [0]})).mark_rule(color="#94a3b8").encode(x="cero:Q")
    return (barras + positivas + negativas + cero).properties(height=max(180, len(datos) * 32))


def texto(valor, kpi):
    if pd.isna(valor):
        return "N/D"
    if kpi == "% Dcto":
        return f"{valor:.2%}"
    if kpi in ("Unidades", "# Doc"):
        return f"{valor:,.0f}"
    return ("$" if kpi in ("Venta", "Venta Neta", "ATV", "ASP") else "") + f"{valor:,.2f}"


def mostrar_comparativo(actual, anterior):
    import streamlit as st

    comunes = set(actual.codigo) & set(anterior.codigo)
    _, todas = comparar(actual, anterior)
    st.caption(f"Tiendas: actuales {len(actual)} · anteriores {len(anterior)} · códigos comunes {len(comunes)}")
    lectura = st.radio("Lectura", ["Región total", "Tiendas comparables"], horizontal=True, key="yoy_lectura")
    comparables = lectura == "Tiendas comparables"
    st.caption(f"Solo códigos presentes en ambos reportes con al menos {MIN_DOCUMENTOS} documentos anteriores. "
               "Coincidencia por código no garantiza operación durante todo el periodo."
               if comparables else "Totales: todos los almacenes de cada año. Tabla: todas las tiendas actuales, incluyendo nuevas y con base insuficiente.")
    if comparables and not comunes:
        st.info("No hay códigos comunes entre los reportes.")
        return
    regional, tiendas = comparar(actual, anterior, comparables)
    ausentes = todas[todas.Estado.eq("Sin dato actual")]
    tiendas = tiendas[tiendas.codigo.isin(actual.codigo)]
    for inicio in (0, 4):
        for col, (_, fila) in zip(st.columns(4), regional.iloc[inicio:inicio + 4].iterrows()):
            kpi = fila.KPI
            delta = (f"{fila.Diferencia * 100:+.2f} pp" if kpi == "% Dcto" and pd.notna(fila["YoY %"])
                     else f"{fila['YoY %']:+.2%}" if pd.notna(fila["YoY %"]) else None)
            col.metric(kpi + " · Actual", texto(fila.Actual, kpi), delta,
                       delta_color="off" if kpi == "% Dcto" else "normal")
            col.caption("Anterior: " + texto(fila.Anterior, kpi))
            col.caption("Diferencia: " + (f"{fila.Diferencia * 100:+.2f} pp" if kpi == "% Dcto"
                                         else texto(fila.Diferencia, kpi)))
            if pd.isna(fila["YoY %"]):
                col.caption("YoY: N/D · base insuficiente o KPI no disponible")
    st.subheader("Comparativo por sucursal")
    kpi = st.selectbox("KPI del ranking YoY", KPIS, key="yoy_kpi")
    datos = ranking(tiendas, kpi)
    st.caption(f"{lectura}: {len(datos)} tiendas en la tabla. "
               f"Gráfica: {datos.Cambio.notna().sum()} tiendas con YoY calculable de {len(actual)} actuales ({kpi}).")
    st.caption(f"Base insuficiente: menos de {MIN_DOCUMENTOS} documentos en el año anterior o KPI base ≤ 0. "
               "Región total conserva sus importes; Tiendas comparables excluye las bases con menos de 30 documentos. "
               "La gráfica solo incluye cambios calculables para el KPI seleccionado. "
               "N/D indica que no se puede calcular una comparación.")
    if kpi == "% Dcto":
        st.caption("El descuento se compara en puntos porcentuales; un aumento no implica mejor desempeño.")
    if datos.Cambio.notna().any():
        st.altair_chart(grafica_ranking(datos, kpi), width="stretch")
    else:
        st.info("No hay tiendas con base suficiente para este KPI.")
    excluidas = datos[datos.Cambio.isna()]
    if not excluidas.empty:
        st.caption("Fuera del ranking: " + "; ".join(
            f"{r['Código']} · {r['Tienda']}: {r['Estado']}" for _, r in excluidas.iterrows()))
    tabla = tiendas.set_index("codigo").loc[datos["Código"]].reset_index()
    tabla["Estado del KPI"] = datos.Estado.to_numpy()
    columnas = ["codigo", "Tienda", "Estado del KPI", "Venta actual", "Venta anterior",
                "Venta diferencia", "Venta YoY %"]
    for metrica in KPIS[1:]:
        columnas.extend([metrica + " actual", metrica + " anterior"])
    if kpi != "Venta":
        columnas.extend([kpi + " diferencia", kpi + " YoY %"])
    tabla = tabla[columnas].rename(columns={"codigo": "Código"})
    formatos = {c: ("{:.2%}" if c.startswith("% Dcto") or c.endswith("YoY %") else "{:,.2f}")
                for c in tabla.columns if c not in ("Código", "Tienda", "Estado del KPI")}
    st.dataframe(tabla.style.format(formatos, na_rep="N/D"), hide_index=True, width="stretch")
    st.caption("Haz clic en los encabezados para ordenar. Los datos ausentes no se convierten en cero.")
    if not ausentes.empty:
        st.caption("Sin dato actual (solo año anterior, fuera de la tabla principal): " + "; ".join(
            f"{r.codigo} · {r.Tienda}" for _, r in ausentes.iterrows()))


def mostrar_year_to_year():
    import streamlit as st

    with st.expander("Cargar reportes para comparar", expanded=True):
        st.caption("Selecciona periodos equivalentes. Actual y anterior se asignan aquí, sin inferirlos del nombre. "
                   "Los archivos permanecen solo durante esta sesión.")
        columnas = st.columns(2)
        archivos = [columnas[0].file_uploader("Reporte actual", type=["xlsx"], key="yoy_archivo_actual"),
                    columnas[1].file_uploader("Reporte año anterior", type=["xlsx"], key="yoy_archivo_anterior")]
    reportes = []
    for rol, archivo in zip(("actual", "anterior"), archivos):
        clave = "yoy_reporte_" + rol
        if archivo is None:
            st.session_state.pop(clave, None)
            continue
        contenido = archivo.getvalue()
        previo = st.session_state.get(clave)
        if previo is None or previo[0] != contenido:
            st.session_state.pop(clave, None)
            try:
                resultado = leer_reporte(contenido)
            except ValueError as exc:
                st.error(f"Reporte {rol}: {exc}")
                continue
            st.session_state[clave] = (contenido, resultado)
        reportes.append(st.session_state[clave][1])
    if len(reportes) != 2:
        st.info("Carga ambos reportes para ver Year to Year.")
        return
    for rol, (_, control, _, _) in zip(("actual", "anterior"), reportes):
        if control.empty:
            st.warning(f"Reporte {rol}: sin TOTALES para conciliar.")
        elif not control.Coincide.all():
            st.warning(f"Reporte {rol}: hay diferencias contra TOTALES. Revisa la conciliación.")
    mostrar_comparativo(reportes[0][0], reportes[1][0])
    with st.expander("Bases de cálculo y conciliación YoY"):
        st.write("Los totales son sumas de tiendas, sin TOTALES. UPT = Σ Unidades / Σ # Doc; "
                 "ATV = Σ Venta / Σ # Doc; ASP = Σ Venta / Σ Unidades; % Dcto = Σ Descuento / Σ Venta Bruta. "
                 "Venta Neta se toma del ERP. No se promedian ratios individuales. "
                 "YoY = (actual − anterior) / anterior. Denominadores no positivos: N/D. "
                 "Tiendas comparables filtra ambos agregados a códigos comunes con al menos 30 documentos anteriores. "
                 "La disponibilidad del YoY de cada KPI depende además de su base y denominadores.")
        for rol, (_, control, _, _) in zip(("Actual", "Anterior"), reportes):
            st.write(rol)
            st.dataframe(control, hide_index=True, width="stretch")
