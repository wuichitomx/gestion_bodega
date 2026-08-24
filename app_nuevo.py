import os
import io
import base64
import pandas as pd
import streamlit as st
import altair as alt
from supabase import create_client
import google.generativeai as genai
from PIL import Image, ImageOps

# Importamos las reglas maestras desde nuestro archivo de configuración
from configuracion_ia import generar_prompt_maestro

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
        st.caption("v3.4 (Neural Core) | Desarrollado por Risal Tech")
        
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
    st.caption("🚀 **Versión:** 3.5 (Neural Core)")
    st.caption(f"👤 **Usuario:** {st.session_state.usuario_actual}")
    
    if st.button("🚪 Cerrar Sesión"):
        st.session_state.autenticado = False
        st.session_state.es_admin = False
        st.session_state.felicitacion_mostrada = False
        st.rerun()
    st.markdown("---")

# ==========================================
# FUNCIONES DE IA
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

# ==========================================
# INTERFAZ PRINCIPAL CON PESTAÑAS
# ==========================================
nombre_usuario_actual = st.session_state.usuario_info.get('nombre_completo', '')
if not nombre_usuario_actual or nombre_usuario_actual == 'None':
    nombre_usuario_actual = st.session_state.usuario_actual

st.title(f"⚡ ¡Bienvenid@, {nombre_usuario_actual}!")

if st.session_state.es_admin:
    tabs = st.tabs(["📊 Dashboard", "🔍 Búsqueda Manual", "📦 Scanner Rápido", "⚙️ Admin & Carga ERP", "👥 Gestión Usuarios"])
    tab_perf, tab_busqueda, tab_escaneo, tab_admin_erp, tab_admin_user = tabs[0], tabs[1], tabs[2], tabs[3], tabs[4]
else:
    tabs = st.tabs(["📊 Dashboard", "🔍 Búsqueda Manual"])
    tab_perf, tab_busqueda = tabs[0], tabs[1]
    tab_escaneo, tab_admin_erp, tab_admin_user = None, None, None

