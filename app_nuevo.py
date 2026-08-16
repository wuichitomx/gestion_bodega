import base64
from datetime import datetime
import os
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from supabase import create_client

st.set_page_config(page_title="Sistema de Bodega", layout="wide")

# ==========================================
# CONFIGURACIÓN SUPABASE
# ==========================================
try:
  url = st.secrets["SUPABASE_URL"]
  key = st.secrets["SUPABASE_KEY"]
  supabase = create_client(url, key)
except Exception as e:
  st.error(f"🚨 Error crítico al leer las credenciales: {e}")

# ==========================================
# ESTILO VISUAL (CSS)
# ==========================================
st.markdown(
    """
<style>
[data-testid="stAppViewContainer"] * {
    color: #000000 !important;
}
button[data-baseweb="tab"] p, button[data-baseweb="tab"] div, [data-testid="stTab"] p {
    font-size: 22px !important;
    font-weight: 900 !important;
    color: #000000 !important;
}
h2, h3, h4 {
    font-size: 22px !important;
    font-weight: 800 !important;
    color: #000000 !important;
}
.stTextInput input, .stSelectbox div {
    font-size: 20px !important;
    font-weight: 600 !important;
    color: #000000 !important;
    background-color: #FFFFFF !important;
    border: 2px solid #1E3A8A !important;
    border-radius: 8px !important;
}
.stTextInput label, .stRadio label, .stSelectbox label, .stCheckbox label {
    font-size: 18px !important;
    font-weight: bold !important;
    color: #000000 !important;
}
[data-testid="stMetricValue"] {
    font-size: 1.8rem !important;
    font-weight: 900 !important;
    color: #000000 !important;
}
[data-testid="stMetricLabel"] {
    font-size: 1.1rem !important;
    font-weight: bold !important;
    color: #000000 !important;
}
</style>
""",
    unsafe_allow_html=True,
)

def limpiar_codigo(val):
  if pd.isna(val):
    return ""
  s = str(val).strip()
  if s.endswith(".0"):
    s = s[:-2]
  return s

def registrar_busqueda(usuario, codigo):
  if codigo:
    try:
      supabase.table("bitacora_busquedas").insert({
          "usuario": usuario,
          "codigo": codigo,
          "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
      }).execute()
    except Exception as e:
      pass 

# ==========================================
# CONTROL DE AUTENTICACIÓN (LOGIN)
# ==========================================
if "autenticado" not in st.session_state:
  st.session_state["autenticado"] = False
  st.session_state["usuario"] = ""
  st.session_state["rol"] = ""

if not st.session_state["autenticado"]:
  st.markdown("<h2 style='text-align: center; color: #1E3A8A; margin-top: 50px;'>🔐 Iniciar Sesión - Sistema de Bodega</h2>", unsafe_allow_html=True)
  col1, col2, col3 = st.columns([1, 2, 1])
  with col2:
    with st.form("login_form"):
      usuario_input = st.text_input("Usuario")
      password_input = st.text_input("Contraseña", type="password")
      submit_login = st.form_submit_button("Entrar", use_container_width=True)
      
      if submit_login:
        try:
          res = supabase.table("usuarios").select("rol").eq("username", usuario_input.strip()).eq("password", password_input.strip()).execute()
          row = res.data
        except Exception as e:
          st.error(f"🚨 Error al conectar con la base de datos: {e}")
          row = []
        
        if row:
          st.session_state["autenticado"] = True
          st.session_state["usuario"] = usuario_input.strip()
          st.session_state["rol"] = row[0]["rol"]
          st.rerun()
        else:
          st.error("⚠️ Usuario o contraseña incorrectos.")
  st.stop()

# ==========================================
# FUNCIONES DE CARGA Y DATOS (SUPABASE)
# ==========================================
def extraer_talla(referencia):
  if pd.isna(referencia):
    return "N/A"
  ref_str = str(referencia).strip()
  if "-" in ref_str:
    partes = ref_str.split("-", 1)
    return partes[1].strip()
  return "N/A"

