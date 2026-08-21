import time
import pandas as pd
from supabase import create_client
import google.generativeai as genai
import streamlit as st

# ==========================================
# CONFIGURACIÓN DE CONEXIONES
# ==========================================
try:
    url = st.secrets["SUPABASE_URL"]
    key = st.secrets["SUPABASE_KEY"]
    supabase = create_client(url, key)
    
    genai.configure(api_key=st.secrets["GEMINI_API_KEY"])
    model = genai.GenerativeModel('gemini-3.7-flash')
    print("✅ Conexiones a Supabase y Gemini establecidas correctamente.")
except Exception as e:
    print(f"❌ Error de configuración: {e}")
    exit()

def generar_prompt(nombre_producto, referencia, categoria):
    return f"""
    Eres un asesor experto de ventas de piso de Adidas. Tu lenguaje es coloquial, directo, muy persuasivo y enfocado en aportar valor real al cliente en tienda.
    
    Genera una tarjeta de venta para este producto:
    - Producto: {nombre_producto}
    - Referencia: {referencia}
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
    
    # Normalizar referencias existentes para no volver a procesarlas
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

    # 6. Bucle de generación masiva
    for index, row in df_pendientes.reset_index(drop=True).iterrows():
        ref_limpia = row['ref_base']
        nombre = row['descripcion']
        cat = row['nivel1'] if row['nivel1'] else "General"
        stock_total = row['stock_sistema']

        print(f"⏳ [{index + 1}/{total_pendientes}] Generando tip para Modelo: {ref_limpia} | Stock Total: {stock_total} pzs | {nombre}...")

        try:
            prompt = generar_prompt(nombre, ref_limpia, cat)
            response = model.generate_content(prompt)
            nuevo_texto = response.text

            # Guardar en Supabase usando el modelo base como referencia
            supabase.table("tips_ia").upsert({
                "referencia": ref_limpia,
                "tips": nuevo_texto
            }).execute()

            print(f"   ✅ Guardado exitosamente para la ref base '{ref_limpia}'.")

            # Pausa de 2 segundos para no saturar peticiones
            time.sleep(2)

        except Exception as e:
            print(f"   ❌ Error al procesar {ref_limpia}: {e}")
            time.sleep(10)

    print("\n🏆 ¡Carga masiva por modelos completada con éxito!")

if __name__ == "__main__":
    ejecutar_carga_masiva()