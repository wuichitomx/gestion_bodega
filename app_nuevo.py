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
  st.error(f"🚨 Error crítico al leer las credenciales (Revisa tus Secrets en Streamlit Cloud): {e}")

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
      pass # Aquí no mostramos error para no interrumpir la navegación

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
          st.error(f"🚨 Error al conectar con la base de datos de usuarios: {e}")
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

  posibles_cols = [
      "existencia",
      "cantidad",
      "stock",
      "disponible",
      "tienda",
      "cant",
      "saldo",
      "unidades",
  ]
  col_existencia = next(
      (
          c
          for c in df.columns
          if any(p in str(c).lower() for p in posibles_cols)
      ),
      None,
  )

  if col_existencia:
    df["Stock_Sistema"] = (
        pd.to_numeric(df[col_existencia], errors="coerce")
        .fillna(0)
        .astype(int)
    )
  else:
    df["Stock_Sistema"] = 0

  df = df[df["Stock_Sistema"] > 0]
  df = df.drop_duplicates(subset=["CodigoLimpio"])
  return df

def cargar_ubicaciones():
  try:
    # AQUÍ ESTÁ LA MODIFICACIÓN: .limit(10000)
    response = supabase.table("ubicaciones").select("codigo_limpio, ubicacion, cantidad, fecha").gt("cantidad", 0).limit(10000).execute()
    if response.data:
      df = pd.DataFrame(response.data)
      df = df.rename(columns={"codigo_limpio": "CodigoLimpio", "ubicacion": "Ubicacion", "cantidad": "Cantidad", "fecha": "Fecha"})
      return df
    return pd.DataFrame(columns=["CodigoLimpio", "Ubicacion", "Cantidad", "Fecha"])
  except Exception as e:
    st.error(f"🚨 Error al leer la tabla 'ubicaciones' de Supabase: {e}")
    return pd.DataFrame(columns=["CodigoLimpio", "Ubicacion", "Cantidad", "Fecha"])

def vaciar_estante_completo(ubicacion):
  ub_limpia = ubicacion.upper().strip()
  if ub_limpia:
    try:
      supabase.table("ubicaciones").delete().eq("ubicacion", ub_limpia).execute()
    except Exception as e:
      st.error(f"🚨 Error al vaciar estante: {e}")

def guardar_ubicacion(
    codigo,
    nueva_ubicacion,
    modo_reemplazar=False,
    productos_reemplazados_set=None,
):
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

  detalles = [
      (
          f"{row['Ubicacion']} ({row['Cantidad']}"
          f" pza{'s' if row['Cantidad'] > 1 else ''})"
      )
      for _, row in match_ub.iterrows()
  ]
  texto_ub = ", ".join(detalles)
  total_piezas = match_ub["Cantidad"].sum()
  return texto_ub, total_piezas

try:
  df_inv = cargar_inventario()
  datos_cargados = True
except Exception as e:
  datos_cargados = False
  st.error(f"Error al cargar el archivo CSV de inventario: {e}")

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
  st.markdown(
      '<h1 style="color: #1E3A8A; font-weight: 900; margin-bottom:'
      ' 20px;">Gestión de Bodega</h1>',
      unsafe_allow_html=True,
  )

# ==========================================
# CONTROL DE PESTAÑAS SEGÚN EL ROL
# ==========================================
if st.session_state["rol"] == "asesor":
  tab1, tab2 = st.tabs([
      "🔍 Escáner Rápido",
      "🔎 Búsqueda Manual / Ubicación",
  ])
else:
  tab1, tab2, tab3, tab4 = st.tabs([
      "🔍 Escáner Rápido",
      "🔎 Búsqueda Manual / Ubicación",
      "📍 Asignar Ubicaciones",
      "🛒 Carga de Ventas y Resurtido",
  ])