def extraer_codigos_multiples(cadena_raw):
  cadena = limpiar_codigo(cadena_raw)
  if not cadena:
    return []
  l = len(cadena)
  if l == 24:
    return [cadena[:12], cadena[12:]]
  elif l == 26:
    return [cadena[:13], cadena[13:]]
  elif l == 36:
    return [cadena[:12], cadena[12:24], cadena[24:]]
  elif l == 39:
    return [cadena[:13], cadena[13:26], cadena[26:]]
  return [cadena]

@st.cache_data
def cargar_inventario():
  archivo = "RPInv_Extracto_Referencia.csv"
  try:
    df = pd.read_csv(archivo, skiprows=5, encoding="utf-8-sig", dtype=str)
    if "CodigoAlterno" not in df.columns:
      df = pd.read_csv(archivo, encoding="utf-8-sig", dtype=str)
  except Exception:
    df = pd.read_csv(archivo, encoding="utf-8-sig", dtype=str)

  df = df.dropna(subset=["CodigoAlterno"])
  df["CodigoLimpio"] = df["CodigoAlterno"].apply(limpiar_codigo)
  df["Talla"] = df["Referencia"].apply(extraer_talla)

  posibles_cols = ["existencia", "cantidad", "stock", "disponible", "tienda", "cant", "saldo", "unidades"]
  col_existencia = next((c for c in df.columns if any(p in str(c).lower() for p in posibles_cols)), None)

  if col_existencia:
    df["Stock_Sistema"] = pd.to_numeric(df[col_existencia], errors="coerce").fillna(0).astype(int)
  else:
    df["Stock_Sistema"] = 0

  df = df[df["Stock_Sistema"] > 0]
  df = df.drop_duplicates(subset=["CodigoLimpio"])
  return df

def cargar_ubicaciones():
  try:
    response = supabase.table("ubicaciones").select("codigo_limpio, ubicacion, cantidad, fecha").gt("cantidad", 0).limit(10000).execute()
    if response.data:
      df = pd.DataFrame(response.data)
      df = df.rename(columns={"codigo_limpio": "CodigoLimpio", "ubicacion": "Ubicacion", "cantidad": "Cantidad", "fecha": "Fecha"})
      return df
    return pd.DataFrame(columns=["CodigoLimpio", "Ubicacion", "Cantidad", "Fecha"])
  except Exception as e:
    st.error(f"🚨 Error al leer la tabla 'ubicaciones': {e}")
    return pd.DataFrame(columns=["CodigoLimpio", "Ubicacion", "Cantidad", "Fecha"])

def vaciar_estante_completo(ubicacion):
  ub_limpia = ubicacion.upper().strip()
  if ub_limpia:
    try:
      supabase.table("ubicaciones").delete().eq("ubicacion", ub_limpia).execute()
    except Exception as e:
      st.error(f"🚨 Error al vaciar estante: {e}")

def guardar_ubicacion(codigo, nueva_ubicacion, modo_reemplazar=False, productos_reemplazados_set=None):
  codigo_limpio = limpiar_codigo(codigo)
  nueva_ubicacion = nueva_ubicacion.upper().strip()

  if not codigo_limpio or not nueva_ubicacion:
    return 0

  es_primer_escaneo_reemplazo = False
  if modo_reemplazar and productos_reemplazados_set is not None:
    if codigo_limpio not in productos_reemplazados_set:
      es_primer_escaneo_reemplazo = True
      productos_reemplazados_set.add(codigo_limpio)

  fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M")

  try:
    if es_primer_escaneo_reemplazo:
      supabase.table("ubicaciones").delete().eq("codigo_limpio", codigo_limpio).execute()
      supabase.table("ubicaciones").insert({
          "codigo_limpio": codigo_limpio,
          "ubicacion": nueva_ubicacion,
          "cantidad": 1,
          "fecha": fecha_actual
      }).execute()
      cant_actual = 1
    else:
      res = supabase.table("ubicaciones").select("cantidad").eq("codigo_limpio", codigo_limpio).eq("ubicacion", nueva_ubicacion).execute()
      if res.data:
        cant_actual = res.data[0]["cantidad"] + 1
        supabase.table("ubicaciones").update({
            "cantidad": cant_actual,
            "fecha": fecha_actual
        }).eq("codigo_limpio", codigo_limpio).eq("ubicacion", nueva_ubicacion).execute()
      else:
        cant_actual = 1
        supabase.table("ubicaciones").insert({
            "codigo_limpio": codigo_limpio,
            "ubicacion": nueva_ubicacion,
            "cantidad": 1,
            "fecha": fecha_actual
        }).execute()
    return cant_actual
  except Exception as e:
    st.error(f"🚨 Error al guardar ubicación: {e}")
    return 0

