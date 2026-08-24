import time
import pandas as pd
from supabase import create_client
import google.generativeai as genai
import streamlit as st

# Importamos las reglas maestras desde nuestro archivo compartido
from configuracion_ia import generar_prompt_maestro

# ==========================================
# CONFIGURACIÓN DE CONEXIONES
# ==========================================
try:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    supabase = create_client(url, key)
    
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    # Mantenemos el modelo gemini-3.7-flash que corresponde a tu cuenta de pago
    model = genai.GenerativeModel('gemini-3.7-flash')
    print("✅ Conexiones a Supabase y Gemini establecidas correctamente.")
except Exception as e:
    print(f"❌ Error de configuración: {e}")
    exit()

def ejecutar_carga_masiva():
    print("\n🔍 Consultando catálogo e identificando productos por modelo base...")
    
    res_erp = supabase.table("catalogo_erp").select("referencia, descripcion, nivel1, stock_sistema").execute()
    if not res_erp.data:
        print("⚠️ No se encontraron productos en catalogo_erp.")
        return

    df = pd.DataFrame(res_erp.data)
    
    # 1. Extraer la referencia base (ej. 'B75806-7' -> 'B75806')
    df['ref_base'] = df['referencia'].astype(str).apply(lambda x: x.split('-')[0].strip())

    # 2. Agrupar por la referencia base limpia y sumar el stock total de todas sus tallas
    df_agrupado = df.groupby('ref_base').agg({
        'stock_sistema': 'sum',
        'descripcion': 'first',
        'nivel1': 'first'
    }).reset_index()

    # 3. Filtrar solo modelos cuyo stock acumulado sea mayor a 4 piezas
    df_filtrado = df_agrupado[df_agrupado['stock_sistema'] > 4]
    print(f"📊 Modelos encontrados con más de 4 piezas en stock acumulado: {len(df_filtrado)}")

    # 4. Consultar qué modelos ya existen en 'tips_ia'
    res_tips = supabase.table("tips_ia").select("referencia").execute()
    
    refs_existentes = set()
    if res_tips.data:
        for item in res_tips.data:
            ref_limpia = str(item['referencia']).split('-')[0].strip()
            refs_existentes.add(ref_limpia)

    # 5. Filtrar los modelos que realmente están pendientes
    df_pendientes = df_filtrado[~df_filtrado['ref_base'].isin(refs_existentes)]
    total_pendientes = len(df_pendientes)

    print(f"🎯 Modelos únicos pendientes por procesar en la IA: {total_pendientes}\n")

    if total_pendientes == 0:
        print("🎉 ¡Todos los modelos con stock > 4 pzs ya tienen su tip guardado!")
        return

    # 6. Bucle de generación masiva optimizado
    for index, row in df_pendientes.reset_index(drop=True).iterrows():
        ref_limpia = row['ref_base']
        nombre = row['descripcion']
        cat = row['nivel1'] if row['nivel1'] else "General"
        stock_total = row['stock_sistema']

        print(f"⏳ [{index + 1}/{total_pendientes}] Generando tip para Modelo: {ref_limpia} | Stock Total: {stock_total} pzs | {nombre}...")

        exitoso = False
        intentos = 0
        
        while not exitoso and intentos < 3:
            try:
                prompt = generar_prompt_maestro(nombre, ref_limpia, cat)
                response = model.generate_content(prompt)
                nuevo_texto = response.text

                # Guardar en Supabase usando el modelo base como referencia
                supabase.table("tips_ia").upsert({
                    "referencia": ref_limpia,
                    "tips": nuevo_texto
                }).execute()

                print(f"   ✅ Guardado exitosamente para la ref base '{ref_limpia}'.")
                exitoso = True
                
                # Pausa ligera de 1 segundo para agilizar
                time.sleep(1)

            except Exception as e:
                intentos += 1
                print(f"   ⚠️ Intento {intentos} fallido para {ref_limpia}: {e}")
                time.sleep(2)

        if not exitoso:
            print(f"   ❌ No se pudo procesar {ref_limpia} tras 3 intentos. Saltando al siguiente...")

    print("\n🏆 ¡Carga masiva completada con éxito!")

if __name__ == "__main__":
    ejecutar_carga_masiva()