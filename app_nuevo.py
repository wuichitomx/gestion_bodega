import os
import io
import base64
import re
import unicodedata
from datetime import datetime, timezone
import pandas as pd
import streamlit as st
import altair as alt
from supabase import create_client
import google.generativeai as genai
from PIL import Image, ImageOps

# Importamos las reglas maestras desde nuestro archivo de configuración
from configuracion_ia import generar_prompt_maestro


def _normalizar_encabezado(valor):
    """Normaliza encabezados del ERP para poder reconocer variantes comunes."""
    texto = unicodedata.normalize("NFKD", str(valor or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", texto.lower()).strip()


def _normalizar_precio(valor):
    """Convierte importes numéricos o con formato regional a float."""
    if pd.isna(valor):
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor)

    texto = str(valor).strip()
    if not texto:
        return None
    negativo = texto.startswith("(") and texto.endswith(")")
    texto = re.sub(r"[^0-9,.-]", "", texto)
    if texto.count("-") > 1:
        return None
    texto = texto.replace("-", "")
    if not texto:
        return None

    if "," in texto and "." in texto:
        decimal = "," if texto.rfind(",") > texto.rfind(".") else "."
        miles = "." if decimal == "," else ","
        texto = texto.replace(miles, "").replace(decimal, ".")
    elif "," in texto:
        partes = texto.split(",")
        texto = "".join(partes) if len(partes[-1]) == 3 else "".join(partes[:-1]) + "." + partes[-1]
    elif texto.count(".") > 1:
        partes = texto.split(".")
        texto = "".join(partes) if len(partes[-1]) == 3 else "".join(partes[:-1]) + "." + partes[-1]

    try:
        numero = float(texto)
        return -numero if negativo else numero
    except ValueError:
        return None


def _separar_referencia(referencia):
    """Obtiene SKU maestro y talla desde el último sufijo de la referencia."""
    referencia = str(referencia).strip().upper()
    if "-" not in referencia:
        return referencia, ""
    sku_maestro, talla = referencia.rsplit("-", 1)
    return sku_maestro.strip(), talla.strip()


def preparar_lista_precios_excel(archivo):
    """Detecta el encabezado real y prepara la lista de precios para validación."""
    bruto = pd.read_excel(archivo, header=None, dtype=object)
    aliases = {
        "referencia": {"referencia", "ref", "codigo referencia", "referencia articulo", "referencia producto"},
        "descripcion": {"descripcion", "descripcion articulo", "descripcion producto", "nombre articulo", "producto"},
        "precio": {
            "lista publico", "lista de publico", "precio publico", "precio de publico",
            "precio lista", "precio de lista", "pvp", "precio venta", "precio venta publico"
        },
    }

    fila_encabezado = None
    indices = None
    for numero_fila, fila in bruto.head(60).iterrows():
        normalizados = [_normalizar_encabezado(valor) for valor in fila.tolist()]
        encontrados = {}
        for destino, opciones in aliases.items():
            for indice, encabezado in enumerate(normalizados):
                if encabezado in opciones:
                    encontrados[destino] = indice
                    break
        if len(encontrados) == len(aliases):
            fila_encabezado, indices = numero_fila, encontrados
            break

    if fila_encabezado is None:
        raise ValueError(
            "No se encontró una fila con Referencia, Descripción y Lista Público (o encabezados equivalentes) "
            "en las primeras 60 filas."
        )

    datos = bruto.iloc[fila_encabezado + 1:, [indices["referencia"], indices["descripcion"], indices["precio"]]].copy()
    datos.columns = ["referencia", "descripcion", "precio_original"]
    datos["referencia"] = datos["referencia"].where(datos["referencia"].notna(), "").astype(str).str.strip().str.upper()
    datos["descripcion"] = datos["descripcion"].where(datos["descripcion"].notna(), "").astype(str).str.strip()
    datos["precio_lista"] = datos["precio_original"].apply(_normalizar_precio)
    datos[["sku_maestro", "talla"]] = datos["referencia"].apply(
        lambda valor: pd.Series(_separar_referencia(valor))
    )

    vacias = datos["referencia"].eq("")
    precios_invalidos = datos["precio_lista"].isna() | (datos["precio_lista"] < 0)
    duplicadas = datos["referencia"].ne("") & datos["referencia"].duplicated(keep="last")
    validas = datos.loc[~vacias & ~precios_invalidos & ~duplicadas].copy()
    validas["precio_lista"] = validas["precio_lista"].round(2)
    validas = validas[["referencia", "descripcion", "talla", "sku_maestro", "precio_lista"]]

    conteos = {
        "filas_leidas": len(datos),
        "referencias_vacias": int(vacias.sum()),
        "precios_invalidos": int((precios_invalidos & ~vacias).sum()),
        "duplicados_descartados": int(duplicadas.sum()),
        "registros_validos": len(validas),
        "fila_encabezado": int(fila_encabezado) + 1,
    }
    return validas, conteos

st.set_page_config(page_title="Sinapsis", page_icon="⚡", layout="wide")

# ==========================================
# CONFIGURACIÓN SUPABASE E IA
# ==========================================
try:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    supabase = create_client(url, key)
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
except Exception as e:
    st.error(f"Error crítico de conexión: {e}")
    st.stop()

# ==========================================
# FUNCIÓN LOGO DINÁMICO (CLARO / OSCURO)
# ==========================================
def render_logo(ruta_imagen, width=160):
    if not os.path.exists(ruta_imagen):
        return
    try:
        with open(ruta_imagen, "rb") as f:
            img_data = f.read()
        b64_orig = base64.b64encode(img_data).decode()
        
        css = f"""
        <style>
        .logo-light-container img {{ display: block; width: {width}px; }}
        .logo-dark-container img {{ display: none; width: {width}px; }}
        .logo-wrapper {{
            background: rgba(255, 255, 255, 0.95);
            padding: 12px;
            border-radius: 12px;
            display: inline-block;
            box-shadow: 0 4px 15px rgba(0,0,0,0.3);
            margin-bottom: 15px;
        }}
        @media (prefers-color-scheme: dark) {{
            .logo-wrapper {{
                background: rgba(255, 255, 255, 0.95) !important;
            }}
        }}
        </style>
        <div class="logo-wrapper">
            <div class="logo-light-container">
                <img src="data:image/png;base64,{b64_orig}">
            </div>
        </div>
        """
        st.markdown(css, unsafe_allow_html=True)
    except Exception as e:
        pass

# ==========================================
# ESTILO VISUAL "SINAPSIS" (TRON / CYBERPUNK)
# ==========================================
st.markdown("""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;600;800&family=Inter:wght@300;400;500;600&display=swap');

    html, body, [class*="st"] {
        font-family: 'Inter', sans-serif !important;
    }

    [data-testid="stIconMaterial"] {
        font-family: 'Material Symbols Rounded' !important;
    }

    h1, h2, h3, .stHeader {
        font-family: 'Orbitron', sans-serif !important;
        letter-spacing: 1px;
    }

    [data-testid="stAppViewContainer"] {
        color: var(--text-color);
    }

    header [data-testid="stHeader"] span, 
    header [data-testid="collapsedControl"] span,
    section[data-testid="stSidebar"] button[kind="header"] span {
        display: none !important;
    }

    .stAlert, div[data-testid="stExpander"] {
        border-radius: 12px;
        border: 1px solid rgba(57, 255, 136, 0.3);
    }

    .kpi-card {
        background: linear-gradient(135deg, rgba(0, 217, 245, 0.03) 0%, rgba(57, 255, 136, 0.05) 100%);
        padding: 18px;
        border-radius: 12px;
        border-left: 5px solid #39FF88;
        border-top: 1px solid rgba(57, 255, 136, 0.15);
        box-shadow: 0 4px 20px rgba(0, 0, 0, 0.05);
        margin-bottom: 10px;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    
    .kpi-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 6px 25px rgba(57, 255, 136, 0.2);
    }

    .kpi-card h4 {
        color: #39FF88;
        font-weight: 600;
        font-family: 'Orbitron', sans-serif !important;
    }

    .login-title {
        font-family: 'Orbitron', sans-serif !important;
        font-size: 2rem !important;
        font-weight: 700;
        color: #FFFFFF;
        margin-bottom: 5px;
    }

    .stButton button[kind="primary"], div.stButton > button {
        border-radius: 8px;
        font-weight: 500;
        transition: all 0.3s ease;
    }

    div.stButton > button:hover {
        border-color: #39FF88;
        box-shadow: 0 0 12px rgba(57, 255, 136, 0.4);
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# LOGIN CON VALIDACIÓN DE ROL
# ==========================================
if "autenticado" not in st.session_state: 
    st.session_state.autenticado = False
if "es_admin" not in st.session_state:
    st.session_state.es_admin = False
if "felicitacion_mostrada" not in st.session_state:
    st.session_state.felicitacion_mostrada = False

if not st.session_state.autenticado:
    col_a, col_b, col_c = st.columns([1, 2, 1])
    with col_b:
        render_logo("logo_adidas.png", 160)
        
        st.markdown('<p class="login-title">⚡ Sinapsis</p>', unsafe_allow_html=True)
        st.caption("v3.21 (Neural Core) | Desarrollado por Risal Tech")
        
        with st.form("login_form"):
            u = st.text_input("Usuario")
            p = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Iniciar Sesión"):
                res = supabase.table("usuarios").select("*").eq("username", u.strip()).eq("password", p.strip()).execute()
                if res.data:
                    st.session_state.autenticado = True
                    st.session_state.usuario_actual = u.strip()
                    usuario_info = res.data[0]
                    st.session_state.usuario_info = usuario_info
                    
                    rol = usuario_info.get("rol", "").lower() if "rol" in usuario_info else ""
                    if rol == "admin" or "admin" in u.strip().lower():
                        st.session_state.es_admin = True
                    else:
                        st.session_state.es_admin = False
                        
                    st.rerun()
                else: 
                    st.error("Credenciales incorrectas.")
    st.stop()

# ==========================================
# MENÚ LATERAL
# ==========================================
with st.sidebar:
    render_logo("logo_adidas.png", 120)
    st.markdown("### ⚡ Sinapsis")
    st.caption("🚀 **Versión:** 3.21 (Neural Core)")
    st.caption(f"👤 **Usuario:** {st.session_state.usuario_actual}")
    
    if st.button("🚪 Cerrar Sesión"):
        st.session_state.autenticado = False
        st.session_state.es_admin = False
        st.session_state.felicitacion_mostrada = False
        st.rerun()
    st.markdown("---")

# ==========================================
# FUNCIONES DE IA Y ANÁLISIS
# ==========================================
def obtener_o_generar_storytelling(referencia, nombre_producto, categoria):
    try:
        ref_limpia = str(referencia).split('-')[0].strip()
        res_db = supabase.table("tips_ia").select("tips").eq("referencia", ref_limpia).execute().data
        
        if res_db and len(res_db) > 0 and res_db[0].get("tips"):
            return res_db[0].get("tips"), "⚡ (Obtenido del núcleo de datos)"
        
        model = genai.GenerativeModel('gemini-3.7-flash')
        prompt_maestro = generar_prompt_maestro(nombre_producto, ref_limpia, categoria)
        response = model.generate_content(prompt_maestro)
        nuevo_texto = response.text
        
        supabase.table("tips_ia").upsert({"referencia": ref_limpia, "tips": nuevo_texto}).execute()
        return nuevo_texto, "⚡ (Generado por IA con Red Sináptica)"
    except Exception as e:
        return f"⚠️ Error al procesar: {e}", "Error"

def agrupar_top_n(df, campo_valor, campo_categoria, top_n=5):
    df_ordenado = df.sort_values(by=campo_valor, ascending=False).reset_index(drop=True)
    if len(df_ordenado) <= top_n:
        return df_ordenado
    
    df_top = df_ordenado.iloc[:top_n].copy()
    valor_otros = df_ordenado.iloc[top_n:][campo_valor].sum()
    fila_otros = pd.DataFrame({campo_categoria: ["Otros"], campo_valor: [valor_otros]})
    return pd.concat([df_top, fila_otros], ignore_index=True)

def crear_grafica_barras_inteligente(df, campo_x, campo_y, titulo_x, titulo_y, color_base='#39FF88', umbral_adentro=15):
    df_sorted = df.sort_values(by=campo_x, ascending=False).reset_index(drop=True)
    orden_y = df_sorted[campo_y].tolist()
    
    base = alt.Chart(df_sorted).encode(
        y=alt.Y(f'{campo_y}:N', sort=orden_y, title=titulo_y),
        x=alt.X(f'{campo_x}:Q', title=titulo_x)
    )
    
    barras = base.mark_bar(color=color_base)
    
    texto_adentro = base.transform_filter(
        alt.datum[campo_x] > umbral_adentro
    ).mark_text(align='right', dx=-8, baseline='middle', color='#111111', fontWeight='bold', fontSize=11).encode(
        text=alt.Text(f'{campo_x}:Q', format=',.0f')
    )
    
    texto_afuera = base.transform_filter(
        alt.datum[campo_x] <= umbral_adentro
    ).mark_text(align='left', dx=5, baseline='middle', color='#FFFFFF', fontSize=11).encode(
        text=alt.Text(f'{campo_x}:Q', format=',.0f')
    )
    
    return (barras + texto_adentro + texto_afuera)

def mostrar_resumen_piso_ventas(supabase):
    st.header("📊 Resumen Ejecutivo: Piso de Ventas (PV)")
    
    res_ubic_all = supabase.table("ubicaciones").select("*").execute()
    res_cat = supabase.table("catalogo_erp").select("codigo_limpio, referencia, descripcion, nivel1, nivel2, nivel3, nivel4, stock_sistema").execute()
    
    if not res_ubic_all.data:
        st.warning("No hay registros en la tabla de ubicaciones.")
        return
        
    df_ubic_all = pd.DataFrame(res_ubic_all.data)
    df_cat = pd.DataFrame(res_cat.data)
    
    # MODIFICACIÓN: Reconocer tanto 'PV' exacto como sub-ubicaciones que inicien con 'PV -' o 'PV-'
    ubi_up = df_ubic_all['ubicacion'].astype(str).str.strip().str.upper()
    is_pv = (ubi_up == 'PV') | (ubi_up.str.startswith('PV -')) | (ubi_up.str.startswith('PV-'))
    
    df_ubic_pv = df_ubic_all[is_pv].copy()
    df_ubic_bodega = df_ubic_all[~is_pv].copy()
    
    if df_ubic_pv.empty:
        st.warning("No hay productos registrados específicamente en la ubicación 'PV' (Piso de Ventas).")
        return
        
    df_cruce = pd.merge(df_ubic_pv, df_cat, on="codigo_limpio", how="left")
    df_cruce.fillna({
        "nivel1": "Sin Categoría", 
        "nivel2": "Sin Actividad",
        "nivel3": "Sin Subcategoría", 
        "nivel4": "Sin Nivel 4",
        "referencia": "Desconocida", 
        "descripcion": "Sin descripción",
        "stock_sistema": 0
    }, inplace=True)
    
    df_cruce['modelo_base'] = df_cruce['referencia'].astype(str).apply(lambda x: x.split('-')[0].strip())
    
    total_piezas = df_cruce['cantidad'].sum()
    st.metric("Total de Piezas en Piso (PV)", f"{total_piezas:,.0f}")
    st.divider()
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Mezcla por Categoría General")
        df_cat1 = df_cruce.groupby("nivel1")['cantidad'].sum().reset_index()
        df_cat1 = agrupar_top_n(df_cat1, 'cantidad', 'nivel1', top_n=5)
        df_cat1['Porcentaje (%)'] = (df_cat1['cantidad'] / total_piezas) * 100
        df_cat1['Etiqueta_Leyenda'] = df_cat1.apply(lambda r: f"{r['nivel1']} ({r['Porcentaje (%)']:.1f}%)", axis=1)
        orden_leyenda_cat1 = df_cat1.sort_values('cantidad', ascending=False)['Etiqueta_Leyenda'].tolist()
        
        dona_cat1 = alt.Chart(df_cat1).mark_arc(innerRadius=55).encode(
            theta=alt.Theta(field="cantidad", type="quantitative"),
            color=alt.Color(field="Etiqueta_Leyenda", type="nominal", sort=orden_leyenda_cat1, legend=alt.Legend(title="Categoría", orient="right")),
            tooltip=[
                alt.Tooltip('nivel1:N', title='Categoría'), 
                alt.Tooltip('cantidad:Q', title='Piezas', format=',.0f'), 
                alt.Tooltip('Porcentaje (%):Q', format='.1f')
            ]
        ).properties(height=340)
        
        st.altair_chart(dona_cat1, use_container_width=True)
        
    with col2:
        st.subheader("Subcategorías (Volumen)")
        df_cat3 = df_cruce.groupby("nivel3")['cantidad'].sum().reset_index()
        chart_cat3 = crear_grafica_barras_inteligente(df_cat3, 'cantidad', 'nivel3', 'Piezas Físicas', None, color_base='#00D9F5', umbral_adentro=30)
        st.altair_chart(chart_cat3.properties(height=340), use_container_width=True)

    st.divider()
    
    st.subheader("🏃‍♂️ Análisis por Actividad / Deporte")
    st.caption("Visualiza la mezcla por deporte en **Porcentaje (%)**, **Piezas Físicas** y **Variedad de Modelos**.")
    
    st.markdown("##### 🍩 Mezcla Porcentual (%)")
    df_act_pct = df_cruce.groupby("nivel2")['cantidad'].sum().reset_index()
    df_act_pct = agrupar_top_n(df_act_pct, 'cantidad', 'nivel2', top_n=5)
    df_act_pct['Porcentaje (%)'] = (df_act_pct['cantidad'] / total_piezas) * 100
    df_act_pct['Etiqueta_Leyenda'] = df_act_pct.apply(lambda r: f"{r['nivel2']} ({r['Porcentaje (%)']:.1f}%)", axis=1)
    orden_leyenda_act = df_act_pct.sort_values('cantidad', ascending=False)['Etiqueta_Leyenda'].tolist()
    
    dona_act_base = alt.Chart(df_act_pct).encode(
        theta=alt.Theta(field="cantidad", type="quantitative", stack=True),
        color=alt.Color(field="Etiqueta_Leyenda", type="nominal", sort=orden_leyenda_act, legend=alt.Legend(title="Deporte", orient="right")),
        order=alt.Order(field="cantidad", sort="descending"),
        tooltip=[
            alt.Tooltip('nivel2:N', title='Deporte'), 
            alt.Tooltip('cantidad:Q', title='Piezas', format=',.0f'), 
            alt.Tooltip('Porcentaje (%):Q', format='.1f')
        ]
    )
    dona_act_arco = dona_act_base.mark_arc(innerRadius=90, outerRadius=180)
    dona_act_texto = dona_act_base.mark_text(radius=210, fontSize=13, fontWeight='bold').encode(
        text=alt.Text('Porcentaje (%):Q', format='.1f')
    )
    st.altair_chart((dona_act_arco + dona_act_texto).properties(height=480), use_container_width=True)

    col_act2, col_act3 = st.columns(2)
    with col_act2:
        st.markdown("##### 📊 Volumen en Piezas")
        df_act_p = df_cruce.groupby("nivel2")['cantidad'].sum().reset_index()
        chart_act_p = crear_grafica_barras_inteligente(df_act_p, 'cantidad', 'nivel2', 'Total Piezas', None, color_base='#39FF88', umbral_adentro=25)
        st.altair_chart(chart_act_p.properties(height=360), use_container_width=True)
        
    with col_act3:
        st.markdown("##### 📋 Variedad de Modelos")
        df_act_r = df_cruce.groupby("nivel2")['modelo_base'].nunique().reset_index().rename(columns={'modelo_base': 'total_modelos'})
        chart_act_r = crear_grafica_barras_inteligente(df_act_r, 'total_modelos', 'nivel2', 'Modelos', None, color_base='#00D9F5', umbral_adentro=8)
        st.altair_chart(chart_act_r.properties(height=360), use_container_width=True)

    st.divider()
    
    st.subheader("⚠️ Alerta de Picos / Resurtido")
    st.markdown("Modelos con **menos de 4 piezas en total** en Piso de Ventas, cruzados con su existencia en bodega e inventario en sistema del ERP. Utiliza el selector por actividad para asignar tareas de resurtido a cada asesor.")
    
    lista_actividades = sorted(df_cruce['nivel2'].dropna().unique().tolist())
    opciones_filtro = ["Todas las Áreas"] + lista_actividades
    actividad_seleccionada = st.selectbox("🎯 Filtrar Resurtido por Área / Actividad (Responsable):", options=opciones_filtro)

    df_ref_pv = df_cruce.groupby(['modelo_base', 'descripcion', 'nivel2'])['cantidad'].sum().reset_index()
    df_picos = df_ref_pv[df_ref_pv['cantidad'] < 4].copy()
    
    if actividad_seleccionada != "Todas las Áreas":
        df_picos = df_picos[df_picos['nivel2'] == actividad_seleccionada]

    if not df_ubic_bodega.empty:
        df_cruce_bodega = pd.merge(df_ubic_bodega, df_cat, on="codigo_limpio", how="left")
        df_cruce_bodega['referencia'] = df_cruce_bodega['referencia'].fillna("Desconocida")
        df_cruce_bodega['modelo_base'] = df_cruce_bodega['referencia'].astype(str).apply(lambda x: str(x).split('-')[0].strip())
        
        df_bodega_totales = df_cruce_bodega.groupby('modelo_base')['cantidad'].sum().reset_index().rename(columns={'cantidad': 'piezas_bodega'})
        df_bodega_ubic = df_cruce_bodega.groupby('modelo_base')['ubicacion'].apply(lambda x: ", ".join(x.dropna().astype(str).unique())).reset_index().rename(columns={'ubicacion': 'Ubicaciones en Bodega'})
        df_bodega_totales = pd.merge(df_bodega_totales, df_bodega_ubic, on='modelo_base', how='left')
    else:
        df_bodega_totales = pd.DataFrame(columns=['modelo_base', 'piezas_bodega', 'Ubicaciones en Bodega'])
        
    if not df_cat.empty:
        df_cat['modelo_base'] = df_cat['referencia'].astype(str).apply(lambda x: str(x).split('-')[0].strip())
        df_sistema_totales = df_cat.groupby('modelo_base')['stock_sistema'].sum().reset_index().rename(columns={'stock_sistema': 'inventario_sistema'})
    else:
        df_sistema_totales = pd.DataFrame(columns=['modelo_base', 'inventario_sistema'])

    df_picos = pd.merge(df_picos, df_bodega_totales, on='modelo_base', how='left')
    df_picos = pd.merge(df_picos, df_sistema_totales, on='modelo_base', how='left')
    
    df_picos['piezas_bodega'] = df_picos['piezas_bodega'].fillna(0).astype(int)
    df_picos['Ubicaciones en Bodega'] = df_picos['Ubicaciones en Bodega'].fillna("No escaneado")
    df_picos['inventario_sistema'] = df_picos['inventario_sistema'].fillna(0).astype(int)
    df_picos = df_picos.sort_values(by="cantidad")
    
    if not df_picos.empty:
        df_tabla_final = df_picos.rename(columns={
            "modelo_base": "Modelo (Ref Base)", 
            "descripcion": "Descripción", 
            "nivel2": "Área / Actividad",
            "cantidad": "Piezas en Piso (PV)",
            "piezas_bodega": "Disponible en Bodega",
            "Ubicaciones en Bodega": "Ubicaciones en Bodega",
            "inventario_sistema": "Inventario en Sistema"
        })
        
        st.dataframe(
            df_tabla_final[['Modelo (Ref Base)', 'Descripción', 'Área / Actividad', 'Piezas en Piso (PV)', 'Disponible en Bodega', 'Ubicaciones en Bodega', 'Inventario en Sistema']],
            use_container_width=True,
            hide_index=True
        )
        st.info(f"Se detectaron {len(df_picos)} modelos listos para ruta de resurtido en el área seleccionada.")
    else:
        st.success("¡Excelente! No hay modelos con menos de 4 piezas en el área seleccionada.")

# ==========================================
# INTERFAZ PRINCIPAL CON PESTAÑAS
# ==========================================
nombre_usuario_actual = st.session_state.usuario_info.get('nombre_completo', '')
if not nombre_usuario_actual or nombre_usuario_actual == 'None':
    nombre_usuario_actual = st.session_state.usuario_actual

st.title(f"⚡ ¡Bienvenid@, {nombre_usuario_actual}!")

# --- LÓGICA DE FELICITACIÓN Y LIDERAZGO MULTI-KPI ---
if os.path.exists("ventas_diarias_temp.csv"):
    df_v_felicitacion = pd.read_csv("ventas_diarias_temp.csv")
    if not df_v_felicitacion.empty and 'codigo' in df_v_felicitacion.columns:
        codigo_erp_bd_actual = st.session_state.usuario_info.get('codigo_erp', '')
        if not codigo_erp_bd_actual or codigo_erp_bd_actual == 'None':
            codigo_erp_bd_actual = st.session_state.usuario_actual
        usuario_code_actual = str(codigo_erp_bd_actual).strip().lower()
        
        # Verificamos líderes en cada categoría si existen las columnas
        es_top_cualquiera = False
        
        # 1. Ventas Netas
        if 'Neto_D_num' in df_v_felicitacion.columns:
            top_neto = df_v_felicitacion.loc[df_v_felicitacion['Neto_D_num'].idxmax()]
            if usuario_code_actual == str(top_neto['codigo']).strip().lower() and top_neto['Neto_D_num'] > 0:
                es_top_cualquiera = True
                if not st.session_state.felicitacion_mostrada:
                    st.balloons()
                    st.session_state.felicitacion_mostrada = True
                st.success(f"🏆 ¡Felicidades, {top_neto['nombre']}! Eres el primer lugar en ventas con ${top_neto['Neto_D_num']:,.2f}. ¡Sigue así, liderando la red!")

        # 2. UPT
        if 'UPT_D_num' in df_v_felicitacion.columns:
            top_upt = df_v_felicitacion.loc[df_v_felicitacion['UPT_D_num'].idxmax()]
            if usuario_code_actual == str(top_upt['codigo']).strip().lower() and top_upt['UPT_D_num'] > 0:
                es_top_cualquiera = True
                st.success(f"🎯 ¡Felicidades, {top_upt['nombre']}! Eres el primer lugar en UPT con {top_upt['UPT_D_num']:,.2f} unidades por ticket. ¡Excelente trabajo!")

        # 3. ASP
        if 'ASP_D_num' in df_v_felicitacion.columns:
            top_asp = df_v_felicitacion.loc[df_v_felicitacion['ASP_D_num'].idxmax()]
            if usuario_code_actual == str(top_asp['codigo']).strip().lower() and top_asp['ASP_D_num'] > 0:
                es_top_cualquiera = True
                st.success(f"💎 ¡Felicidades, {top_asp['nombre']}! Eres el primer lugar en ASP con un precio promedio de ${top_asp['ASP_D_num']:,.2f}. ¡Imparable!")

        # 4. ATV
        if 'ATV_D_num' in df_v_felicitacion.columns:
            top_atv = df_v_felicitacion.loc[df_v_felicitacion['ATV_D_num'].idxmax()]
            if usuario_code_actual == str(top_atv['codigo']).strip().lower() and top_atv['ATV_D_num'] > 0:
                es_top_cualquiera = True
                st.success(f"🔥 ¡Felicidades, {top_atv['nombre']}! Eres el primer lugar en ATV con un ticket promedio de ${top_atv['ATV_D_num']:,.2f}. ¡Sigue liderando!")

        if not st.session_state.felicitacion_mostrada and es_top_cualquiera:
            st.balloons()
            st.session_state.felicitacion_mostrada = True

if st.session_state.es_admin:
    tabs = st.tabs(["📊 Dashboard", "🔍 Búsqueda Manual", "📈 Resumen PV", "📦 Scanner Rápido", "⚙️ Admin & Carga ERP", "👥 Gestión Usuarios"])
    tab_perf, tab_busqueda, tab_resumen_pv, tab_escaneo, tab_admin_erp, tab_admin_user = tabs[0], tabs[1], tabs[2], tabs[3], tabs[4], tabs[5]
else:
    tabs = st.tabs(["📊 Dashboard", "🔍 Búsqueda Manual"])
    tab_perf, tab_busqueda = tabs[0], tabs[1]
    tab_resumen_pv, tab_escaneo, tab_admin_erp, tab_admin_user = None, None, None, None

# ------------------------------------------
# 5. PESTAÑA: ADMIN & CARGA ERP (SÓLO ADMIN)
# ------------------------------------------
if tab_admin_erp is not None:
    with tab_admin_erp:
        st.subheader("📥 Cargar Reporte de Ventas Diario del ERP (Excel)")
        archivo_sales = st.file_uploader("Subir Archivo Excel de Ventas", type=["xlsx", "xls"], key="sales_excel")
        
        if archivo_sales is not None:
            try:
                df_raw = pd.read_excel(archivo_sales, header=2)
                df_raw = df_raw[df_raw['No. Línea'].notna() & (df_raw['No. Línea'].astype(str).str.strip().str.upper() != 'TOTALES')]
                
                df_raw['codigo'] = df_raw['Código de Vendedor'].astype(str).str.strip()
                df_raw['nombre'] = df_raw['Vendedor'].astype(str).str.strip()
                
                df_raw['Neto_D_num'] = pd.to_numeric(df_raw['Venta Neta'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                df_raw['UPT_D_num'] = pd.to_numeric(df_raw['Items x Doc.'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                df_raw['ATV_D_num'] = pd.to_numeric(df_raw['Venta x Documento'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                df_raw['ASP_D_num'] = pd.to_numeric(df_raw['Precio x Unidad'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                
                df_raw_totales = pd.read_excel(archivo_sales, header=2)
                row_totales = df_raw_totales[df_raw_totales['No. Línea'].astype(str).str.strip().str.upper() == 'TOTALES']
                
                if not row_totales.empty:
                    df_raw['Neto_T_num'] = float(str(row_totales.iloc[0]['Venta Neta']).replace(',', ''))
                    df_raw['UPT_T_num'] = float(str(row_totales.iloc[0]['Items x Doc.']).replace(',', ''))
                    df_raw['ATV_T_num'] = float(str(row_totales.iloc[0]['Venta x Documento']).replace(',', ''))
                    df_raw['ASP_T_num'] = float(str(row_totales.iloc[0]['Precio x Unidad']).replace(',', ''))
                else:
                    df_raw['Neto_T_num'] = df_raw['Neto_D_num'].sum()
                    df_raw['UPT_T_num'] = df_raw['UPT_D_num'].mean()
                    df_raw['ATV_T_num'] = df_raw['ATV_D_num'].mean()
                    df_raw['ASP_T_num'] = df_raw['ASP_D_num'].mean()
                
                df_raw.to_csv("ventas_diarias_temp.csv", index=False)
                st.session_state.felicitacion_mostrada = False
                
                st.success("✅ Reporte de ventas en Excel procesado por la red correctamente.")
                st.info("👉 Ahora puedes ir a la pestaña '📊 Dashboard' para ver los resultados actualizados.")
            except Exception as ex:
                st.error(f"Error al procesar el archivo Excel: {ex}")
        
        st.markdown("---")
        st.subheader("📦 Cargar Catálogo de Inventario al Núcleo")
        st.info("Sube aquí el archivo de tu ERP (RPInv_Extracto_Referencia) en formato Excel para sincronizar la red en Supabase.")

        archivo_catalogo = st.file_uploader("Subir Catálogo (Excel)", type=["xlsx", "xls"], key="cat_csv")

        if archivo_catalogo is not None:
            if st.button("⚡ Sincronizar Catálogo en la Nube"):
                try:
                    with st.spinner("Estableciendo sinapsis y sincronizando el núcleo... Esto puede tomar unos segundos."):
                        df_cat = pd.read_excel(archivo_catalogo, skiprows=5, dtype=str)
                        
                        mapeo_columnas = {
                            'CodigoAlterno': 'codigo_limpio',
                            'Referencia': 'referencia',
                            'Descripcion': 'descripcion',
                            'Cantidad': 'stock_sistema',
                            'Nivel1': 'nivel1',
                            'Nivel2': 'nivel2',
                            'Nivel3': 'nivel3',
                            'Nivel4': 'nivel4'
                        }
                        
                        df_cat = df_cat.rename(columns=mapeo_columnas)
                        
                        if 'referencia' in df_cat.columns:
                            df_cat['talla'] = df_cat['referencia'].apply(lambda x: str(x).split('-', 1)[1] if '-' in str(x) else '')
                        
                        columnas_esperadas = ['codigo_limpio', 'referencia', 'descripcion', 'talla', 'nivel1', 'nivel2', 'nivel3', 'nivel4', 'stock_sistema']
                        columnas_existentes = [col for col in columnas_esperadas if col in df_cat.columns]
                        df_cat = df_cat[columnas_existentes]
                        
                        if 'stock_sistema' in df_cat.columns:
                            df_cat['stock_sistema'] = pd.to_numeric(df_cat['stock_sistema'], errors='coerce').fillna(0).astype(int)
                        
                        if 'codigo_limpio' in df_cat.columns:
                            df_cat = df_cat.dropna(subset=['codigo_limpio'])
                        
                        df_cat = df_cat.astype(object).where(pd.notna(df_cat), None)
                        registros = df_cat.to_dict(orient="records")

                        TAMANO_BLOQUE = 500
                        total_registros = len(registros)
                        
                        barra_progreso = st.progress(0, text=f"Sincronizando 0 de {total_registros} artículos...")

                        for i in range(0, total_registros, TAMANO_BLOQUE):
                            bloque = registros[i:i + TAMANO_BLOQUE]
                            supabase.table("catalogo_erp").upsert(bloque).execute()

                            subidos = min(i + TAMANO_BLOQUE, total_registros)
                            porcentaje = subidos / total_registros
                            barra_progreso.progress(porcentaje, text=f"Sincronizando {subidos} de {total_registros} artículos...")

                        barra_progreso.empty()
                        st.success(f"⚡ ¡Núcleo actualizado exitosamente! Se sincronizaron {total_registros} artículos.")
                except Exception as ex:
                    st.error(f"⚠️ Error al sincronizar: {ex}")

        st.markdown("---")
        st.subheader("💲 Actualizar Lista de Precios ERP")
        st.info(
            "Carga un Excel de precios. La red localizará el encabezado real y preparará "
            "Referencia, Descripción, talla, SKU maestro y precio antes de sincronizar."
        )
        archivo_precios = st.file_uploader(
            "Subir Lista de Precios (Excel)",
            type=["xlsx", "xls"],
            key="price_list_excel",
        )

        if archivo_precios is not None:
            try:
                df_precios, validacion_precios = preparar_lista_precios_excel(archivo_precios)
                st.caption(
                    f"Encabezado detectado en la fila {validacion_precios['fila_encabezado']} del archivo."
                )
                col_p1, col_p2, col_p3, col_p4 = st.columns(4)
                col_p1.metric("Filas leídas", validacion_precios["filas_leidas"])
                col_p2.metric("Registros válidos", validacion_precios["registros_validos"])
                col_p3.metric(
                    "Sin referencia/precio",
                    validacion_precios["referencias_vacias"] + validacion_precios["precios_invalidos"],
                )
                col_p4.metric("Duplicados descartados", validacion_precios["duplicados_descartados"])

                st.markdown("**Vista previa de los registros válidos**")
                st.dataframe(df_precios.head(100), use_container_width=True, hide_index=True)
                if len(df_precios) > 100:
                    st.caption(f"Se muestran 100 de {len(df_precios)} registros válidos.")

                confirmar_precios = st.checkbox(
                    "Confirmo que revisé la vista previa y deseo actualizar `lista_precios`.",
                    key="confirm_price_upload",
                )
                if st.button(
                    "⚡ Sincronizar Lista de Precios",
                    disabled=not confirmar_precios or df_precios.empty,
                    key="sync_price_list",
                ):
                    fecha_actualizacion = datetime.now(timezone.utc).isoformat()
                    df_precios_subida = df_precios.assign(fecha_actualizacion=fecha_actualizacion)
                    df_precios_subida = df_precios_subida.astype(object).where(pd.notna(df_precios_subida), None)
                    registros_precios = df_precios_subida.to_dict(orient="records")
                    tamano_bloque_precios = 500
                    total_precios = len(registros_precios)
                    barra_precios = st.progress(0, text=f"Sincronizando 0 de {total_precios} precios...")

                    for inicio in range(0, total_precios, tamano_bloque_precios):
                        bloque_precios = registros_precios[inicio:inicio + tamano_bloque_precios]
                        supabase.table("lista_precios").upsert(
                            bloque_precios,
                            on_conflict="referencia",
                        ).execute()
                        procesados = min(inicio + tamano_bloque_precios, total_precios)
                        barra_precios.progress(
                            procesados / total_precios,
                            text=f"Sincronizando {procesados} de {total_precios} precios...",
                        )

                    st.success(f"✅ Lista de precios actualizada: {total_precios} registros sincronizados.")
            except Exception as ex:
                st.error(f"⚠️ No se pudo procesar la lista de precios: {ex}")
        
        st.markdown("---")
        st.subheader("⚙️ Configuración de Metas por Asesor")
        
        res_u = supabase.table("usuarios").select("*").execute().data
        if res_u:
            df_u = pd.DataFrame(res_u)
            for col in ['codigo_erp', 'nombre_completo', 'meta_mensual']:
                if col not in df_u.columns:
                    df_u[col] = ""
                    
            edited_df = st.data_editor(
                df_u[['username', 'codigo_erp', 'nombre_completo', 'meta_mensual']],
                column_config={
                    "username": st.column_config.TextColumn("Usuario App", disabled=True),
                    "codigo_erp": st.column_config.TextColumn("Código ERP"),
                    "nombre_completo": st.column_config.TextColumn("Nombre Completo"),
                    "meta_mensual": st.column_config.NumberColumn("Meta Mensual ($)", format="$%.2f", step=0.01, min_value=0.0)
                },
                use_container_width=True
            )
            
            if st.button("Guardar Cambios de Metas"):
                for _, row in edited_df.iterrows():
                    supabase.table("usuarios").update({
                        "codigo_erp": str(row['codigo_erp']).strip(),
                        "nombre_completo": str(row['nombre_completo']).strip(),
                        "meta_mensual": float(row['meta_mensual']) if row['meta_mensual'] else 0.0
                    }).eq("username", row['username']).execute()
                st.success("¡Metas guardadas correctamente en la red!")
                st.rerun()

# ------------------------------------------
# 1. PESTAÑA: PERFORMANCE & KPIS (DASHBOARD)
# ------------------------------------------
with tab_perf:
    if not os.path.exists("ventas_diarias_temp.csv"):
        st.header("📊 Tablero de Rendimiento Diario")
        st.info("ℹ️ No se ha cargado el reporte de ventas del día. El administrador puede subirlo en la pestaña '⚙️ Admin & Carga ERP'.")
    else:
        df_v = pd.read_csv("ventas_diarias_temp.csv")
        res_u = supabase.table("usuarios").select("*").execute().data
        df_users = pd.DataFrame(res_u) if res_u else pd.DataFrame()
        
        if not df_users.empty and "codigo_erp" in df_users.columns:
            df_v = df_v.merge(df_users[['codigo_erp', 'meta_mensual']], left_on='codigo', right_on='codigo_erp', how='left')
            df_v['meta_mensual'] = pd.to_numeric(df_v['meta_mensual'], errors='coerce').fillna(0)
        else:
            df_v['meta_mensual'] = 0.0

        venta_tienda_neto = df_v['Neto_T_num'].iloc[0] if 'Neto_T_num' in df_v.columns else 0.0
        meta_tienda_total = df_v['meta_mensual'].sum() if df_v['meta_mensual'].sum() > 0 else 1571112.40
        alcance_tienda_pct = (venta_tienda_neto / meta_tienda_total) * 100 if meta_tienda_total > 0 else 0
        falta_tienda = max(0.0, meta_tienda_total - venta_tienda_neto)
        
        upt_tienda = df_v['UPT_T_num'].iloc[0] if 'UPT_T_num' in df_v.columns else 0.0
        atv_tienda = df_v['ATV_T_num'].iloc[0] if 'ATV_T_num' in df_v.columns else 0.0
        asp_tienda = df_v['ASP_T_num'].iloc[0] if 'ASP_T_num' in df_v.columns else 0.0

        def crear_grafica_kpi(df, campo_y, titulo, promedio_tienda, formato):
            df_sorted = df.sort_values(campo_y, ascending=False).reset_index(drop=True)
            max_val = df_sorted[campo_y].max()
            df_sorted['Color'] = df_sorted[campo_y].apply(lambda x: '#39FF88' if x == max_val and x > 0 else '#00D9F5')
            
            orden_x = df_sorted['nombre'].tolist()
            base = alt.Chart(df_sorted).encode(x=alt.X('nombre:N', sort=orden_x, title='Asesor', axis=alt.Axis(labelAngle=-45)))
            bar = base.mark_bar(opacity=0.85, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                y=alt.Y(f'{campo_y}:Q', title=titulo),
                color=alt.Color('Color:N', scale=None),
                tooltip=[alt.Tooltip('nombre:N', title='Asesor'), alt.Tooltip(f'{campo_y}:Q', title=titulo, format=formato)]
            )
            df_sorted['_mitad'] = df_sorted[campo_y] / 2
            text = alt.Chart(df_sorted).mark_text(align='center', baseline='middle', angle=270, color='white', fontWeight='bold', fontSize=10).encode(
                x=alt.X('nombre:N', sort=orden_x),
                y=alt.Y('_mitad:Q'),
                text=alt.Text(f'{campo_y}:Q', format=formato)
            )
            rule = alt.Chart(pd.DataFrame({campo_y: [promedio_tienda]})).mark_rule(
                color='#FFD700', strokeWidth=3, strokeDash=[5, 5]
            ).encode(y=f'{campo_y}:Q')
            return (bar + text + rule).properties(height=320, title=f"{titulo}")

        if not st.session_state.es_admin:
            codigo_erp_bd = st.session_state.usuario_info.get('codigo_erp', '')
            if not codigo_erp_bd or codigo_erp_bd == 'None':
                codigo_erp_bd = st.session_state.usuario_actual
                
            usuario_code = str(codigo_erp_bd).strip().lower()
            user_row = df_v[df_v['codigo'].astype(str).str.strip().str.lower() == usuario_code]
            
            if not user_row.empty:
                row_asesor = user_row.iloc[0]
                nombre_asesor = row_asesor.get('nombre', usuario_code)
                venta_asesor_neto = row_asesor.get('Neto_D_num', 0.0)
                meta_asesor = row_asesor.get('meta_mensual', 282800.23)
                alcance_asesor_pct = (venta_asesor_neto / meta_asesor) * 100 if meta_asesor > 0 else 0
                falta_asesor = max(0.0, meta_asesor - venta_asesor_neto)
                
                max_venta_red = df_v['Neto_D_num'].max()
                diferencia_primer_lugar = max_venta_red - venta_asesor_neto
                if diferencia_primer_lugar > 0:
                    texto_primer_lugar = f"🎯 <b>Te faltan ${diferencia_primer_lugar:,.2f} para alcanzar al 1.er lugar en ventas</b>"
                else:
                    texto_primer_lugar = "👑 <b>¡Vas a la cabeza del primer lugar en ventas!</b>"

                meta_71 = meta_asesor * 0.71
                falta_71 = max(0.0, meta_71 - venta_asesor_neto)
                txt_71 = f"${falta_71:,.2f}" if falta_71 > 0 else "✅ ¡Alcanzado!"
                
                meta_81 = meta_asesor * 0.81
                falta_81 = max(0.0, meta_81 - venta_asesor_neto)
                txt_81 = f"${falta_81:,.2f}" if falta_81 > 0 else "✅ ¡Alcanzado!"
                
                meta_91 = meta_asesor * 0.91
                falta_91 = max(0.0, meta_91 - venta_asesor_neto)
                txt_91 = f"${falta_91:,.2f}" if falta_91 > 0 else "✅ ¡Alcanzado!"
                
                txt_100 = f"${falta_asesor:,.2f}" if falta_asesor > 0 else "🚀 ¡Meta Superada!"
                
                st.header("📊 Mi Tablero de Rendimiento")
                st.subheader(f"👤 MI PERFORMANCE ({nombre_asesor})")
                st.markdown(f"""
                <div class="kpi-card">
                    <h4>Mi Meta Mensual: ${meta_asesor:,.2f}</h4>
                    <p><b>Mi Venta Neta:</b> ${venta_asesor_neto:,.2f}</p>
                    <p><b>Mi Alcance:</b> {alcance_asesor_pct:.1f}%</p>
                    <p>{texto_primer_lugar}</p>
                    <hr style="border-color: rgba(57,255,136,0.2); margin: 10px 0;">
                    <p>🎯 <b>Falta para Primer Objetivo (71%):</b> {txt_71}</p>
                    <p>🎯 <b>Falta para Segundo Objetivo (81%):</b> {txt_81}</p>
                    <p>🎯 <b>Falta para Tercer Objetivo (91%):</b> {txt_91}</p>
                    <p>🏆 <b>Falta para Meta Final (100%):</b> {txt_100}</p>
                </div>
                """, unsafe_allow_html=True)
                st.progress(min(1.0, alcance_asesor_pct / 100))
                st.markdown("---")

        if st.session_state.es_admin:
            st.header("📊 Tablero Gerencial Diario")
        else:
            st.header("🏢 Tablero General de la Tienda")
            
        st.subheader("🏬 PERFORMANCE TIENDA")
        st.markdown(f"""
        <div class="kpi-card">
            <h4>Meta Tienda: ${meta_tienda_total:,.2f}</h4>
            <p><b>Venta Acumulada Neta:</b> ${venta_tienda_neto:,.2f}</p>
            <p><b>Falta para la Meta:</b> ${falta_tienda:,.2f}</p>
            <p><b>Alcance:</b> {alcance_tienda_pct:.1f}%</p>
        </div>
        """, unsafe_allow_html=True)
        st.progress(min(1.0, alcance_tienda_pct / 100))
        
        st.markdown("---")
        st.subheader("📈 Ranking de Ventas Acumuladas por Asesor ($)")
        
        df_chart = df_v[['nombre', 'Neto_D_num']].sort_values('Neto_D_num', ascending=False).reset_index(drop=True)
        max_venta = df_chart['Neto_D_num'].max()
        df_chart['Color'] = df_chart['Neto_D_num'].apply(lambda x: '#39FF88' if x == max_venta and x > 0 else '#00D9F5')
        
        sort_vendedor = alt.EncodingSortField(field='Neto_D_num', op='sum', order='descending')
        bars = alt.Chart(df_chart).mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6).encode(
            x=alt.X('nombre:N', sort=sort_vendedor, title='Asesor', axis=alt.Axis(labelAngle=-45)),
            y=alt.Y('Neto_D_num:Q', title='Venta Neta ($)'),
            color=alt.Color('Color:N', scale=None),
            tooltip=[alt.Tooltip('nombre:N', title='Asesor'), alt.Tooltip('Neto_D_num:Q', title='Venta Neta', format='$,.2f')]
        )
        text = bars.mark_text(align='center', baseline='bottom', dy=-5, color='white').encode(text=alt.Text('Neto_D_num:Q', format='$,.0f'))
        st.altair_chart((bars + text).properties(height=350), use_container_width=True)

        st.markdown("---")
        st.subheader("🎯 Radiografía Operativa: KPIs por Asesor vs Promedio Tienda")
        st.caption("🟡 La línea amarilla punteada indica el promedio general de la tienda (reportado por el ERP).")

        df_kpis = df_v[['nombre', 'UPT_D_num', 'ATV_D_num', 'ASP_D_num']].copy()
        
        kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
        with kpi_col1:
            st.altair_chart(crear_grafica_kpi(df_kpis, 'UPT_D_num', 'UPT (Unidades x Ticket)', upt_tienda, '.2f'), use_container_width=True)
        with kpi_col2:
            st.altair_chart(crear_grafica_kpi(df_kpis, 'ATV_D_num', 'ATV (Ticket Promedio)', atv_tienda, '$,.0f'), use_container_width=True)
        with kpi_col3:
            st.altair_chart(crear_grafica_kpi(df_kpis, 'ASP_D_num', 'ASP (Precio Promedio)', asp_tienda, '$,.0f'), use_container_width=True)

# ------------------------------------------
# 2. PESTAÑA: BÚSQUEDA MANUAL
# ------------------------------------------
with tab_busqueda:
    col1, col2, col3 = st.columns(3)
    in_ref = col1.text_input("🔍 Ref/Desc:", key="in_ref")
    in_talla = col2.text_input("📏 Talla:", key="in_talla")
    in_ubic = col3.text_input("🏢 Estante/Ubicación:", key="in_ubic")

    if st.session_state.es_admin:
        solo_disp = st.checkbox("Ocultar stock en 0", value=True)
    else:
        solo_disp = True
        
    if st.button("Buscar en la Red") or in_ref or in_talla or in_ubic:
        codigos_en_ubicacion = None
        if in_ubic:
            res_ubic_busqueda = supabase.table("ubicaciones").select("codigo_limpio").ilike("ubicacion", f"%{in_ubic.strip()}%").execute()
            codigos_en_ubicacion = list({item['codigo_limpio'] for item in (res_ubic_busqueda.data or [])})
            if not codigos_en_ubicacion:
                st.warning(f"⚠️ No hay artículos escaneados en la ubicación '{in_ubic.strip()}'.")

        query = supabase.table("catalogo_erp").select("*")
        if solo_disp: query = query.gt("stock_sistema", 0)
        
        if in_ref: 
            query = query.or_(
                f"referencia.ilike.%{in_ref.strip()}%, "
                f"descripcion.ilike.%{in_ref.strip()}%, "
                f"nivel1.ilike.%{in_ref.strip()}%, "
                f"nivel2.ilike.%{in_ref.strip()}%, "
                f"nivel3.ilike.%{in_ref.strip()}%"
            )
            
        if in_talla: query = query.like("talla", f"{in_talla.strip()}%")
        if codigos_en_ubicacion is not None:
            if not codigos_en_ubicacion:
                codigos_en_ubicacion = ["__sin_resultados__"]
            query = query.in_("codigo_limpio", codigos_en_ubicacion)
        
        res = query.limit(50).execute() 
        if res.data:
            df = pd.DataFrame(res.data)
            codigos_encontrados = df['codigo_limpio'].dropna().astype(str).unique().tolist()
            if codigos_encontrados:
                res_ubic = supabase.table("ubicaciones").select("codigo_limpio, ubicacion, cantidad").in_("codigo_limpio", codigos_encontrados).execute()
                if res_ubic.data:
                    df_ubic = pd.DataFrame(res_ubic.data)
                    df_ubic['ubicacion_texto'] = df_ubic['ubicacion'].astype(str) + " (" + df_ubic['cantidad'].astype(str) + " pza)"
                    df_ubic_agrupado = df_ubic.groupby('codigo_limpio')['ubicacion_texto'].apply(lambda x: ", ".join(x)).reset_index()
                    df_ubic_agrupado = df_ubic_agrupado.rename(columns={'ubicacion_texto': 'ubicacion'})
                    df = df.merge(df_ubic_agrupado, on='codigo_limpio', how='left')
                else:
                    df['ubicacion'] = None
            else:
                df['ubicacion'] = None
            df['ubicacion'] = df['ubicacion'].fillna("Sin ubicación registrada")

            st.success(f"⚡ Se conectaron {len(df)} artículos en la red.")

            if st.session_state.es_admin:
                st.dataframe(df[['codigo_limpio', 'referencia', 'descripcion', 'talla', 'nivel1', 'stock_sistema', 'ubicacion']], use_container_width=True)
            else:
                st.dataframe(df[['referencia', 'descripcion', 'talla', 'ubicacion']], use_container_width=True)
            
            ref_raw = df.iloc[0]['referencia']
            codigo_detectado = str(ref_raw).split('-')[0].strip()
            nombre_detectado = df.iloc[0]['descripcion']
            categoria_detectada = df.iloc[0].get('nivel1', 'General')
            
            st.markdown("---")
            st.markdown("#### ⚡ Asistente Sináptico de Ventas")
            with st.expander(f"✨ Ver Tips de Venta para {codigo_detectado}", expanded=False):
                if st.session_state.es_admin:
                    if st.button("Regenerar argumentos con IA", key="btn_ia_manual_regen"):
                        supabase.table("tips_ia").delete().eq("referencia", codigo_detectado).execute()
                    
                if st.button("Generar argumentos con IA", key="btn_ia_manual"):
                    with st.spinner("Estableciendo sinapsis cognitiva..."):
                        tips_venta, origen_dato = obtener_o_generar_storytelling(codigo_detectado, nombre_detectado, categoria_detectada)
                        st.info(tips_venta)
                        st.caption(f"⚡ {origen_dato}")
        else:
            st.warning("Sin conexiones en la red.")

# ------------------------------------------
# 3. PESTAÑA: RESUMEN EJECUTIVO (PV) (SÓLO ADMIN)
# ------------------------------------------
if tab_resumen_pv is not None:
    with tab_resumen_pv:
        mostrar_resumen_piso_ventas(supabase)

# ------------------------------------------
# 4. PESTAÑA: SCANNER RÁPIDO (SÓLO ADMIN)
# ------------------------------------------
if tab_escaneo is not None:
    with tab_escaneo:
        codigo = st.text_input("Escanea o escribe el código de barras:", key="scan")
        if codigo:
            prod = supabase.table("catalogo_erp").select("*").eq("codigo_limpio", codigo.strip()).execute().data
            if prod:
                p = prod[0]
                st.success(f"Producto: {p.get('descripcion')} | Ref: {p.get('referencia')} | Stock: {p.get('stock_sistema')}")
            else:
                st.warning("Producto no encontrado en el núcleo.")

# ------------------------------------------
# 6. PESTAÑA: GESTIÓN USUARIOS (SÓLO ADMIN)
# ------------------------------------------
if tab_admin_user is not None:
    with tab_admin_user:
        st.subheader("👥 Agregar Usuario")
        with st.form("nuevo_u"):
            col_u1, col_u2 = st.columns(2)
            with col_u1:
                u_n = st.text_input("Usuario")
            with col_u2:
                p_n = st.text_input("Contraseña")
            if st.form_submit_button("Agregar Usuario"):
                if u_n and p_n:
                    try:
                        supabase.table("usuarios").insert({
                            "username": u_n.strip(), 
                            "password": p_n.strip(), 
                            "rol": "asesor",
                            "codigo_erp": u_n.strip(),
                            "nombre_completo": "",
                            "meta_mensual": 0.0
                        }).execute()
                        st.success(f"Usuario {u_n.strip()} agregado correctamente.")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Error al crear usuario: {e}")
                else:
                    st.warning("Debes llenar el usuario y la contraseña.")
                    
        st.markdown("---")
        st.subheader("🗑️ Eliminar Usuario")
        res_usuarios_existentes = supabase.table("usuarios").select("username").execute().data
        lista_usernames = [row["username"] for row in res_usuarios_existentes] if res_usuarios_existentes else []
        
        if lista_usernames:
            with st.form("form_eliminar_usuario"):
                usuario_a_borrar = st.selectbox("Selecciona el usuario que deseas eliminar:", options=lista_usernames)
                btn_borrar = st.form_submit_button("Eliminar Usuario Seleccionado", type="primary")
                
                if btn_borrar:
                    if usuario_a_borrar.lower() == "admin":
                        st.error("⚠️ Por seguridad, no se puede eliminar al usuario administrador principal.")
                    else:
                        try:
                            supabase.table("usuarios").delete().eq("username", usuario_a_borrar).execute()
                            st.success(f"🗑️ El usuario '{usuario_a_borrar}' ha sido eliminado de la red.")
                            st.rerun()
                        except Exception as ex:
                            st.error(f"Error al eliminar usuario: {ex}")
        else:
            st.info("No hay usuarios adicionales registrados.")

        st.markdown("---")
        st.subheader("🔑 Gestionar Contraseñas y Permisos")
        st.info("Visualiza y edita las contraseñas o el rol de acceso directamente en esta tabla. No olvides dar clic en 'Guardar Cambios de Usuarios'.")
        
        res_u_list = supabase.table("usuarios").select("username, password, rol").execute().data
        if res_u_list:
            df_u_pass = pd.DataFrame(res_u_list)
            
            edited_pass_df = st.data_editor(
                df_u_pass,
                column_config={
                    "username": st.column_config.TextColumn("Usuario App", disabled=True),
                    "password": st.column_config.TextColumn("Contraseña (Editable)"),
                    "rol": st.column_config.SelectboxColumn("Rol de Acceso", options=["admin", "asesor"])
                },
                use_container_width=True
            )
            
            if st.button("Guardar Cambios de Usuarios"):
                with st.spinner("Actualizando contraseñas y permisos en el núcleo..."):
                    try:
                        for _, row in edited_pass_df.iterrows():
                            supabase.table("usuarios").update({
                                "password": str(row['password']).strip(),
                                "rol": str(row['rol']).strip()
                            }).eq("username", row['username']).execute()
                        st.success("¡Contraseñas y accesos actualizados correctamente!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Ocurrió un error al guardar: {e}")