# ==========================================
# PESTAÑA 1: ESCÁNER EN VIVO
# ==========================================
with tab1:
  st.markdown("### Escáner por Código de Barras en Vivo")
  st.info("💡 **Instrucción:** Centra las líneas del código de barras dentro del recuadro horizontal. ¡Lo leerá y buscará de inmediato sin atascarse!")

  if "ultimo_codigo" not in st.session_state:
    st.session_state["ultimo_codigo"] = ""

  scanner_html = """
    <div style="display: flex; flex-direction: column; align-items: center;">
        <div id="reader" style="width: 100%; max-width: 420px;"></div>
        <p id="result" style="font-weight: bold; color: green; margin-top: 10px; font-size: 18px; text-align: center;"></p>
    </div>

    <script src="https://unpkg.com/html5-qrcode"></script>
    <script>
        let scanningDone = false;

        function onScanSuccess(decodedText, decodedResult) {
            if (scanningDone) return;
            scanningDone = true;
            
            document.getElementById('result').innerText = "¡Código detectado! Procesando...";
            
            try {
                // Obtenemos la URL raíz de tu app para saltarnos la "cajita de cristal"
                let appUrl = new URL(document.referrer);
                appUrl.searchParams.set('scanned_code', decodedText);
                
                // Intento 1: Redirigir toda la página automáticamente
                window.top.location.href = appUrl.href;
                
                // Intento 2: Paracaídas de emergencia a prueba de balas (con target="_top")
                document.getElementById('result').innerHTML = 
                    `<br><a href="${appUrl.href}" target="_top" 
                    style="display: inline-block; padding: 15px 30px; background-color: #2563EB; 
                    color: white; text-decoration: none; border-radius: 10px; font-weight: bold; 
                    font-size: 20px; box-shadow: 0px 4px 6px rgba(0,0,0,0.3);">
                    🔍 BUSCAR CÓDIGO: ${decodedText}
                    </a><br><p style="color:gray; font-size:14px; margin-top:10px;">Toca el botón azul si no avanza solo.</p>`;
            } catch (error) {
                document.getElementById('result').innerText = "Copia el código manualmente: " + decodedText;
            }
        }

        const config = {
            fps: 30,
            qrbox: { width: 380, height: 120 },
            aspectRatio: 1.0,
            formatsToSupport: [
                Html5QrcodeSupportedFormats.EAN_13,
                Html5QrcodeSupportedFormats.CODE_128,
                Html5QrcodeSupportedFormats.UPC_A
            ]
        };

        let html5QrcodeScanner = new Html5QrcodeScanner("reader", config, false);
        html5QrcodeScanner.render(onScanSuccess, (error) => {});
    </script>
  """
  
  components.html(scanner_html, height=480)

  if "scanned_code" in st.query_params:
    codigo_url = limpiar_codigo(st.query_params["scanned_code"])
    
    # Eliminamos el parámetro de la URL para que no se cicle
    del st.query_params["scanned_code"]
    
    if codigo_url:
      st.session_state["ultimo_codigo"] = codigo_url
      st.session_state["barcode_input"] = ""  # Limpiamos el recuadro por precaución
      registrar_busqueda(st.session_state["usuario"], codigo_url)
      st.rerun()
  def procesar_escaneo_consulta():
    codigo_leido = limpiar_codigo(st.session_state["barcode_input"])
    st.session_state["ultimo_codigo"] = codigo_leido
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

      ubicacion_texto, total_pzas = obtener_resumen_producto(
          codigo_buscado, df_ub
      )
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
      ubicacion_texto, total_pzas = obtener_resumen_producto(
          codigo_buscado, df_ub
      )
      st.warning(
          f"⚠️ El código **{codigo_buscado}** no está en el catálogo CSV, pero"
          f" tiene registrada la ubicación: **{ubicacion_texto}**"
      )

