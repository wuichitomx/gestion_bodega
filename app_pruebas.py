import os
import io
import base64
import pandas as pd
import streamlit as st
import altair as alt
from supabase import create_client
import google.generativeai as genai
from PIL import Image, ImageOps

# Importamos las reglas maestras desde nuestro nuevo archivo
from configuracion_ia import generar_prompt_maestro

st.set_page_config(page_title="Sistema de Bodega & Performance", page_icon="📦", layout="wide")

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
        
        img = Image.open(ruta_imagen).convert("RGBA")
        r, g, b, alpha = img.split()
        rgb_inverted = ImageOps.invert(Image.merge("RGB", (r, g, b)))
        r_inv, g_inv, b_inv = rgb_inverted.split()
        img_inv = Image.merge("RGBA", (r_inv, g_inv, b_inv, alpha))
        
        buf = io.BytesIO()
        img_inv.save(buf, format="PNG")
        b64_inv = base64.b64encode(buf.getvalue()).decode()
        
        css = f"""
        <style>
        .logo-light-container img {{ display: block; width: {width}px; margin-bottom: 15px; }}
        .logo-dark-container img {{ display: none; width: {width}px; margin-bottom: 15px; }}
        @media (prefers-color-scheme: dark) {{
            .logo-light-container img {{ display: none; }}
            .logo-dark-container img {{ display: block; }}
        }}
        </style>
        <div class="logo-light-container">
            <img src="data:image/png;base64,{b64_orig}">
        </div>
        <div class="logo-dark-container">
            <img src="data:image/png;base64,{b64_inv}">
        </div>
        """
        st.markdown(css, unsafe_allow_html=True)
    except Exception as e:
        pass

