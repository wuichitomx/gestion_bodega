import pandas as pd
import streamlit as st
from supabase import create_client
from groq import Groq
import requests
from bs4 import BeautifulSoup
import re
import time

st.set_page_config(page_title="Sistema de Bodega", page_icon="📦", layout="wide")

# ==========================================
# CONFIGURACIÓN SUPABASE E IA
# ==========================================
try:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    supabase = create_client(url, key)
    
    # Configuración de la API de IA (Groq)
    cliente_ia = Groq(api_key=st.secrets["GROQ_API_KEY"])
except Exception as e:
    st.error(f"Error crítico de conexión (Revisa Supabase o API Key de IA): {e}")
    st.stop()

# ==========================================
# ESTILO Y LOGIN
# ==========================================
st.markdown("""<style>[data-testid="stAppViewContainer"] * {color: #000000 !important;}</style>""", unsafe_allow_html=True)

if "autenticado" not in st.session_state: 
    st.session_state.autenticado = False

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
                st.session_state.rol_actual = res.data[0].get("rol", "asesor")
                st.rerun()
            else: 
                st.error("Credenciales incorrectas.")
    st.stop()

# ==========================================
# FUNCIONES AUXILIARES DE BÚSQUEDA REAL
# ==========================================
DOMINIOS_BUSQUEDA = ["adidas.com", "adidas.mx"]

def _buscar_url_producto_adidas(referencia, nombre_producto):
    """Paso 1: le pide a la IA SOLO la URL exacta del producto (respuesta corta, sin riesgo de saturarse)."""
    try:
        prompt_busqueda = f"""
        Busca en adidas.com o adidas.mx la página exacta del producto con Número de artículo "{referencia}" (nombre relacionado: "{nombre_producto}").
        Responde ÚNICAMENTE con la URL completa de esa página de producto, sin ningún texto adicional.
        Si no encuentras una coincidencia exacta, responde únicamente con la palabra: NO_ENCONTRADO
        """
        response = cliente_ia.chat.completions.create(
            model="groq/compound-mini",
            messages=[{"role": "user", "content": prompt_busqueda}],
            compound_custom={"tools": {"enabled_tools": ["web_search"]}},
            search_settings={"include_domains": DOMINIOS_BUSQUEDA},
            max_completion_tokens=200,
        )
        texto = response.choices[0].message.content.strip()
        match = re.search(r'https?://[^\s\)\]]+adidas\.(com|mx)[^\s\)\]]*', texto)
        return match.group(0) if match else None
    except Exception:
        return None