# ==========================================
# PESTAÑA 2: BÚSQUEDA MANUAL Y POR ESTANTE
# ==========================================
with tab2:
  st.markdown("### Búsqueda de Productos y Estantes")

  opcion_busqueda = st.radio(
      "Selecciona el tipo de búsqueda:",
      [
          "🔤 Por Nombre / Descripción / Referencia",
          "👟 Por Talla Específica",
          "🏢 Por Estante / Ubicación",
      ],
      horizontal=True,
  )

  ver_sin_stock = st.checkbox(
      "Mostrar también registros en ceros (0 en sistema y 0 en bodega)",
      value=False,
  )

  if opcion_busqueda == "🔤 Por Nombre / Descripción / Referencia":
    busqueda_texto = (
        st.text_input("Escribe el nombre, referencia, palabra clave o código:")
        .strip()
        .lower()
    )

    if busqueda_texto and datos_cargados:
      registrar_busqueda(st.session_state["usuario"], busqueda_texto)
      mask = (
          df_inv["Descripcion"]
          .astype(str)
          .str.lower()
          .str.contains(busqueda_texto, na=False)
          | df_inv["Referencia"]
          .astype(str)
          .str.lower()
          .str.contains(busqueda_texto, na=False)
          | df_inv["Nivel2"]
          .astype(str)
          .str.lower()
          .str.contains(busqueda_texto, na=False)
          | df_inv["CodigoLimpio"]
          .astype(str)
          .str.lower()
          .str.contains(busqueda_texto, na=False)
          | df_inv["Talla"]
          .astype(str)
          .str.lower()
          .str.contains(busqueda_texto, na=False)
      )
      resultados_df = df_inv[mask].copy()

      if not resultados_df.empty:
        df_ub = cargar_ubicaciones()

        resumenes = []
        for cod in resultados_df["CodigoLimpio"]:
          ub_txt, tot_pzas = obtener_resumen_producto(cod, df_ub)
          resumenes.append({
              "CodigoLimpio": cod,
              "Ubicacion": ub_txt,
              "TotalPiezas": tot_pzas,
          })

        df_resumenes = pd.DataFrame(resumenes)
        merged = pd.merge(
            resultados_df,
            df_resumenes,
            on="CodigoLimpio",
            how="left",
            suffixes=("", "_ub"),
        )

        merged["Stock_Sistema"] = merged["Stock_Sistema"].fillna(0).astype(int)
        merged["TotalPiezas"] = merged["TotalPiezas"].fillna(0).astype(int)
        merged["Ubicacion"] = merged["Ubicacion"].fillna("⚠️ Sin asignar")
        merged["Diferencia"] = merged["TotalPiezas"] - merged["Stock_Sistema"]

        if not ver_sin_stock:
          merged = merged[
              (merged["Stock_Sistema"] > 0) | (merged["TotalPiezas"] > 0)
          ]

        if not merged.empty:
          st.write(
              f"Se encontraron **{len(merged)}** registro(s) con existencias:"
          )

          if st.session_state["rol"] == "asesor":
            merged["Descripcion"] = merged["Descripcion"].astype(str).str.slice(0, 15)
            merged["Nivel2"] = merged["Nivel2"].astype(str).str.slice(0, 12)
            columnas_mostrar = [
                "Descripcion",
                "Referencia",
                "Talla",
                "Nivel2",
                "Stock_Sistema",
                "Ubicacion",
            ]
            renombres = {
                "Descripcion": "Descripción",
                "Referencia": "Referencia",
                "Talla": "Talla",
                "Nivel2": "Categoría",
                "Stock_Sistema": "System",
                "Ubicacion": "Ubicaciones Bodega",
            }
          else:
            columnas_mostrar = [
                "CodigoLimpio",
                "Descripcion",
                "Referencia",
                "Talla",
                "Nivel2",
                "Stock_Sistema",
                "TotalPiezas",
                "Diferencia",
                "Ubicacion",
            ]
            renombres = {
                "CodigoLimpio": "Código",
                "Descripcion": "Descripción",
                "Referencia": "Referencia",
                "Talla": "Talla",
                "Nivel2": "Categoría",
                "Stock_Sistema": "Sistema (CSV)",
                "TotalPiezas": "Bodega (Físico)",
                "Diferencia": "Diferencia",
                "Ubicacion": "Ubicaciones Bodega",
            }

          st.dataframe(
              merged[columnas_mostrar].rename(columns=renombres),
              use_container_width=True,
              hide_index=True,
          )
        else:
          st.warning(
              "Se encontraron coincidencias en el catálogo, pero todas las"
              " tallas están en 0."
          )
      else:
        st.warning("No se encontraron productos con ese término de búsqueda.")

  elif opcion_busqueda == "👟 Por Talla Específica":
    if datos_cargados:
      tallas_disponibles = sorted(
          [t for t in df_inv["Talla"].unique() if t and t != "N/A"]
      )
      talla_seleccionada = st.selectbox(
          "Selecciona o escribe la talla:",
          ["-- Selecciona una talla --"] + tallas_disponibles,
      )

      if (
          talla_seleccionada
          and talla_seleccionada != "-- Selecciona una talla --"
      ):
        resultados_df = df_inv[
            df_inv["Talla"].astype(str).str.upper()
            == talla_seleccionada.upper()
        ].copy()

        if not resultados_df.empty:
          df_ub = cargar_ubicaciones()

          resumenes = []
          for cod in resultados_df["CodigoLimpio"]:
            ub_txt, tot_pzas = obtener_resumen_producto(cod, df_ub)
            resumenes.append({
                "CodigoLimpio": cod,
                "Ubicacion": ub_txt,
                "TotalPiezas": tot_pzas,
            })

          df_resumenes = pd.DataFrame(resumenes)
          merged = pd.merge(
              resultados_df,
              df_resumenes,
              on="CodigoLimpio",
              how="left",
              suffixes=("", "_ub"),
          )

          merged["Stock_Sistema"] = (
              merged["Stock_Sistema"].fillna(0).astype(int)
          )
          merged["TotalPiezas"] = merged["TotalPiezas"].fillna(0).astype(int)
          merged["Ubicacion"] = merged["Ubicacion"].fillna("⚠️ Sin asignar")
          merged["Diferencia"] = (
              merged["TotalPiezas"] - merged["Stock_Sistema"]
          )

          if not ver_sin_stock:
            merged = merged[
                (merged["Stock_Sistema"] > 0) | (merged["TotalPiezas"] > 0)
            ]

          if not merged.empty:
            st.write(
                f"Se encontraron **{len(merged)}** producto(s) en talla"
                f" **'{talla_seleccionada}'**:"
            )

            if st.session_state["rol"] == "asesor":
              merged["Descripcion"] = merged["Descripcion"].astype(str).str.slice(0, 15)
              merged["Nivel2"] = merged["Nivel2"].astype(str).str.slice(0, 12)
              columnas_mostrar = [
                  "Descripcion",
                  "Referencia",
                  "Talla",
                  "Nivel2",
                  "Stock_Sistema",
                  "Ubicacion",
              ]
              renombres = {
                  "Descripcion": "Descripción",
                  "Referencia": "Referencia",
                  "Talla": "Talla",
                  "Nivel2": "Categoría",
                  "Stock_Sistema": "System",
                  "Ubicacion": "Ubicaciones Bodega",
              }
            else:
              columnas_mostrar = [
                  "CodigoLimpio",
                  "Descripcion",
                  "Referencia",
                  "Talla",
                  "Nivel2",
                  "Stock_Sistema",
                  "TotalPiezas",
                  "Diferencia",
                  "Ubicacion",
              ]
              renombres = {
                  "CodigoLimpio": "Código",
                  "Descripcion": "Descripción",
                  "Referencia": "Referencia",
                  "Talla": "Talla",
                  "Nivel2": "Categoría",
                  "Stock_Sistema": "Sistema (CSV)",
                  "TotalPiezas": "Bodega (Físico)",
                  "Diferencia": "Diferencia",
                  "Ubicacion": "Ubicaciones Bodega",
              }

            st.dataframe(
                merged[columnas_mostrar].rename(columns=renombres),
                use_container_width=True,
                hide_index=True,
            )
          else:
            st.warning(
                f"No hay piezas con existencias para la talla '{talla_seleccionada}'."
            )
        else:
          st.warning(
              f"No hay productos registrados con la talla '{talla_seleccionada}'."
          )

  else:
    busqueda_ub = (
        st.text_input("Escribe el nombre del estante o ubicación (Ejemplo: S5):")
        .strip()
        .upper()
    )

    if busqueda_ub:
      df_ub = cargar_ubicaciones()
      if not df_ub.empty:
        mask_ub = (
            df_ub["Ubicacion"]
            .astype(str)
            .str.upper()
            .str.contains(busqueda_ub, na=False)
        )
        df_ub_filtrado = df_ub[mask_ub].copy()

        if not df_ub_filtrado.empty:
          if datos_cargados:
            merged_loc = pd.merge(
                df_ub_filtrado,
                df_inv,
                on="CodigoLimpio",
                how="left",
                suffixes=("", "_cat"),
            )
            merged_loc["Descripcion"] = merged_loc["Descripcion"].fillna(
                "⚠️ Sin registro en Catálogo CSV"
            )
            merged_loc["Referencia"] = merged_loc["Referencia"].fillna("-")
            merged_loc["Talla"] = merged_loc["Talla"].fillna("-")
            merged_loc["Nivel2"] = merged_loc["Nivel2"].fillna("-")
            merged_loc["Stock_Sistema"] = (
                merged_loc["Stock_Sistema"].fillna(0).astype(int)
            )
          else:
            merged_loc = df_ub_filtrado
            merged_loc["Descripcion"] = "Sin catálogo"
            merged_loc["Referencia"] = "-"
            merged_loc["Talla"] = "-"
            merged_loc["Nivel2"] = "-"
            merged_loc["Stock_Sistema"] = 0

          total_pzas_estante = merged_loc["Cantidad"].sum()
          modelos_unicos = len(merged_loc)

          if st.session_state["rol"] == "asesor":
            merged_loc["Descripcion"] = merged_loc["Descripcion"].astype(str).str.slice(0, 15)
            merged_loc["Nivel2"] = merged_loc["Nivel2"].astype(str).str.slice(0, 12)
            col_m1 = st.columns(1)[0]
            with col_m1:
              st.metric(
                  "🏷️ MODELOS / CÓDIGOS DIFERENTES", f"{modelos_unicos} modelos"
              )
            
            columnas_estante = [
                "Descripcion",
                "Referencia",
                "Talla",
                "Nivel2",
                "Stock_Sistema",
                "Ubicacion",
            ]
            renombres_est = {
                "Descripcion": "Descripción",
                "Referencia": "Referencia",
                "Talla": "Talla",
                "Nivel2": "Categoría",
                "Stock_Sistema": "System",
                "Ubicacion": "Estante",
            }
          else:
            merged_loc["Diferencia"] = (
                merged_loc["Cantidad"] - merged_loc["Stock_Sistema"]
            )
            col_m1, col_m2 = st.columns(2)
            with col_m1:
              st.metric(
                  "📦 TOTAL PIEZAS FÍSICAS EN ESTANTE",
                  f"{total_pzas_estante} pzas",
              )
            with col_m2:
              st.metric(
                  "🏷️ MODELOS / CÓDIGOS DIFERENTES", f"{modelos_unicos} modelos"
              )

            columnas_estante = [
                "CodigoLimpio",
                "Descripcion",
                "Referencia",
                "Talla",
                "Nivel2",
                "Stock_Sistema",
                "Cantidad",
                "Diferencia",
                "Ubicacion",
            ]
            renombres_est = {
                "CodigoLimpio": "Código",
                "Descripcion": "Descripción",
                "Referencia": "Referencia",
                "Talla": "Talla",
                "Nivel2": "Categoría",
                "Stock_Sistema": "Sistema (CSV)",
                "Cantidad": "Bodega (Físico)",
                "Diferencia": "Diferencia Estante vs Sistema",
                "Ubicacion": "Estante",
            }

          st.divider()
          st.dataframe(
              merged_loc[columnas_estante].rename(columns=renombres_est),
              use_container_width=True,
              hide_index=True,
          )
        else:
          st.warning(
              "No hay productos registrados en el estante o ubicación"
              f" '{busqueda_ub}'."
          )
      else:
        st.info("Aún no se han registrado ubicaciones en la bodega.")