# ------------------------------------------
# 4. PESTAÑA: ADMIN & CARGA ERP (SÓLO ADMIN)
# ------------------------------------------
if tab_admin_erp is not None:
    with tab_admin_erp:
        st.subheader("📥 Cargar Reporte de Ventas Diario del ERP (CSV)")
        archivo_sales = st.file_uploader("Subir CSV de Extracto de Ventas", type=["csv"], key="sales_csv")
        
        if archivo_sales is not None:
            try:
                df_raw = pd.read_csv(archivo_sales, encoding='latin1')
                
                df_raw['Neto_D_num'] = pd.to_numeric(df_raw['Neto_D'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                df_raw['Neto_T_num'] = pd.to_numeric(df_raw['Neto_T'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                df_raw['UPT_D_num'] = pd.to_numeric(df_raw['textbox28'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                df_raw['ATV_D_num'] = pd.to_numeric(df_raw['textbox19'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                df_raw['ASP_D_num'] = pd.to_numeric(df_raw['textbox2'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                
                df_raw['UPT_T_num'] = pd.to_numeric(df_raw['textbox31'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                df_raw['ATV_T_num'] = pd.to_numeric(df_raw['textbox5'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                df_raw['ASP_T_num'] = pd.to_numeric(df_raw['textbox20'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                
                df_raw.to_csv("ventas_diarias_temp.csv", index=False)
                st.session_state.felicitacion_mostrada = False
                
                st.success("✅ Reporte de ventas procesado por la red correctamente.")
                st.info("👉 Ahora puedes ir a la pestaña '📊 Dashboard' para ver los resultados actualizados.")
            except Exception as ex:
                st.error(f"Error al procesar el CSV: {ex}")
        
        # ==========================================
        # SECCIÓN: CARGA DE CATÁLOGO A SUPABASE
        # ==========================================
        st.markdown("---")
        st.subheader("📦 Cargar Catálogo de Inventario al Núcleo")
        st.info("Sube aquí el archivo CSV de tu ERP (RPInv_Extracto_Referencia) para sincronizar la red en Supabase.")

        archivo_catalogo = st.file_uploader("Subir CSV de Catálogo", type=["csv"], key="cat_csv")

        if archivo_catalogo is not None:
            if st.button("⚡ Sincronizar Catálogo en la Nube"):
                try:
                    with st.spinner("Estableciendo sinapsis y sincronizando el núcleo... Esto puede tomar unos segundos."):
                        df_cat = pd.read_csv(archivo_catalogo, encoding='latin1', skiprows=5, dtype=str)
                        
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
                        total_bloques = max(1, -(-total_registros // TAMANO_BLOQUE))

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
# 1. PESTAÑA: PERFORMANCE & KPIS (ACTUALIZADA V3.5)
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

        # ==========================================
        # VISTA EXCLUSIVA PARA EL ADMINISTRADOR
        # ==========================================
        if st.session_state.es_admin:
            st.header("📊 Tablero Gerencial Diario")
            
            # 1. Performance General de Tienda
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
            bars = alt.Chart(df_chart).mark_bar(color='#00D9F5', cornerRadiusTopLeft=6, cornerRadiusTopRight=6).encode(
                x=alt.X('nombre:N', sort=None, title='Asesor', axis=alt.Axis(labelAngle=-45)),
                y=alt.Y('Neto_D_num:Q', title='Venta Neta ($)')
            )
            text = bars.mark_text(align='center', baseline='bottom', dy=-5, color='white').encode(text=alt.Text('Neto_D_num:Q', format='$,.0f'))
            st.altair_chart(bars + text, use_container_width=True)

            st.markdown("---")
            st.subheader("🎯 Radiografía Operativa: KPIs por Asesor vs Promedio Tienda")
            st.caption("🟢 La línea verde punteada indica el promedio general de la tienda.")

            def crear_grafica_kpi(df, campo_y, titulo, promedio_tienda, formato):
                df_sorted = df.sort_values(campo_y, ascending=False)
                base = alt.Chart(df_sorted).encode(x=alt.X('nombre:N', sort=None, title=None, axis=alt.Axis(labelAngle=-45)))
                bar = base.mark_bar(color='#00D9F5', opacity=0.85, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                    y=alt.Y(f'{campo_y}:Q', title=titulo)
                )
                text = bar.mark_text(align='center', baseline='bottom', dy=-5, color='white').encode(
                    text=alt.Text(f'{campo_y}:Q', format=formato)
                )
                rule = alt.Chart(pd.DataFrame({campo_y: [promedio_tienda]})).mark_rule(
                    color='#39FF88', strokeWidth=3, strokeDash=[5, 5]
                ).encode(y=f'{campo_y}:Q')
                return (bar + text + rule).properties(height=320, title=f"{titulo}")

            df_kpis = df_v[['nombre', 'UPT_D_num', 'ATV_D_num', 'ASP_D_num']].copy()
            
            kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
            with kpi_col1:
                st.altair_chart(crear_grafica_kpi(df_kpis, 'UPT_D_num', 'UPT (Unidades x Ticket)', upt_tienda, '.2f'), use_container_width=True)
            with kpi_col2:
                st.altair_chart(crear_grafica_kpi(df_kpis, 'ATV_D_num', 'ATV (Ticket Promedio)', atv_tienda, '$,.0f'), use_container_width=True)
            with kpi_col3:
                st.altair_chart(crear_grafica_kpi(df_kpis, 'ASP_D_num', 'ASP (Precio Promedio)', asp_tienda, '$,.0f'), use_container_width=True)

        # ==========================================
        # VISTA INDIVIDUAL PARA EL ASESOR
        # ==========================================
        else:
            codigo_erp_bd = st.session_state.usuario_info.get('codigo_erp', '')
            if not codigo_erp_bd or codigo_erp_bd == 'None':
                codigo_erp_bd = st.session_state.usuario_actual
                
            usuario_code = str(codigo_erp_bd).strip().lower()
            user_row = df_v[df_v['codigo'].astype(str).str.strip().str.lower() == usuario_code]
            
            if user_row.empty:
                st.header("📊 Tablero de Rendimiento Diario")
                st.warning(f"No se encontraron registros de ventas para el código ERP: '{usuario_code}'.")
            else:
                row_asesor = user_row.iloc[0]
                
                nombre_asesor = row_asesor.get('nombre', usuario_code)
                venta_asesor_neto = row_asesor.get('Neto_D_num', 0.0)
                meta_asesor = row_asesor.get('meta_mensual', 282800.23)
                alcance_asesor_pct = (venta_asesor_neto / meta_asesor) * 100 if meta_asesor > 0 else 0
                falta_asesor = max(0.0, meta_asesor - venta_asesor_neto)
                
                upt_asesor = row_asesor.get('UPT_D_num', 0.0)
                atv_asesor = row_asesor.get('ATV_D_num', 0.0)
                asp_asesor = row_asesor.get('ASP_D_num', 0.0)

                df_ranked = df_v.sort_values('Neto_D_num', ascending=False).reset_index(drop=True)
                if not df_ranked.empty:
                    primer_lugar_nombre = df_ranked.iloc[0]['nombre']
                    primer_lugar_venta = df_ranked.iloc[0]['Neto_D_num']
                    
                    if str(nombre_asesor) == str(primer_lugar_nombre):
                        st.success(f"🏆 ¡Felicidades, {nombre_asesor}! Lideras la red de ventas, continúa así.")
                        if not st.session_state.felicitacion_mostrada:
                            st.balloons()
                            st.session_state.felicitacion_mostrada = True
                    else:
                        diferencia = primer_lugar_venta - venta_asesor_neto
                        st.info(f"⚡ ¡Excelente esfuerzo, {nombre_asesor}! Estás a **${diferencia:,.2f}** de conectar con el primer lugar ({primer_lugar_nombre}).")
                
                st.header("📊 Tablero de Rendimiento Diario")

                col_t, col_a = st.columns(2)
                with col_t:
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

                with col_a:
                    st.subheader(f"👤 MI PERFORMANCE ({nombre_asesor})")
                    st.markdown(f"""
                    <div class="kpi-card">
                        <h4>Mi Meta Mensual: ${meta_asesor:,.2f}</h4>
                        <p><b>Mi Venta Neta:</b> ${venta_asesor_neto:,.2f}</p>
                        <p><b>Falta para Mi Meta:</b> ${falta_asesor:,.2f}</p>
                        <p><b>Mi Alcance:</b> {alcance_asesor_pct:.1f}%</p>
                    </div>
                    """, unsafe_allow_html=True)
                    st.progress(min(1.0, alcance_asesor_pct / 100))

                st.markdown("---")
                st.subheader("🎯 Comparativa de KPIs Operativos")
                
                kpi_c1, kpi_c2, kpi_c3 = st.columns(3)
                with kpi_c1:
                    diff_upt = upt_asesor - upt_tienda
                    st.metric(label="UPT (Unidades x Ticket)", value=f"{upt_asesor:.2f}", delta=f"{diff_upt:+.2f} vs Tienda ({upt_tienda:.2f})", delta_color="normal")
                with kpi_c2:
                    diff_atv = atv_asesor - atv_tienda
                    signo_atv = "+" if diff_atv >= 0 else "-"
                    st.metric(label="ATV (Ticket Promedio)", value=f"${atv_asesor:,.2f}", delta=f"{signo_atv}\\${abs(diff_atv):,.2f} vs Tienda (\\${atv_tienda:,.2f})", delta_color="normal")
                with kpi_c3:
                    diff_asp = asp_asesor - asp_tienda
                    signo_asp = "+" if diff_asp >= 0 else "-"
                    st.metric(label="ASP (Precio Promedio)", value=f"${asp_asesor:,.2f}", delta=f"{signo_asp}\\${abs(diff_asp):,.2f} vs Tienda (\\${asp_tienda:,.2f})", delta_color="normal")

                st.markdown("---")
                st.subheader("📈 Ranking de Ventas Acumuladas por Vendedor ($)")
                
                df_chart_user = df_v[['nombre', 'Neto_D_num']].sort_values('Neto_D_num', ascending=False).reset_index(drop=True)
                df_chart_user['Color'] = df_chart_user['nombre'].apply(lambda x: '#39FF88' if str(x) == str(nombre_asesor) else '#00D9F5')
                
                bars_a = alt.Chart(df_chart_user).mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6).encode(
                    x=alt.X('nombre:N', sort=None, title='Asesor', axis=alt.Axis(labelAngle=-45)),
                    y=alt.Y('Neto_D_num:Q', title='Venta Neta ($)'),
                    color=alt.Color('Color:N', scale=None)
                )
                text_a = bars_a.mark_text(align='center', baseline='bottom', dy=-5, color='white').encode(text=alt.Text('Neto_D_num:Q', format='$,.0f'))
                st.altair_chart(bars_a + text_a, use_container_width=True)

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
# 3. PESTAÑA: SCANNER RÁPIDO (SÓLO ADMIN)
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
# 5. PESTAÑA: GESTIÓN USUARIOS (SÓLO ADMIN)
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