def obtener_resumen_producto(codigo_limpio, df_ub):
  match_ub = df_ub[df_ub["CodigoLimpio"] == codigo_limpio]
  if match_ub.empty:
    return "⚠️ Sin asignar", 0

  detalles = [f"{row['Ubicacion']} ({row['Cantidad']} pza{'s' if row['Cantidad'] > 1 else ''})" for _, row in match_ub.iterrows()]
  texto_ub = ", ".join(detalles)
  total_piezas = match_ub["Cantidad"].sum()
  return texto_ub, total_piezas

try:
  df_inv = cargar_inventario()
  datos_cargados = True
except Exception as e:
  datos_cargados = False
  st.error(f"Error al cargar el archivo CSV: {e}")

# ==========================================
# BARRA LATERAL (CONTROLES GLOBALES)
# ==========================================
with st.sidebar:
  st.markdown(f"### 👤 Usuario: {st.session_state['usuario']}")
  st.markdown(f"**Rol:** {st.session_state['rol'].upper()}")
  
  if st.button("🚪 Cerrar Sesión", use_container_width=True):
    st.session_state["autenticado"] = False
    st.session_state["usuario"] = ""
    st.session_state["rol"] = ""
    st.rerun()

  st.markdown("---")
  st.markdown("### ⚙️ Opciones del Sistema")
  if st.button("🔄 Recargar Inventario CSV", use_container_width=True):
    st.cache_data.clear()
    st.rerun()

if os.path.exists("Adidas-logo.png"):
  img_b64 = base64.b64encode(open("Adidas-logo.png", "rb").read()).decode()
  st.markdown(
      f"""
    <div style="display: flex; align-items: center; gap: 20px; margin-bottom: 20px;">
        <img src="data:image/png;base64,{img_b64}" style="width: 110px; height: auto;">
        <h1 style="color: #1E3A8A; font-weight: 900; margin: 0; font-size: 42px; line-height: 1;">Gestión de Bodega</h1>
    </div>
    """,
      unsafe_allow_html=True,
  )
else:
  st.markdown('<h1 style="color: #1E3A8A; font-weight: 900; margin-bottom: 20px;">Gestión de Bodega</h1>', unsafe_allow_html=True)

# ==========================================
# CONTROL DE PESTAÑAS SEGÚN EL ROL
# ==========================================
if st.session_state["rol"] == "asesor":
  tab1, tab2 = st.tabs(["🔍 Escáner Rápido", "🔎 Búsqueda Manual / Ubicación"])
else:
  tab1, tab2, tab3, tab4 = st.tabs(["🔍 Escáner Rápido", "🔎 Búsqueda Manual / Ubicación", "📍 Asignar Ubicaciones", "🛒 Carga de Ventas y Resurtido"])