# ==========================================
# PESTAÑA 3: ASIGNAR UBICACIONES EN LOTE (Solo ADMIN)
# ==========================================
if st.session_state["rol"] == "admin":
  with tab3:
    st.markdown("### Asignación Rápida por Lote (Conteo de Piezas)")

    if "ultima_asignacion" not in st.session_state:
      st.session_state["ultima_asignacion"] = ""
    if "error_asignacion" not in st.session_state:
      st.session_state["error_asignacion"] = ""
    if "reemplazados_set" not in st.session_state:
      st.session_state["reemplazados_set"] = set()
    if "prev_ub_fija" not in st.session_state:
      st.session_state["prev_ub_fija"] = ""
    if "prev_modo" not in st.session_state:
      st.session_state["prev_modo"] = ""
    if "contador_sesion_lote" not in st.session_state:
      st.session_state["contador_sesion_lote"] = 0

    modo = st.radio(
        "Modo de asignación:",
        [
            "➕ Sumar a las ubicaciones existentes",
            "🔄 Reemplazar ubicaciones anteriores de los productos escaneados",
            (
                "🧹 Inventario de Estante (Vaciar todo el estante al iniciar y"
                " contar de cero)"
            ),
        ],
        horizontal=False,
    )

    es_reemplazo_producto = "🔄 Reemplazar ubicaciones anteriores" in modo
    es_inventario_estante = "🧹 Inventario de Estante" in modo

    col_input_ub, col_btn_vaciar = st.columns([3, 1])

    with col_input_ub:
      ub_actual_input = st.text_input(
          "1. Escribe la ubicación actual (Ejemplo: S5, T1, Pasillo 2):",
          key="ubicacion_fija_input",
      ).strip()

    with col_btn_vaciar:
      st.write(" ")
      st.write(" ")
      if st.button("🗑️ Vaciar este estante", use_container_width=True):
        if ub_actual_input:
          vaciar_estante_completo(ub_actual_input)
          st.session_state["reemplazados_set"] = set()
          st.session_state["contador_sesion_lote"] = 0
          st.session_state["ultima_asignacion"] = (
              f"🧹 El estante **'{ub_actual_input.upper()}'** ha sido vaciado por"
              " completo. Ahora está en 0 piezas."
          )
          st.session_state["error_asignacion"] = ""
          st.rerun()
        else:
          st.session_state["error_asignacion"] = (
              "⚠️ Escribe primero el nombre del estante para vaciarlo."
          )

    if ub_actual_input and (
        ub_actual_input != st.session_state["prev_ub_fija"]
        or modo != st.session_state["prev_modo"]
    ):
      st.session_state["reemplazados_set"] = set()
      st.session_state["contador_sesion_lote"] = 0
      if es_inventario_estante and ub_actual_input:
        vaciar_estante_completo(ub_actual_input)
        st.session_state["ultima_asignacion"] = (
            f"🧹 Se inició inventario limpio para **'{ub_actual_input.upper()}'**."
            " Estante listo en 0 piezas."
        )
      st.session_state["prev_ub_fija"] = ub_actual_input
      st.session_state["prev_modo"] = modo

    def procesar_escaneo_ubicacion():
      raw_input = st.session_state["barcode_ub_input"]
      codigos_detectados = extraer_codigos_multiples(raw_input)
      ub_actual = st.session_state["ubicacion_fija_input"].strip()

      if not ub_actual:
        st.session_state["error_asignacion"] = (
            "⚠️ Primero debes escribir una ubicación en el recuadro superior."
        )
        st.session_state["ultima_asignacion"] = ""
      elif codigos_detectados:
        cant_registrada = 0
        for cod in codigos_detectados:
          cant_registrada = guardar_ubicacion(
              cod,
              ub_actual,
              modo_reemplazar=es_reemplazo_producto,
              productos_reemplazados_set=st.session_state["reemplazados_set"],
          )
          st.session_state["contador_sesion_lote"] += 1

        if len(codigos_detectados) > 1:
          st.session_state["ultima_asignacion"] = (
              f"⚡ ¡Doble escaneo detectado y separado! Se registraron"
              f" **{len(codigos_detectados)} piezas** en **'{ub_actual.upper()}'**."
          )
        else:
          st.session_state["ultima_asignacion"] = (
              f"✅ Código **{codigos_detectados[0]}** en **'{ub_actual.upper()}'**"
              f" (Pieza #{cant_registrada} de este modelo | Total en ráfaga:"
              f" {st.session_state['contador_sesion_lote']} piezas)"
          )

        st.session_state["error_asignacion"] = ""

      st.session_state["barcode_ub_input"] = ""

    st.text_input(
        "2. Apunta la pistola y dispara a los productos uno por uno:",
        key="barcode_ub_input",
        on_change=procesar_escaneo_ubicacion,
    )

    if st.session_state["contador_sesion_lote"] > 0:
      st.info(
          "📊 Disparos confirmados en esta ráfaga actual:"
          f" **{st.session_state['contador_sesion_lote']} piezas**."
      )

    if st.session_state["error_asignacion"]:
      st.warning(st.session_state["error_asignacion"])

    if st.session_state["ultima_asignacion"]:
      st.success(st.session_state["ultima_asignacion"])


