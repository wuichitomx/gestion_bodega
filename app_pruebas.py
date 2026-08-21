import os
import pandas as pd
import streamlit as st
from supabase import create_client
import google.generativeai as genai
import time
from PIL import Image, ImageOps

st.set_page_config(page_title="Sistema de Bodega", page_icon="📦", layout="wide")

# ==========================================
# CONFIGURACIÓN SUPABASE E IA
# ==========================================
try:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    supabase = create_client(url, key)
    
    # Configuración de la API de IA (Gemini)
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
except Exception as e:
    st.error(f"Error crítico de conexión (Revisa Supabase o API Key de IA): {e}")
    st.stop()

# ==========================================
# FUNCIÓN PARA ADAPTAR LOGO A MODO OSCURO
# ==========================================
def obtener_logo_adaptado(ruta_imagen):
    if not os.path.exists(ruta_imagen):
        return None
    try:
        img = Image.open(ruta_imagen).convert("RGBA")
        r, g, b, alpha = img.split()
        rgb_inverted = ImageOps.invert(Image.merge("RGB", (r, g, b)))
        r_inv, g_inv, b_inv = rgb_inverted.split()
        return Image.merge("RGBA", (r_inv, g_inv, b_inv, alpha))
    except Exception:
        return None

# ==========================================
# ESTILO GENERAL
# ==========================================
st.markdown("""
    <style>
    [data-testid="stAppViewContainer"] {
        color: var(--text-color);
    }
    .stAlert, div[data-testid="stExpander"] {
        border-radius: 8px;
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

if not st.session_state.autenticado:
    col_a, col_b, col_c = st.columns([1, 2, 1])
    with col_b:
        logo_blanco = obtener_logo_adaptado("logo_adidas.png")
        if logo_blanco:
            st.image(logo_blanco, width=160)
        
        st.title("🔐 Acceso al Sistema")
        st.caption("v2.0 | Desarrollado por Risal Tech")
        
        with st.form("login_form"):
            u = st.text_input("Usuario")
            p = st.text_input("Contraseña", type="password")
            if st.form_submit_button("Iniciar Sesión"):
                res = supabase.table("usuarios").select("*").eq("username", u.strip()).eq("password", p.strip()).execute()
                if res.data:
                    st.session_state.autenticado = True
                    st.session_state.usuario_actual = u.strip()
                    
                    # Se revisa si en la BD existe la columna 'rol' o si el usuario se llama 'admin'
                    usuario_info = res.data[0]
                    rol = usuario_info.get("rol", "").lower() if "rol" in usuario_info else ""
                    
                    # Es admin si su rol es 'admin' o si su nombre de usuario contiene 'admin'
                    if rol == "admin" or "admin" in u.strip().lower():
                        st.session_state.es_admin = True
                    else:
                        st.session_state.es_admin = False
                        
                    st.rerun()
                else: 
                    st.error("Credenciales incorrectas.")
    st.stop()

# ==========================================
# MENÚ LATERAL Y DERECHOS
# ==========================================
with st.sidebar:
    logo_blanco = obtener_logo_adaptado("logo_adidas.png")
    if logo_blanco:
        st.image(logo_blanco, width=120)
    st.markdown("### Sistema de Bodega")
    st.caption("🚀 **Versión:** 2.0")
    st.caption("👨‍💻 **Desarrollado por:** Risal Tech")
    
    # Botón de cerrar sesión opcional
    if st.button("🚪 Cerrar Sesión"):
        st.session_state.autenticado = False
        st.session_state.es_admin = False
        st.rerun()
        
    st.markdown("---")

# ==========================================
# FUNCIONES DE IA Y UI (CON CACHÉ EN 'tips_ia')
# ==========================================
def obtener_o_generar_storytelling(referencia, nombre_producto, categoria):
    try:
        ref_limpia = str(referencia).split('-')[0].strip()
        
        res_db = supabase.table("tips_ia").select("tips").eq("referencia", ref_limpia).execute().data
        
        if res_db and len(res_db) > 0 and res_db[0].get("tips"):
            return res_db[0].get("tips"), "⚡ (Obtenido de la tabla 'tips_ia' - Sin costo)"
        
        model = genai.GenerativeModel('gemini-3.7-flash')
        
        prompt_maestro = f"""
        Eres un asesor experto de ventas de piso de Adidas. Tu lenguaje es coloquial, directo, muy persuasivo y enfocado en aportar valor real al cliente en tienda.
        
        Genera una tarjeta de venta para este producto:
        - Producto: {nombre_producto}
        - Referencia: {ref_limpia}
        - Categoría del sistema: {categoria}

        REGLAS DE ESTRUCTURA (Sigue este orden exacto):

        1. **Conecta:** 
           - Redacta exactamente 3 oraciones enfocadas en conectar de forma empática y entusiasta con la necesidad del cliente. 
           - Es OBLIGATORIO mencionar embajadores de la marca, futbolistas/clubes (si es jersey), artistas, celebridades, influencers o creadores de contenido reales a quienes se les ha visto usando el producto o la silueta (ej. Bad Bunny, Kendall Jenner, Messi, etc.) para crear una conexión aspiracional inmediata.

        2. **Plática Técnica:** 
           - Proporciona viñetas cortas, muy enfocadas y con datos técnicos reales y específicos del producto (ej. tipo de espuma/amortiguación, si tiene o no placa de carbono, transpirabilidad/material de la capellada, tipo de suela y agarre).
           - Si el producto es de Running: Es OBLIGATORIO incluir en una viñeta el rango de kilómetros recomendado y el nivel del corredor.
           - Si es Lifestyle: Menciona brevemente la tendencia de moda urbana/streetwear asociada.
           - Si es Performance: Enfatiza la ventaja técnica según la disciplina.

        3. **🎯 Sugerencia de Venta Cruzada:** 
           - Menciona únicamente 2 productos complementarios concretos y directos para aumentar el ticket (ej. calcetines de rendimiento, gorra, mochila, producto de limpieza de calzado, etc.).
        """
        
        response = model.generate_content(prompt_maestro)
        nuevo_texto = response.text
        
        supabase.table("tips_ia").upsert({
            "referencia": ref_limpia,
            "tips": nuevo_texto
        }).execute()
        
        return nuevo_texto, "🤖 (Generado por IA por primera vez y guardado en 'tips_ia')"
        
    except Exception as e:
        mensaje_error = str(e)
        if "429" in mensaje_error or "quota" in mensaje_error.lower():
            return "⏳ Se alcanzó temporalmente el límite de consultas a la IA. Intenta de nuevo en unos minutos.", "Error de cuota"
        return f"⚠️ Ocurrió un error al procesar el storytelling: {e}", "Error"

# ==========================================
# INTERFAZ PRINCIPAL CON DERECHOS DE ACCESO
# ==========================================
st.title("Gestión de Bodega")
st.write(f"Bienvenido, **{st.session_state.get('usuario_actual', 'Usuario')}**")

# Definir dinámicamente las pestañas según el nivel de usuario
if st.session_state.es_admin:
    tabs = st.tabs(["Scanner Rápido", "Búsqueda Manual", "⚙️ Actualizar Catálogo", "👥 Gestión Usuarios"])
    tab_escaneo, tab_busqueda, tab_admin_erp, tab_admin_user = tabs[0], tabs[1], tabs[2], tabs[3]
else:
    tabs = st.tabs(["Scanner Rápido", "Búsqueda Manual"])
    tab_escaneo, tab_busqueda = tabs[0], tabs[1]
    tab_admin_erp, tab_admin_user = None, None

# ------------------------------------------
# SCANNER RÁPIDO
# ------------------------------------------
with tab_escaneo:
    st.info("💡 Para resultados enriquecidos con IA, utiliza la pestaña de Búsqueda Manual.")
    codigo = st.text_input("Escanea o escribe el código de barras:", key="scan")
    if codigo:
        prod = supabase.table("catalogo_erp").select("*").eq("codigo_limpio", codigo.strip()).execute().data
        if prod:
            p = prod[0]
            ubic = supabase.table("ubicaciones").select("ubicacion").eq("codigo_limpio", codigo.strip()).execute().data
            u_str = ubic[0].get('ubicacion') if ubic else "Sin ubicar"
            st.success(f"Producto: {p.get('descripcion')} | Ref: {p.get('referencia')} | Stock: {p.get('stock_sistema')} | Estante: {u_str}")
        else:
            st.warning("No encontrado en el catálogo.")

# ------------------------------------------
# BÚSQUEDA MANUAL (TABLA + ASISTENTE IA)
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
            
            ubics = supabase.table("ubicaciones").select("codigo_limpio, ubicacion").execute().data
            if ubics:
                df_ubics = pd.DataFrame(ubics)
                df = df.merge(df_ubics, on="codigo_limpio", how="left")
            else:
                df['ubicacion'] = "Sin ubicar"
                
            df['ubicacion'] = df['ubicacion'].fillna("Sin ubicar")
            
            st.success(f"✅ Se encontraron {len(df)} artículos.")
            
            st.dataframe(df[['codigo_limpio', 'referencia', 'descripcion', 'talla', 'nivel1', 'stock_sistema', 'ubicacion']], use_container_width=True)
            
            ref_raw = df.iloc[0]['referencia']
            codigo_detectado = str(ref_raw).split('-')[0].strip()
            
            nombre_detectado = df.iloc[0]['descripcion']
            categoria_detectada = df.iloc[0]['nivel1'] if 'nivel1' in df.columns else "General"
            
            st.markdown("---")
            st.markdown("#### 🤖 Asistente de Ventas en Piso")
            
            with st.expander(f"✨ Ver Tips de Venta para {codigo_detectado}", expanded=False):
                if st.button("Generar argumentos con IA", key="btn_ia_manual"):
                    with st.spinner("Buscando en la base de conocimientos..."):
                        
                        tips_venta, origen_dato = obtener_o_generar_storytelling(
                            codigo_detectado, nombre_detectado, categoria_detectada
                        )
                        
                        st.info(tips_venta)
                        st.caption(f"💡 {origen_dato}")
        else: 
            st.warning("Sin resultados.")

# ------------------------------------------
# ACTUALIZAR CATÁLOGO ERP (SÓLO ADMIN)
# ------------------------------------------
if tab_admin_erp is not None:
    with tab_admin_erp:
        archivo = st.file_uploader("Subir CSV del ERP", type=["csv"])
        if archivo and st.button("Sincronizar Catálogo"):
            df = pd.read_csv(archivo, skiprows=5, encoding='latin1', dtype=str)
            df_l = pd.DataFrame()
            df_l['codigo_limpio'] = df['CodigoAlterno'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
            df_l['referencia'] = df['Referencia'].astype(str).str.strip()
            df_l['descripcion'] = df['Descripcion'].astype(str).str.strip()
            df_l['talla'] = df['Referencia'].apply(lambda x: str(x).split('-', 1)[1] if '-' in str(x) else "Única")
            df_l['nivel1'] = df['Nivel1'].astype(str).str.strip()
            df_l['nivel2'] = df['Nivel2'].astype(str).str.strip()
            df_l['nivel3'] = df['Nivel3'].astype(str).str.strip()
            df_l['nivel4'] = df['Nivel4'].astype(str).str.strip()
            df_l['stock_sistema'] = pd.to_numeric(df['Cantidad'].astype(str).str.replace(',', ''), errors='coerce').fillna(0).astype(int)
            
            supabase.table("catalogo_erp").upsert(df_l.to_dict(orient="records")).execute()
            st.success("¡Base de datos actualizada con éxito!")

# ------------------------------------------
# GESTIÓN USUARIOS (SÓLO ADMIN)
# ------------------------------------------
if tab_admin_user is not None:
    with tab_admin_user:
        with st.form("nuevo_u"):
            u_n, p_n = st.text_input("Usuario"), st.text_input("Contraseña", type="password")
            if st.form_submit_button("Agregar Usuario"):
                supabase.table("usuarios").insert({"username": u_n, "password": p_n}).execute()
                st.rerun()
        st.dataframe(pd.DataFrame(supabase.table("usuarios").select("*").execute().data))