# ==========================================
# PESTAÑA 1: ESCÁNER EN VIVO
# ==========================================
with tab1:
  st.markdown("### Escáner por Código de Barras en Vivo")
  st.info("💡 **Instrucción:** Centra el código de barras dentro del recuadro. La búsqueda se realizará automáticamente.")

  if "ultimo_codigo" not in st.session_state:
    st.session_state["ultimo_codigo"] = ""

  # Motor Oficial (Conecta con tu carpeta 'escaner' subida a GitHub)
  try:
    escaner_vivo = components.declare_component("escaner", path="escaner")
    codigo_detectado = escaner_vivo(key="camara_oficial")

    if codigo_detectado:
      codigo_limpio_cam = limpiar_codigo(codigo_detectado)
      # Evitamos parpadeos asegurándonos de procesarlo solo si es un código nuevo
      if st.session_state.get("codigo_previo_cam") != codigo_limpio_cam:
          st.session_state["codigo_previo_cam"] = codigo_limpio_cam
          st.session_state["ultimo_codigo"] = codigo_limpio_cam
          registrar_busqueda(st.session_state["usuario"], codigo_limpio_cam)
          st.rerun() # Esto refresca solo los datos, NO el navegador, tu sesión sigue intacta
  except Exception as e:
    st.error("Cargando la cámara... Si no aparece, asegúrate de que la carpeta 'escaner' ya se sincronizó en GitHub.")

  def procesar_escaneo_consulta():
    codigo_leido = limpiar_codigo(st.session_state["barcode_input"])
    st.session_state["ultimo_codigo"] = codigo_leido
    st.session_state["codigo_previo_cam"] = codigo_leido # Sincronizamos
    st.session_state["barcode_input"] = ""
    registrar_busqueda(st.session_state["usuario"], codigo_leido)

  st.text_input(
      "O ingresa el código manualmente / usa tu pistola lectora:",
      key="barcode_input",
      on_change=procesar_escaneo_consulta,
  )

  codigo_buscado = st.session_state["ultimo_codigo"]

  if codigo_buscado and datos_cargados:
    resultado = df_inv[df_inv["CodigoLimpio"] == codigo_buscado]

    if not resultado.empty:
      prod = resultado.iloc[0]
      df_ub = cargar_ubicaciones()

      ubicacion_texto, total_pzas = obtener_resumen_producto(codigo_buscado, df_ub)
      stock_sis = prod["Stock_Sistema"]
      diferencia = total_pzas - stock_sis

      st.success(f"¡Producto Encontrado! (Código: {codigo_buscado})")

      if st.session_state["rol"] == "asesor":
        col_sis = st.columns(1)[0]
        with col_sis:
          st.metric(label="💻 System", value=f"{stock_sis} pza(s)")
        st.warning(f"📍 **UBICACIONES EN BODEGA:** {ubicacion_texto}")
      else:
        col_sis, col_tot, col_dif = st.columns(3)
        with col_sis:
          st.metric(label="💻 SISTEMA (CSV)", value=f"{stock_sis} pza(s)")
        with col_tot:
          st.metric(label="📦 BODEGA (FÍSICO)", value=f"{total_pzas} pza(s)")
        with col_dif:
          st.metric(label="⚠️ DIFERENCIA", value=f"{diferencia} pza(s)")
        st.warning(f"📍 **UBICACIONES EN BODEGA:** {ubicacion_texto}")

      st.divider()

      col1, col2 = st.columns(2)
      with col1:
        cat_val = prod.get('Nivel2', '-')
        if st.session_state["rol"] == "asesor":
          cat_val = str(cat_val)[:12]
        st.write(f"**Categoría:** {cat_val}")
        st.write(f"**Tipo:** {prod.get('Nivel1', '-')}")
        st.write(f"**Línea:** {prod.get('Nivel3', '-')}")
        st.write(f"**Talla:** {prod.get('Talla', '-')}")
      with col2:
        st.write(f"**Público:** {prod.get('Nivel4', '-')}")
        st.write(f"**Referencia:** {prod.get('Referencia', '-')}")

      desc_mostrar = prod.get('Descripcion', '-')
      if st.session_state["rol"] == "asesor":
        desc_mostrar = str(desc_mostrar)[:15]

      st.info(f"**Descripción:** {desc_mostrar}")
    else:
      df_ub = cargar_ubicaciones()
      ubicacion_texto, total_pzas = obtener_resumen_producto(codigo_buscado, df_ub)
      st.warning(f"⚠️ El código **{codigo_buscado}** no está en el catálogo CSV, pero tiene registrada la ubicación: **{ubicacion_texto}**")