# ==========================================
# ESTILO GENERAL
# ==========================================
st.markdown("""
    <style>
    [data-testid="stAppViewContainer"] { color: var(--text-color); }
    .stAlert, div[data-testid="stExpander"] { border-radius: 8px; }
    .kpi-card {
        background-color: rgba(125, 125, 125, 0.1);
        padding: 15px;
        border-radius: 10px;
        border-left: 5px solid #0080FF;
        margin-bottom: 10px;
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
        
        st.title("🔐 Acceso al Sistema")
        st.caption("v3.3 | Desarrollado por Risal Tech")
        
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
    st.markdown("### Risal Tech")
    st.caption("🚀 **Versión:** 3.3 (Performance Hub)")
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
            return res_db[0].get("tips"), "⚡ (Obtenido de la base de datos)"
        
        model = genai.GenerativeModel('gemini-3.7-flash')
        prompt_maestro = generar_prompt_maestro(nombre_producto, ref_limpia, categoria)
        response = model.generate_content(prompt_maestro)
        nuevo_texto = response.text
        
        supabase.table("tips_ia").upsert({"referencia": ref_limpia, "tips": nuevo_texto}).execute()
        return nuevo_texto, "🤖 (Generado por IA con Reglas Locales)"
    except Exception as e:
        return f"⚠️ Error al procesar: {e}", "Error"

# ==========================================
# INTERFAZ PRINCIPAL CON PESTAÑAS
# ==========================================
st.title("Sistema de Bodega & Performance")

if st.session_state.es_admin:
    tabs = st.tabs(["📊 Performance & KPIs", "🔍 Búsqueda Manual", "📦 Scanner Rápido", "⚙️ Admin & Carga ERP", "👥 Gestión Usuarios"])
    tab_perf, tab_busqueda, tab_escaneo, tab_admin_erp, tab_admin_user = tabs[0], tabs[1], tabs[2], tabs[3], tabs[4]
else:
    tabs = st.tabs(["📊 Performance & KPIs", "🔍 Búsqueda Manual", "📦 Scanner Rápido"])
    tab_perf, tab_busqueda, tab_escaneo = tabs[0], tabs[1], tabs[2]
    tab_admin_erp, tab_admin_user = None, None

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
                
                st.success("✅ Reporte de ventas cargado y guardado correctamente.")
                st.info("👉 Ahora puedes ir a la pestaña '📊 Performance & KPIs' para ver los resultados actualizados.")
            except Exception as ex:
                st.error(f"Error al procesar el CSV: {ex}")
        
        # ==========================================
        # NUEVA SECCIÓN: CARGA DE CATÁLOGO A SUPABASE CON TRADUCTOR
        # ==========================================
        st.markdown("---")
        st.subheader("📦 Cargar Catálogo de Inventario a la Nube")
        st.info("Sube aquí el archivo CSV de tu ERP (RPInv_Extracto_Referencia) para actualizar los productos y existencias en Supabase.")

        archivo_catalogo = st.file_uploader("Subir CSV de Catálogo", type=["csv"], key="cat_csv")

        if archivo_catalogo is not None:
            if st.button("🚀 Actualizar Catálogo en Supabase"):
                try:
                    with st.spinner("Procesando y subiendo a la nube... Esto puede tomar un par de minutos dependiendo del tamaño del archivo."):
                        # 1. Leer el archivo como texto puro saltando las 5 líneas de encabezado
                        df_cat = pd.read_csv(archivo_catalogo, encoding='latin1', skiprows=5, dtype=str)
                        
                        # 2. Diccionario Traductor: 'Nombre en ERP' : 'Nombre en Supabase'
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
                        
                        # Aplicar la traducción de nombres
                        df_cat = df_cat.rename(columns=mapeo_columnas)
                        
                        # 3. Extraer la talla automáticamente desde la referencia (ej: 280647-10 saca el '10')
                        if 'referencia' in df_cat.columns:
                            df_cat['talla'] = df_cat['referencia'].apply(lambda x: str(x).split('-', 1)[1] if '-' in str(x) else '')
                        
                        # 4. Quedarnos ÚNICAMENTE con las columnas que Supabase espera, ignorando las extras
                        columnas_esperadas = ['codigo_limpio', 'referencia', 'descripcion', 'talla', 'nivel1', 'nivel2', 'nivel3', 'nivel4', 'stock_sistema']
                        columnas_existentes = [col for col in columnas_esperadas if col in df_cat.columns]
                        df_cat = df_cat[columnas_existentes]
                        
                        # 5. Asegurar que el stock sea formato número
                        if 'stock_sistema' in df_cat.columns:
                            df_cat['stock_sistema'] = pd.to_numeric(df_cat['stock_sistema'], errors='coerce').fillna(0)
                        
                        # 6. Eliminar filas que vengan sin código de barras (generalmente subtotales al final del ERP)
                        if 'codigo_limpio' in df_cat.columns:
                            df_cat = df_cat.dropna(subset=['codigo_limpio'])
                        
                        # 7. Convertir vacíos a nulos de base de datos
                        df_cat = df_cat.astype(object).where(pd.notna(df_cat), None)
                        
                        # 8. Convertir y mandar en paquetes a Supabase
                        registros = df_cat.to_dict(orient="records")
                        
                        # Inyectar los datos en Supabase (Actualiza o Crea nuevo)
                        supabase.table("catalogo_erp").upsert(registros).execute()
                        
                        st.success(f"✅ ¡Catálogo actualizado exitosamente! Se subieron {len(registros)} artículos a la base de datos.")
                except Exception as ex:
                    st.error(f"⚠️ Error al subir el catálogo: {ex}")
        # ==========================================
                
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
                st.success("¡Metas y códigos guardados correctamente en Supabase!")
                st.rerun()

# ------------------------------------------
# 1. PESTAÑA: PERFORMANCE & KPIS
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

        codigo_erp_bd = st.session_state.usuario_info.get('codigo_erp', '')
        if not codigo_erp_bd or codigo_erp_bd == 'None':
            codigo_erp_bd = st.session_state.usuario_actual
            
        usuario_code = str(codigo_erp_bd).strip().lower()
        user_row = df_v[df_v['codigo'].astype(str).str.strip().str.lower() == usuario_code]
        
        if user_row.empty and not st.session_state.es_admin:
            st.header("📊 Tablero de Rendimiento Diario")
            st.warning(f"No se encontraron registros de ventas para el código ERP: '{usuario_code}'.")
        else:
            row_asesor = user_row.iloc[0] if not user_row.empty else df_v.iloc[0]
            
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
                
                if "admin" not in st.session_state.usuario_actual.lower():
                    if str(nombre_asesor) == str(primer_lugar_nombre):
                        st.success(f"🏆 ¡Felicidades, {nombre_asesor}! Eres el primer lugar en ventas, continúa así.")
                        if not st.session_state.felicitacion_mostrada:
                            st.balloons()
                            st.session_state.felicitacion_mostrada = True
                    else:
                        diferencia = primer_lugar_venta - venta_asesor_neto
                        st.info(f"🚀 ¡Excelente esfuerzo, {nombre_asesor}! Estás a **${diferencia:,.2f}** de llegar al primer lugar con {primer_lugar_nombre}.")
            
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
                st.metric(label="UPT (Unidades x Ticket)", value=f"{upt_asesor:.2f}", delta=f"{upt_asesor - upt_tienda:+.2f} vs Tienda ({upt_tienda:.2f})")
            with kpi_c2:
                st.metric(label="ATV (Ticket Promedio)", value=f"${atv_asesor:,.2f}", delta=f"${atv_asesor - atv_tienda:+,.2f} vs Tienda (${atv_tienda:,.2f})")
            with kpi_c3:
                st.metric(label="ASP (Precio Promedio)", value=f"${asp_asesor:,.2f}", delta=f"${asp_asesor - asp_tienda:+,.2f} vs Tienda (${asp_tienda:,.2f})")

            st.markdown("---")
            st.subheader("📈 Ranking de Ventas Acumuladas por Vendedor ($)")
            
            df_chart = df_v[['nombre', 'Neto_D_num']].sort_values('Neto_D_num', ascending=False).reset_index(drop=True)
            df_chart['Color'] = df_chart['nombre'].apply(lambda x: '#0080FF' if str(x) == str(nombre_asesor) else '#7F8C8D')
            
            bars = alt.Chart(df_chart).mark_bar(cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X('nombre:N', sort=None, title='Asesor', axis=alt.Axis(labelAngle=-45)),
                y=alt.Y('Neto_D_num:Q', title='Venta Neta ($)'),
                color=alt.Color('Color:N', scale=None)
            )
            text = bars.mark_text(align='center', baseline='bottom', dy=-5, color='white').encode(text=alt.Text('Neto_D_num:Q', format='$,.0f'))
            
            st.altair_chart(bars + text, use_container_width=True)

# ------------------------------------------
# 2. PESTAÑA: BÚSQUEDA MANUAL
# ------------------------------------------
with tab_busqueda:
    col1, col2, col3 = st.columns(3)
    in_ref = col1.text_input("🔍 Ref/Desc:", key="in_ref")
    in_talla = col2.text_input("📏 Talla:", key="in_talla")
    in_ubic = col3.text_input("🏢 Depto:", key="in_ubic")
    solo_disp = st.checkbox("Ocultar stock en 0", value=True)
        
    if st.button("Buscar Producto") or in_ref or in_talla or in_ubic:
        query = supabase.table("catalogo_erp").select("*")
        if solo_disp: query = query.gt("stock_sistema", 0)
        if in_ref: query = query.or_(f"referencia.ilike.%{in_ref.strip()}%,descripcion.ilike.%{in_ref.strip()}%")
        if in_talla: query = query.like("talla", f"{in_talla.strip()}%")
        if in_ubic: query = query.or_(f"nivel1.ilike.%{in_ubic.strip()}%,nivel2.ilike.%{in_ubic.strip()}%")
        
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

            st.success(f"✅ Se encontraron {len(df)} artículos.")
            st.dataframe(df[['codigo_limpio', 'referencia', 'descripcion', 'talla', 'nivel1', 'stock_sistema', 'ubicacion']], use_container_width=True)
            
            ref_raw = df.iloc[0]['referencia']
            codigo_detectado = str(ref_raw).split('-')[0].strip()
            nombre_detectado = df.iloc[0]['descripcion']
            categoria_detectada = df.iloc[0].get('nivel1', 'General')
            
            st.markdown("---")
            st.markdown("#### 🤖 Asistente de Ventas en Piso")
            with st.expander(f"✨ Ver Tips de Venta para {codigo_detectado}", expanded=False):
                if st.button("Regenerar argumentos con IA", key="btn_ia_manual_regen"):
                    supabase.table("tips_ia").delete().eq("referencia", codigo_detectado).execute()
                    
                if st.button("Generar argumentos con IA", key="btn_ia_manual"):
                    with st.spinner("Buscando en la base de conocimientos..."):
                        tips_venta, origen_dato = obtener_o_generar_storytelling(codigo_detectado, nombre_detectado, categoria_detectada)
                        st.info(tips_venta)
                        st.caption(f"💡 {origen_dato}")
        else:
            st.warning("Sin resultados.")

# ------------------------------------------
# 3. PESTAÑA: SCANNER RÁPIDO
# ------------------------------------------
with tab_escaneo:
    codigo = st.text_input("Escanea o escribe el código de barras:", key="scan")
    if codigo:
        prod = supabase.table("catalogo_erp").select("*").eq("codigo_limpio", codigo.strip()).execute().data
        if prod:
            p = prod[0]
            st.success(f"Producto: {p.get('descripcion')} | Ref: {p.get('referencia')} | Stock: {p.get('stock_sistema')}")
        else:
            st.warning("No encontrado en el catálogo.")

# ------------------------------------------
# 5. PESTAÑA: GESTIÓN USUARIOS (SÓLO ADMIN)
# ------------------------------------------
if tab_admin_user is not None:
    with tab_admin_user:
        with st.form("nuevo_u"):
            u_n, p_n = st.text_input("Usuario"), st.text_input("Contraseña", type="password")
            if st.form_submit_button("Agregar Usuario"):
                supabase.table("usuarios").insert({
                    "username": u_n.strip(), 
                    "password": p_n.strip(), 
                    "rol": "asesor",
                    "codigo_erp": u_n.strip(),
                    "nombre_completo": "",
                    "meta_mensual": 0.0
                }).execute()
                st.rerun()
        res_u_list = supabase.table("usuarios").select("username, rol, codigo_erp, nombre_completo, meta_mensual").execute().data
        if res_u_list:
            st.dataframe(pd.DataFrame(res_u_list), use_container_width=True)