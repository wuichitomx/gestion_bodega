import pandas as pd
import streamlit as st
from supabase import create_client

# ==========================================
# CONFIGURACIÓN DE LA PÁGINA
# ==========================================
st.set_page_config(page_title="Cruce Inventario 2024", page_icon="📅", layout="wide")

# ==========================================
# CONEXIÓN A SUPABASE
# ==========================================
try:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    supabase = create_client(url, key)
except Exception as e:
    st.error(f"Error de conexión a Supabase. Revisa tu archivo .streamlit/secrets.toml: {e}")
    st.stop()

# ==========================================
# INTERFAZ PRINCIPAL
# ==========================================
st.title("📅 Cruce Especial: Producto 2024 vs Tienda Física")
st.markdown("""
Esta herramienta temporal te permite subir el Excel de la temporada 2024 y cruzarlo. 
Te mostrará **TODO el listado del Excel**, indicando la ubicación física de los que sí están escaneados, 
y marcando como "Sin ubicación" los que no se encuentren en la tienda.
""")
st.divider()

# Subida del archivo Excel
archivo_2024 = st.file_uploader("Sube el archivo PRODUCTO 2024 (Excel)", type=["xlsx", "xls"])

if archivo_2024 is not None:
    if st.button("🔍 Iniciar Cruce Completo", type="primary"):
        with st.spinner("Conectando con Supabase y cruzando códigos..."):
            try:
                # 1. Leer el archivo Excel
                df_2024 = pd.read_excel(archivo_2024)
                
                if 'EAN' not in df_2024.columns:
                    st.error("⚠️ El archivo subido no contiene la columna 'EAN'. Por favor revisa el formato.")
                    st.stop()
                    
                # Limpiar EAN
                df_2024['codigo_limpio'] = df_2024['EAN'].astype(str).str.replace(r'\.0$', '', regex=True).str.strip()
                total_excel = len(df_2024['codigo_limpio'].unique())
                
                # 2. Descargar ubicaciones desde Supabase
                res_ubic = supabase.table("ubicaciones").select("codigo_limpio, ubicacion, cantidad").execute()
                
                # Si no hay datos en ubicaciones, creamos un DataFrame vacío con las mismas columnas
                if not res_ubic.data:
                    df_ubic = pd.DataFrame(columns=['codigo_limpio', 'ubicacion', 'cantidad'])
                else:
                    df_ubic = pd.DataFrame(res_ubic.data)
                    df_ubic['codigo_limpio'] = df_ubic['codigo_limpio'].astype(str).str.strip()
                    
                # 3. Hacer el cruce (Left Join: mantiene todo lo del Excel)
                df_cruzado = pd.merge(
                    df_2024[['codigo_limpio', 'REFERENCIA', 'DESCRIPCION']], 
                    df_ubic, 
                    on='codigo_limpio', 
                    how='left'
                )
                
                # 4. Rellenar los vacíos (los que no hicieron match con Supabase)
                df_cruzado['ubicacion'] = df_cruzado['ubicacion'].fillna('Sin ubicación en tienda')
                df_cruzado['cantidad'] = df_cruzado['cantidad'].fillna(0).astype(int)
                
                # Calcular estadísticas
                encontrados = df_cruzado[df_cruzado['cantidad'] > 0]
                total_encontrados = len(encontrados['codigo_limpio'].unique())
                total_piezas = encontrados['cantidad'].sum()
                
                # Alertas visuales
                st.success(f"✅ Se procesaron los **{total_excel} modelos distintos** del Excel.")
                st.info(f"📦 De ese total, encontramos **{total_encontrados} modelos** físicamente en la tienda, sumando **{total_piezas} piezas** escaneadas.")
                
                # Dar formato limpio a la tabla
                df_final = df_cruzado.rename(columns={
                    'codigo_limpio': 'EAN',
                    'REFERENCIA': 'Referencia',
                    'DESCRIPCION': 'Descripción',
                    'ubicacion': 'Ubicación Física',
                    'cantidad': 'Piezas Escaneadas'
                })
                
                # Ordenar para que los que sí tienen piezas aparezcan arriba (opcional, pero útil)
                df_final = df_final.sort_values(by='Piezas Escaneadas', ascending=False)
                
                # Mostrar tabla
                st.dataframe(df_final, use_container_width=True, hide_index=True)
                
                # Descargar reporte
                csv = df_final.to_csv(index=False).encode('utf-8-sig')
                st.download_button(
                    label="⬇️ Descargar Reporte Completo (CSV)", 
                    data=csv, 
                    file_name="Reporte_Completo_2024.csv", 
                    mime="text/csv"
                )
                        
            except Exception as e:
                st.error(f"Ocurrió un error al procesar el archivo: {e}")