# ==========================================
# PESTAÑA 2: BÚSQUEDA MANUAL Y POR ESTANTE
# ==========================================
with tab2:
  st.markdown("### Búsqueda de Productos y Estantes")
  opcion_busqueda = st.radio("Selecciona el tipo de búsqueda:", ["🔤 Por Nombre / Descripción / Referencia", "👟 Por Talla Específica", "🏢 Por Estante / Ubicación"], horizontal=True)
  ver_sin_stock = st.checkbox("Mostrar también registros en ceros (0 en sistema y 0 en bodega)", value=False)

  if opcion_busqueda == "🔤 Por Nombre / Descripción / Referencia":
    busqueda_texto = st.text_input("Escribe el nombre, referencia, palabra clave o código:").strip().lower()
    if busqueda_texto and datos_cargados:
      registrar_busqueda(st.session_state["usuario"], busqueda_texto)
      mask = (
          df_inv["Descripcion"].astype(str).str.lower().str.contains(busqueda_texto, na=False)
          | df_inv["Referencia"].astype(str).str.lower().str.contains(busqueda_texto, na=False)
          | df_inv["Nivel2"].astype(str).str.lower().str.contains(busqueda_texto, na=False)
          | df_inv["CodigoLimpio"].astype(str).str.lower().str.contains(busqueda_texto, na=False)
          | df_inv["Talla"].astype(str).str.lower().str.contains(busqueda_texto, na=False)
      )
      resultados_df = df_inv[mask].copy()
      if not resultados_df.empty:
        df_ub = cargar_ubicaciones()
        resumenes = [{"CodigoLimpio": cod, "Ubicacion": obtener_resumen_producto(cod, df_ub)[0], "TotalPiezas": obtener_resumen_producto(cod, df_ub)[1]} for cod in resultados_df["CodigoLimpio"]]
        merged = pd.merge(resultados_df, pd.DataFrame(resumenes), on="CodigoLimpio", how="left")
        merged["Stock_Sistema"] = merged["Stock_Sistema"].fillna(0).astype(int)
        merged["TotalPiezas"] = merged["TotalPiezas"].fillna(0).astype(int)
        merged["Ubicacion"] = merged["Ubicacion"].fillna("⚠️ Sin asignar")
        merged["Diferencia"] = merged["TotalPiezas"] - merged["Stock_Sistema"]
        if not ver_sin_stock:
          merged = merged[(merged["Stock_Sistema"] > 0) | (merged["TotalPiezas"] > 0)]
        if not merged.empty:
          st.write(f"Se encontraron **{len(merged)}** registro(s):")
          st.dataframe(merged[["CodigoLimpio", "Descripcion", "Referencia", "Talla", "Nivel2", "Stock_Sistema", "TotalPiezas", "Ubicacion"]], use_container_width=True, hide_index=True)
        else:
          st.warning("Todas las existencias están en ceros.")
      else:
        st.warning("No se encontraron coincidencias.")

  elif opcion_busqueda == "👟 Por Talla Específica":
    if datos_cargados:
      tallas_disponibles = sorted([t for t in df_inv["Talla"].unique() if t and t != "N/A"])
      talla_seleccionada = st.selectbox("Selecciona la talla:", ["-- Selecciona una talla --"] + tallas_disponibles)
      if talla_seleccionada and talla_seleccionada != "-- Selecciona una talla --":
        resultados_df = df_inv[df_inv["Talla"].astype(str).str.upper() == talla_seleccionada.upper()].copy()
        if not resultados_df.empty:
          df_ub = cargar_ubicaciones()
          resumenes = [{"CodigoLimpio": cod, "Ubicacion": obtener_resumen_producto(cod, df_ub)[0], "TotalPiezas": obtener_resumen_producto(cod, df_ub)[1]} for cod in resultados_df["CodigoLimpio"]]
          merged = pd.merge(resultados_df, pd.DataFrame(resumenes), on="CodigoLimpio", how="left")
          merged["Stock_Sistema"] = merged["Stock_Sistema"].fillna(0).astype(int)
          merged["TotalPiezas"] = merged["TotalPiezas"].fillna(0).astype(int)
          merged["Ubicacion"] = merged["Ubicacion"].fillna("⚠️ Sin asignar")
          if not ver_sin_stock:
            merged = merged[(merged["Stock_Sistema"] > 0) | (merged["TotalPiezas"] > 0)]
          if not merged.empty:
            st.dataframe(merged[["CodigoLimpio", "Descripcion", "Referencia", "Talla", "Stock_Sistema", "TotalPiezas", "Ubicacion"]], use_container_width=True, hide_index=True)

  else:
    busqueda_ub = st.text_input("Escribe el estante (Ejemplo: H3A):").strip().upper()
    if busqueda_ub:
      df_ub = cargar_ubicaciones()
      if not df_ub.empty:
        df_ub_filtrado = df_ub[df_ub["Ubicacion"].astype(str).str.upper().str.contains(busqueda_ub, na=False)].copy()
        if not df_ub_filtrado.empty:
          merged_loc = pd.merge(df_ub_filtrado, df_inv, on="CodigoLimpio", how="left").fillna("-")
          st.dataframe(merged_loc[["CodigoLimpio", "Descripcion", "Referencia", "Talla", "Cantidad", "Ubicacion"]], use_container_width=True, hide_index=True)
        else:
          st.warning(f"No hay productos en '{busqueda_ub}'.")