# ==========================================
# PESTAÑA 4: CARGA DE VENTAS Y RESURTIDO (Solo ADMIN)
# ==========================================
if st.session_state["rol"] == "admin":
  with tab4:
    st.markdown("### Cargar Reporte Diario de Ventas (Descuento y Resurtido)")
    st.write(
        "Al procesar el archivo, se descontarán primero las piezas del **PISO**"
        " de ventas. Para cada pieza que salga de piso, el sistema sugerirá de"
        " qué estante de **Bodega** tomar el resurtido."
    )

    archivo_ventas = st.file_uploader(
        "Sube tu archivo de ventas (CSV o Excel):", type=["csv", "xlsx"]
    )

    if archivo_ventas:
      try:
        if archivo_ventas.name.endswith(".csv"):
          df_v = pd.read_csv(archivo_ventas)
        else:
          df_v = pd.read_excel(archivo_ventas)

        st.write("**Vista previa del archivo cargado:**")
        st.dataframe(df_v.head(3), hide_index=True)

        col_c1, col_c2 = st.columns(2)
        col_code = col_c1.selectbox(
            "Selecciona la columna del Código / SKU:", df_v.columns
        )
        col_cant = col_c2.selectbox(
            "Selecciona la columna de Cantidad Vendida:", df_v.columns
        )

        if st.button("🚀 Procesar Ventas y Generar Resurtido", type="primary"):
          df_ub_local = cargar_ubicaciones()
          reporte_resurtido = []

          for _, row in df_v.iterrows():
            codigo = limpiar_codigo(row[col_code])
            try:
              cant_vendida = int(row[col_cant])
            except:
              cant_vendida = 0

            if not codigo or cant_vendida <= 0:
              continue

            mask_piso = (df_ub_local["CodigoLimpio"] == codigo) & (
                df_ub_local["Ubicacion"] == "PISO"
            )

            if mask_piso.any():
              cant_piso = df_ub_local.loc[mask_piso, "Cantidad"].values[0]
              desc_piso = min(cant_piso, cant_vendida)

              if cant_piso > desc_piso:
                df_ub_local.loc[mask_piso, "Cantidad"] -= desc_piso
              else:
                df_ub_local = df_ub_local[~mask_piso]

              cant_restante = cant_vendida - desc_piso

              bodega_items = df_ub_local[
                  (df_ub_local["CodigoLimpio"] == codigo)
                  & (df_ub_local["Ubicacion"] != "PISO")
              ]
              estante_sugerido = (
                  bodega_items.iloc[0]["Ubicacion"]
                  if not bodega_items.empty
                  else "❌ AGOTADO EN BODEGA"
              )

              desc_prod, talla_prod = "Sin Catálogo", "N/A"
              if datos_cargados:
                match = df_inv[df_inv["CodigoLimpio"] == codigo]
                if not match.empty:
                  desc_prod = match.iloc[0].get("Descripcion", "Sin Descripción")
                  talla_prod = match.iloc[0].get("Talla", "N/A")

              reporte_resurtido.append({
                  "Código": codigo,
                  "Descripción": desc_prod,
                  "Talla": talla_prod,
                  "Piezas Vendidas de Piso": desc_piso,
                  "Tomar de Estante": estante_sugerido,
                  "Destino": "PISO DE VENTAS",
              })
            else:
              cant_restante = cant_vendida

            if cant_restante > 0:
              mask_bodega = (df_ub_local["CodigoLimpio"] == codigo) & (
                  df_ub_local["Ubicacion"] != "PISO"
              )
              for idx in df_ub_local[mask_bodega].index:
                if cant_restante <= 0:
                  break
                cant_bod = df_ub_local.loc[idx, "Cantidad"]
                desc_bod = min(cant_bod, cant_restante)

                if cant_bod > desc_bod:
                  df_ub_local.loc[idx, "Cantidad"] -= desc_bod
                else:
                  df_ub_local.drop(idx)
                cant_restante -= desc_bod

          try:
            supabase.table("ubicaciones").delete().neq("codigo_limpio", "BORRAR_TODO_FAKE").execute()
            for _, row in df_ub_local.iterrows():
              if row["Cantidad"] > 0:
                supabase.table("ubicaciones").insert({
                    "codigo_limpio": row["CodigoLimpio"],
                    "ubicacion": row["Ubicacion"],
                    "cantidad": int(row["Cantidad"]),
                    "fecha": datetime.now().strftime("%Y-%m-%d %H:%M")
                }).execute()
            
            st.success("✅ Ventas procesadas correctamente y base de datos actualizada.")
          except Exception as e:
            st.error(f"🚨 Error al actualizar la base de datos con el resurtido: {e}")

          if reporte_resurtido:
            st.markdown("### 📋 Lista de Resurtido para Bodega")
            df_res = pd.DataFrame(reporte_resurtido)
            st.dataframe(df_res, use_container_width=True, hide_index=True)

            csv_res = df_res.to_csv(index=False).encode("utf-8")
            st.download_button(
                "📥 Descargar Lista de Resurtido (CSV)",
                csv_res,
                "Lista_Resurtido.csv",
                "text/csv",
            )
          else:
            st.info(
                "No se requirió resurtido a piso (las ventas salieron directamente"
                " de bodega o no había stock en piso)."
            )

          st.cache_data.clear()

      except Exception as e:
        st.error(f"Error al procesar el archivo: {e}")