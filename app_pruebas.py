import pandas as pd
import streamlit as st
from supabase import create_client

st.set_page_config(page_title="Sistema de Bodega", page_icon="📦", layout="wide")

# ==========================================
# CONFIGURACIÓN SUPABASE
# ==========================================
try:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    supabase = create_client(url, key)
except Exception as e:
    st.error(f"Error crítico: {e}")
    st.stop()

# ==========================================
# ESTILO Y LOGIN
# ==========================================
st.markdown("""<style>[data-testid="stAppViewContainer"] * {color: #000000 !important;}</style>""", unsafe_allow_html=True)

if "autenticado" not in st.session_state: st.session_state.autenticado = False

if not st.session_state.autenticado:
    st.title("🔐 Acceso")
    with st.form("login_form"):
        u = st.text_input("Usuario")
        p = st.text_input("Contraseña", type="password")
        if st.form_submit_button("Iniciar Sesión"):
            res = supabase.table("usuarios").select("*").eq("username", u.strip()).eq("password", p.strip()).execute()
            if res.data:
                st.session_state.autenticado = True
                st.session_state.usuario_actual = u
                st.rerun()
            else: st.error("Credenciales incorrectas.")
    st.stop()

# ==========================================
# INTERFAZ PRINCIPAL
# ==========================================
st.title("Gestión de Bodega")
st.write(f"Bienvenido, **{st.session_state.get('usuario_actual', 'Usuario')}**")

tab_escaneo, tab_busqueda, tab_admin_erp, tab_admin_user = st.tabs([
    "Scanner Rápido", "Búsqueda Manual", "⚙️ Actualizar Catálogo", "👥 Gestión Usuarios"
])

# ------------------------------------------
# SCANNER RÁPIDO
# ------------------------------------------
with tab_escaneo:
    codigo = st.text_input("Escanea o escribe el código:", key="scan")
    if codigo:
        prod = supabase.table("catalogo_erp").select("*").eq("codigo_limpio", codigo.strip()).execute().data
        if prod:
            p = prod[0]
            ubic = supabase.table("ubicaciones").select("ubicacion").eq("codigo_limpio", codigo.strip()).execute().data
            u_str = ubic[0].get('ubicacion') if ubic else "Sin ubicar"
            st.success(f"Producto: {p.get('descripcion')} | Stock: {p.get('stock_sistema')} | Estante: {u_str}")
        else:
            st.warning("No encontrado.")

# ------------------------------------------
# BÚSQUEDA MANUAL (CON CRUCE DE UBICACIONES)
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
        
        res = query.limit(500).execute()
        if res.data:
            df = pd.DataFrame(res.data)
            ubics = supabase.table("ubicaciones").select("codigo_limpio, ubicacion").execute().data
            if ubics:
                df = df.merge(pd.DataFrame(ubics), on="codigo_limpio", how="left")
            st.dataframe(df)
        else: st.warning("Sin resultados.")

# ------------------------------------------
# ACTUALIZAR CATÁLOGO ERP
# ------------------------------------------
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
        st.success("¡Base de datos actualizada!")

# ------------------------------------------
# GESTIÓN USUARIOS
# ------------------------------------------
with tab_admin_user:
    with st.form("nuevo_u"):
        u_n, p_n = st.text_input("Usuario"), st.text_input("Contraseña", type="password")
        if st.form_submit_button("Agregar"):
            supabase.table("usuarios").insert({"username": u_n, "password": p_n}).execute()
            st.rerun()
    st.dataframe(pd.DataFrame(supabase.table("usuarios").select("*").execute().data))