def _extraer_detalles_producto(url):
    """Paso 2: descarga la página con Python y extrae solo el texto real (sin todo el HTML/JS)."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
        resp = requests.get(url, headers=headers, timeout=8)
        if resp.status_code != 200:
            return None

        soup = BeautifulSoup(resp.text, "html.parser")
        partes = []

        meta_desc = soup.find("meta", attrs={"name": "description"})
        if meta_desc and meta_desc.get("content"):
            partes.append(meta_desc["content"])

        # Líneas cortas tipo bullet (ajuste, materiales, bolsillos, etc.)
        for li in soup.find_all("li"):
            texto_li = li.get_text(strip=True)
            if texto_li and 3 < len(texto_li) < 150:
                partes.append(f"- {texto_li}")

        texto_final = "\n".join(partes)
        return texto_final[:1800] if texto_final.strip() else None
    except Exception:
        return None

# ==========================================
# FUNCIONES DE IA Y UI (CON CACHÉ EN 'tips_ia')
# ==========================================
def obtener_o_generar_storytelling(referencia, nombre_producto, categoria):
    """
    1. Busca en la tabla dedicada 'tips_ia' usando la referencia.
    2. Si ya existe, lo devuelve al instante sin gastar llamadas a la IA.
    3. Si no existe: busca la URL real del producto, extrae sus detalles con Python,
       y genera el storytelling con esos datos confirmados, guardándolo en 'tips_ia'.
    """
    try:
        # 1. Consultar en la tabla corregida 'tips_ia'
        res_db = supabase.table("tips_ia").select("tips").eq("referencia", referencia.strip()).execute().data
        
        if res_db and len(res_db) > 0 and res_db[0].get("tips"):
            return res_db[0].get("tips"), "⚡ (Obtenido de la tabla 'tips_ia' - Sin costo)"

        # 2. Buscar la ficha real del producto (dos pasos controlados por Python)
        url_producto = _buscar_url_producto_adidas(referencia, nombre_producto)
        detalles_reales = _extraer_detalles_producto(url_producto) if url_producto else None

        if detalles_reales:
            bloque_datos = (
                f"\n\nInformación REAL confirmada de la ficha oficial ({url_producto}) — única fuente válida para especificaciones:\n"
                f"{detalles_reales}\n\n"
                "Redacta 'La Plática Técnica' basándote ÚNICAMENTE en estos datos reales. "
                "No inventes materiales, tecnologías, ni tipo de prenda/calzado que no aparezcan aquí."
            )
            origen_busqueda = "🌐🤖 (Generado con datos reales de la ficha oficial y guardado en 'tips_ia')"
        else:
            bloque_datos = (
                "\n\nNo se logró confirmar la ficha oficial del producto en línea. "
                "Apóyate solo en el Nombre/Descripción y la Categoría del sistema, sin inventar "
                "especificaciones técnicas, materiales ni tecnologías que no puedas confirmar."
            )
            origen_busqueda = "🤖 (No se encontró ficha oficial; generado solo con datos del sistema y guardado en 'tips_ia')"

        # 3. Prompt maestro (reglas de redacción)
        prompt_maestro = f"""
        Eres un asesor experto de ventas de piso de Adidas. Tu lenguaje es coloquial, directo, empático y persuasivo, de tú a tú con el cliente. Cero descripciones robóticas ni de manual técnico.

        Producto a analizar:
        - Código de Referencia (Número de Artículo en adidas.com): {referencia}
        - Nombre / Descripción completa: {nombre_producto}
        - Categoría del sistema (Nivel 1): {categoria}
        {bloque_datos}

        Reglas obligatorias:
        1. Validación Textil vs. Calzado: Evalúa la categoría (Footwear, Apparel o Hardware) usando el Nivel 1 y lo confirmado en los datos reales. Nunca hables de suelas o pisadas si es una prenda textil; en esos casos enfócate en materiales y comodidad reales.
        2. Filtro de Running (regla de oro): Si el calzado es de running, es OBLIGATORIO incluir el rango de kilómetros recomendado (ej. rodajes cortos de 5 a 10 km, o distancias largas) y el nivel de corredor al que va dirigido.
        3. Cultura y Embajadores (Lifestyle): Si es un producto de moda urbana, retro, o de una selección/equipo (ej. ediciones de aniversario), menciona tendencias de streetwear o el orgullo del equipo/selección, y embajadores globales relevantes si aplica (por ejemplo el efecto Bad Bunny u otros artistas pop/urbanos), para dar un argumento aspiracional.
        4. Especialidad Técnica (Performance): Si es para una disciplina específica, habla su idioma técnico real. Ejemplos: Pádel → agarre lateral en pasto sintético; levantamiento de pesas (ej. Dropset) → base rígida y estabilidad; fútbol → tracción y control de balón.
        5. El Gancho Comercial (venta cruzada): El tip final SIEMPRE debe cerrar con una sugerencia de venta cruzada concreta para el piso de venta, pensada para subir las unidades por transacción de forma natural.
        6. Estructura exacta a devolver:
           - **El Rompehielo:** Una historia corta que conecte con la necesidad del cliente (si hay edición especial, selección o embajador confirmado, arráncalo desde ahí).
           - **La Plática Técnica:** Datos reales confirmados, explicados de forma natural (no técnica).
           - **🎯 Tip de Venta / Especialidad:** A quién vendérselo directamente, + la sugerencia de venta cruzada obligatoria.
        """

        # 4. Generación final: ya no necesita herramientas, los datos reales ya vienen incluidos en el prompt
        response = cliente_ia.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[{"role": "user", "content": prompt_maestro}],
            max_completion_tokens=2000,
        )
        nuevo_texto = response.choices[0].message.content
        
        # 5. Guardar el resultado en la tabla 'tips_ia' usando upsert
        supabase.table("tips_ia").upsert({
            "referencia": referencia.strip(),
            "tips": nuevo_texto
        }).execute()
        
        return nuevo_texto, origen_busqueda
        
    except Exception as e:
        mensaje_error = str(e)
        if "429" in mensaje_error or "rate_limit" in mensaje_error.lower() or "quota" in mensaje_error.lower():
            return "⏳ Se alcanzó el límite de consultas a la IA. Por favor intenta de nuevo en unos minutos.", "Error de cuota"
        return f"⚠️ Ocurrió un error al procesar el storytelling: {e}", "Error"

def forzar_regeneracion_tip(referencia):
    """Borra el tip guardado para que la próxima consulta genere uno nuevo con IA."""
    supabase.table("tips_ia").delete().eq("referencia", referencia.strip()).execute()

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
            
            codigo_detectado = df.iloc[0]['referencia']
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

                if st.session_state.get("rol_actual") == "admin":
                    if st.button("🔄 Regenerar tip (admin)", key="btn_regenerar_ia"):
                        forzar_regeneracion_tip(codigo_detectado)
                        with st.spinner("Buscando información actualizada en internet..."):
                            tips_venta, origen_dato = obtener_o_generar_storytelling(
                                codigo_detectado, nombre_detectado, categoria_detectada
                            )
                            st.info(tips_venta)
                            st.caption(f"💡 {origen_dato}")
        else: 
            st.warning("Sin resultados.")

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
        st.success("¡Base de datos actualizada con éxito!")

# ------------------------------------------
# GESTIÓN USUARIOS
# ------------------------------------------
with tab_admin_user:
    with st.form("nuevo_u"):
        u_n, p_n = st.text_input("Usuario"), st.text_input("Contraseña", type="password")
        if st.form_submit_button("Agregar Usuario"):
            supabase.table("usuarios").insert({"username": u_n, "password": p_n}).execute()
            st.rerun()
    st.dataframe(pd.DataFrame(supabase.table("usuarios").select("*").execute().data))