# ==========================================
# PESTAÑA 3: ASIGNAR UBICACIONES (ADMIN)
# ==========================================
if st.session_state["rol"] == "admin":
  with tab3:
    st.markdown("### Asignación Rápida por Lote")
    if "ultima_asignacion" not in st.session_state: st.session_state["ultima_asignacion"] = ""
    if "error_asignacion" not in st.session_state: st.session_state["error_asignacion"] = ""
    if "reemplazados_set" not in st.session_state: st.session_state["reemplazados_set"] = set()
    if "contador_sesion_lote" not in st.session_state: st.session_state["contador_sesion_lote"] = 0

    modo = st.radio("Modo:", ["➕ Sumar", "🔄 Reemplazar", "🧹 Inventario Limpio de Estante"])
    es_reem = "🔄 Reemplazar" in modo
    es_inv = "🧹" in modo

    c1, c2 = st.columns([3, 1])
    with c1: ub_in = st.text_input("1. Ubicación:", key="ub_fija").strip()
    with c2:
      st.write(" ")
      st.write(" ")
      if st.button("🗑️ Vaciar", use_container_width=True) and ub_in:
        vaciar_estante_completo(ub_in)
        st.session_state["reemplazados_set"] = set()
        st.session_state["contador_sesion_lote"] = 0
        st.success(f"Estante {ub_in} vaciado.")
        st.rerun()

    def procesar_escaneo_ubicacion():
      raw = st.session_state["barcode_ub_input"]
      cods = extraer_codigos_multiples(raw)
      ub = st.text_input("1. Ubicación:", key="ub_fija").strip() if "ub_fija" in st.session_state else ""
      if not ub:
        st.session_state["error_asignacion"] = "⚠️ Escribe la ubicación."
      elif cods:
        for c in cods:
          guardar_ubicacion(c, ub, modo_reemplazar=es_reem, productos_reemplazados_set=st.session_state["reemplazados_set"])
          st.session_state["contador_sesion_lote"] += 1
        st.session_state["ultima_asignacion"] = f"✅ Registrado en {ub.upper()}"
        st.session_state["error_asignacion"] = ""
      st.session_state["barcode_ub_input"] = ""

    st.text_input("2. Escanea productos:", key="barcode_ub_input", on_change=procesar_escaneo_ubicacion)
    if st.session_state["ultima_asignacion"]: st.success(st.session_state["ultima_asignacion"])

# ==========================================
# PESTAÑA 4: VENTAS (ADMIN)
# ==========================================
if st.session_state["rol"] == "admin":
  with tab4:
    st.markdown("### Cargar Ventas y Resurtido")
    archivo_ventas = st.file_uploader("Sube reporte:", type=["csv", "xlsx"])
    if archivo_ventas:
      df_v = pd.read_csv(archivo_ventas) if archivo_ventas.name.endswith(".csv") else pd.read_excel(archivo_ventas)
      st.dataframe(df_v.head(3), hide_index=True)
      col_c1, col_c2 = st.columns(2)
      col_code = col_c1.selectbox("Columna Código:", df_v.columns)
      col_cant = col_c2.selectbox("Columna Cantidad:", df_v.columns)
      if st.button("🚀 Procesar"):
        st.success("Ventas procesadas.")