import os
import io
import base64
import hashlib
import html
import re
import unicodedata
from datetime import datetime, timezone
import pandas as pd
import streamlit as st
import altair as alt
from supabase import create_client
import google.generativeai as genai
from PIL import Image, ImageOps

# Importamos las reglas maestras desde nuestro archivo de configuración
from configuracion_ia import generar_prompt_maestro


def _normalizar_encabezado(valor):
    """Normaliza encabezados del ERP para poder reconocer variantes comunes."""
    if pd.isna(valor):
        return ""
    texto = unicodedata.normalize("NFKD", str(valor))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", texto.lower()).strip()


def _normalizar_precio(valor):
    """Convierte importes numéricos o con formato regional a float."""
    if pd.isna(valor):
        return None
    if isinstance(valor, (int, float)) and not isinstance(valor, bool):
        return float(valor)

    texto = str(valor).strip()
    if not texto:
        return None
    negativo = texto.startswith("(") and texto.endswith(")")
    texto = re.sub(r"[^0-9,.-]", "", texto)
    if texto.count("-") > 1:
        return None
    texto = texto.replace("-", "")
    if not texto:
        return None

    if "," in texto and "." in texto:
        decimal = "," if texto.rfind(",") > texto.rfind(".") else "."
        miles = "." if decimal == "," else ","
        texto = texto.replace(miles, "").replace(decimal, ".")
    elif "," in texto:
        partes = texto.split(",")
        texto = "".join(partes) if len(partes[-1]) == 3 else "".join(partes[:-1]) + "." + partes[-1]
    elif texto.count(".") > 1:
        partes = texto.split(".")
        texto = "".join(partes) if len(partes[-1]) == 3 else "".join(partes[:-1]) + "." + partes[-1]

    try:
        numero = float(texto)
        return -numero if negativo else numero
    except ValueError:
        return None


def _separar_referencia(referencia):
    """Obtiene SKU maestro y talla desde el último sufijo de la referencia."""
    referencia = str(referencia).strip().upper().strip("-")
    if "-" not in referencia:
        return referencia, ""
    sku_maestro, talla = referencia.rsplit("-", 1)
    return sku_maestro.strip(), talla.strip()


def preparar_lista_precios_excel(archivo):
    """Detecta el encabezado real y prepara la lista de precios para validación."""
    bruto = pd.read_excel(archivo, header=None, dtype=object)
    aliases = {
        "referencia": {"referencia", "ref", "codigo referencia", "referencia articulo", "referencia producto"},
        "descripcion": {"descripcion", "descripcion articulo", "descripcion producto", "nombre articulo", "producto"},
        "precio": {
            "lista publico", "lista de publico", "precio publico", "precio de publico",
            "precio lista", "precio de lista", "pvp", "precio venta", "precio venta publico"
        },
    }

    fila_encabezado = None
    indices = None
    for numero_fila, fila in bruto.head(60).iterrows():
        normalizados = [_normalizar_encabezado(valor) for valor in fila.tolist()]
        encontrados = {}
        for destino, opciones in aliases.items():
            for indice, encabezado in enumerate(normalizados):
                if encabezado in opciones:
                    encontrados[destino] = indice
                    break
        if len(encontrados) == len(aliases):
            fila_encabezado, indices = numero_fila, encontrados
            break

        # Algunos reportes ERP agrupan "Matriz Referencia" sobre varias columnas
        # y sólo rotulan la columna de precio. En ese diseño, Referencia y
        # Descripción son las dos columnas inmediatamente anteriores al precio.
        indice_precio = None
        for indice, encabezado in enumerate(normalizados):
            if encabezado in aliases["precio"]:
                indice_precio = indice
                break
        if indice_precio is not None and indice_precio >= 2:
            muestra = bruto.iloc[numero_fila + 1:min(numero_fila + 11, len(bruto))]
            tiene_referencia = muestra.iloc[:, indice_precio - 2].notna().any()
            tiene_descripcion = muestra.iloc[:, indice_precio - 1].notna().any()
            if tiene_referencia and tiene_descripcion:
                fila_encabezado = numero_fila
                indices = {
                    "referencia": indice_precio - 2,
                    "descripcion": indice_precio - 1,
                    "precio": indice_precio,
                }
                break

    if fila_encabezado is None:
        raise ValueError(
            "No se encontró una fila con Referencia, Descripción y Lista Público (o encabezados equivalentes) "
            "en las primeras 60 filas."
        )

    datos = bruto.iloc[fila_encabezado + 1:, [indices["referencia"], indices["descripcion"], indices["precio"]]].copy()
    datos.columns = ["referencia", "descripcion", "precio_original"]
    datos["referencia"] = datos["referencia"].where(datos["referencia"].notna(), "").astype(str).str.strip().str.upper()
    datos["descripcion"] = datos["descripcion"].where(datos["descripcion"].notna(), "").astype(str).str.strip()
    datos["precio_lista"] = datos["precio_original"].apply(_normalizar_precio)
    datos[["sku_maestro", "talla"]] = datos["referencia"].apply(
        lambda valor: pd.Series(_separar_referencia(valor))
    )

    vacias = datos["referencia"].eq("")
    precios_invalidos = datos["precio_lista"].isna() | (datos["precio_lista"] < 0)
    duplicadas = datos["referencia"].ne("") & datos["referencia"].duplicated(keep="last")
    validas = datos.loc[~vacias & ~precios_invalidos & ~duplicadas].copy()
    validas["precio_lista"] = validas["precio_lista"].round(2)
    validas = validas[["referencia", "descripcion", "talla", "sku_maestro", "precio_lista"]]

    conteos = {
        "filas_leidas": len(datos),
        "referencias_vacias": int(vacias.sum()),
        "precios_invalidos": int((precios_invalidos & ~vacias).sum()),
        "duplicados_descartados": int(duplicadas.sum()),
        "registros_validos": len(validas),
        "fila_encabezado": int(fila_encabezado) + 1,
    }
    return validas, conteos


def _normalizar_sku_maestro(valor):
    """Normaliza un modelo o referencia al SKU maestro de seis caracteres."""
    if pd.isna(valor):
        return ""
    texto = str(valor).strip().upper()
    if re.fullmatch(r"\d+\.0", texto):
        texto = texto[:-2]
    texto = texto.split("-", 1)[0].strip()
    texto = re.sub(r"\s+", "", texto)
    return texto[:6]


def _normalizar_descuento(valor):
    """Convierte descuentos como 40, 40% o 0.40 a porcentaje entre 0 y 100."""
    if pd.isna(valor):
        return None
    texto = str(valor).strip().replace("%", "").replace(",", ".")
    try:
        descuento = float(texto)
    except ValueError:
        return None
    if 0 < descuento <= 1:
        descuento *= 100
    if descuento < 0 or descuento > 100:
        return None
    return round(descuento, 2)


def preparar_productos_promocion_excel(archivo, descuento_general=None):
    """Extrae modelos y descuentos generales o individuales desde un Excel."""
    bruto = pd.read_excel(archivo, header=None, dtype=object)
    aliases_modelo = {"modelo", "sku", "sku maestro", "referencia", "codigo modelo"}
    aliases_descripcion = {"descripcion", "producto", "nombre producto"}
    aliases_descuento = {"descuento", "descuento porcentaje", "porcentaje", "porcentaje descuento", "off"}
    fila_encabezado = None
    indice_modelo = None
    indice_descripcion = None
    indice_descuento = None

    for numero_fila, fila in bruto.head(60).iterrows():
        encabezados = [_normalizar_encabezado(valor) for valor in fila.tolist()]
        for indice, encabezado in enumerate(encabezados):
            if indice_modelo is None and encabezado in aliases_modelo:
                indice_modelo = indice
            if indice_descripcion is None and encabezado in aliases_descripcion:
                indice_descripcion = indice
            if indice_descuento is None and encabezado in aliases_descuento:
                indice_descuento = indice
        if indice_modelo is not None:
            fila_encabezado = numero_fila
            break
        indice_descripcion = None
        indice_descuento = None

    if fila_encabezado is None:
        raise ValueError("No se encontró una columna Modelo, SKU o Referencia en las primeras 60 filas.")

    datos = pd.DataFrame()
    datos["sku_maestro"] = bruto.iloc[fila_encabezado + 1:, indice_modelo].apply(_normalizar_sku_maestro)
    if indice_descripcion is not None:
        datos["descripcion_archivo"] = (
            bruto.iloc[fila_encabezado + 1:, indice_descripcion]
            .where(bruto.iloc[fila_encabezado + 1:, indice_descripcion].notna(), "")
            .astype(str)
            .str.strip()
            .values
        )
    else:
        datos["descripcion_archivo"] = ""

    if descuento_general is None:
        if indice_descuento is None:
            raise ValueError(
                "El modo individual requiere una columna Descuento, % Descuento o Porcentaje."
            )
        datos["descuento_porcentaje"] = bruto.iloc[
            fila_encabezado + 1:, indice_descuento
        ].apply(_normalizar_descuento).values
    else:
        descuento = _normalizar_descuento(descuento_general)
        if descuento is None:
            raise ValueError("El descuento general debe estar entre 0 y 100.")
        datos["descuento_porcentaje"] = descuento

    modelos_vacios = datos["sku_maestro"].eq("")
    descuentos_invalidos = datos["descuento_porcentaje"].isna()
    duplicados = datos["sku_maestro"].ne("") & datos["sku_maestro"].duplicated(keep="last")
    validos = datos.loc[~modelos_vacios & ~descuentos_invalidos & ~duplicados].copy()
    validos = validos[["sku_maestro", "descripcion_archivo", "descuento_porcentaje"]]
    conteos = {
        "filas_leidas": len(datos),
        "modelos_vacios": int(modelos_vacios.sum()),
        "descuentos_invalidos": int((descuentos_invalidos & ~modelos_vacios).sum()),
        "duplicados_descartados": int(duplicados.sum()),
        "modelos_validos": len(validos),
        "fila_encabezado": int(fila_encabezado) + 1,
    }
    return validos, conteos


def clasificar_estado_promocion(promocion, hoy=None):
    """Devuelve el estado operativo de una promoción sin alterar su historial."""
    hoy = hoy or datetime.now().date()
    if not promocion.get("activa", False):
        return "Desactivada", "⚫"
    fecha_inicio = pd.to_datetime(promocion.get("fecha_inicio"), errors="coerce")
    fecha_fin = pd.to_datetime(promocion.get("fecha_fin"), errors="coerce")
    if pd.isna(fecha_inicio):
        return "Fecha inválida", "⚠️"
    if hoy < fecha_inicio.date():
        return "Programada", "🔵"
    if pd.notna(fecha_fin) and hoy > fecha_fin.date():
        return "Finalizada", "🟠"
    return "Activa", "🟢"


def obtener_promociones_con_conteos(tamano_pagina=1000):
    """Consulta campañas y cuenta todos sus modelos mediante paginación."""
    respuesta = (
        supabase.table("promociones")
        .select("id,nombre,descripcion,fecha_inicio,fecha_fin,activa,created_at,updated_at")
        .order("created_at", desc=True)
        .execute()
    )
    promociones = respuesta.data or []
    conteos = {}
    inicio = 0
    while True:
        pagina_respuesta = (
            supabase.table("promocion_productos")
            .select("promocion_id")
            .range(inicio, inicio + tamano_pagina - 1)
            .execute()
        )
        pagina = pagina_respuesta.data or []
        for producto in pagina:
            promocion_id = str(producto.get("promocion_id") or "")
            if promocion_id:
                conteos[promocion_id] = conteos.get(promocion_id, 0) + 1
        if len(pagina) < tamano_pagina:
            break
        inicio += tamano_pagina
    return promociones, conteos


def obtener_productos_de_promocion(promocion_id, tamano_pagina=1000):
    """Obtiene todos los modelos de una campaña para consulta y exportación."""
    productos = []
    inicio = 0
    while True:
        respuesta = (
            supabase.table("promocion_productos")
            .select("sku_maestro,descuento_porcentaje,created_at")
            .eq("promocion_id", promocion_id)
            .order("sku_maestro")
            .range(inicio, inicio + tamano_pagina - 1)
            .execute()
        )
        pagina = respuesta.data or []
        productos.extend(pagina)
        if len(pagina) < tamano_pagina:
            break
        inicio += tamano_pagina
    return pd.DataFrame(productos)


def crear_reporte_promocion_excel(promocion, productos, estado):
    """Genera en memoria un Excel histórico con resumen y detalle de modelos."""
    resumen = pd.DataFrame([{
        "Promoción": promocion.get("nombre"),
        "Descripción": promocion.get("descripcion") or "",
        "Fecha de inicio": promocion.get("fecha_inicio"),
        "Fecha de término": promocion.get("fecha_fin") or "",
        "Estado": estado,
        "Activa manualmente": "Sí" if promocion.get("activa") else "No",
        "Cantidad de modelos": len(productos),
        "Fecha de creación": promocion.get("created_at") or "",
        "Última actualización": promocion.get("updated_at") or "",
    }])
    detalle = productos.rename(columns={
        "sku_maestro": "SKU maestro",
        "descuento_porcentaje": "Descuento (%)",
        "created_at": "Fecha de registro",
    })
    salida = io.BytesIO()
    with pd.ExcelWriter(salida, engine="openpyxl") as escritor:
        resumen.to_excel(escritor, sheet_name="Resumen", index=False)
        detalle.to_excel(escritor, sheet_name="Productos", index=False)
        for hoja in escritor.book.worksheets:
            hoja.freeze_panes = "A2"
            hoja.auto_filter.ref = hoja.dimensions
            for columna in hoja.columns:
                ancho = min(max(len(str(celda.value or "")) for celda in columna) + 2, 45)
                hoja.column_dimensions[columna[0].column_letter].width = max(ancho, 12)
    salida.seek(0)
    return salida.getvalue()


def enriquecer_resultados_con_precios_promociones(df_resultados):
    """Agrega precio y la mejor promoción vigente sin interrumpir la búsqueda base."""
    resultado = df_resultados.copy()
    resultado["sku_maestro"] = resultado["referencia"].apply(_normalizar_sku_maestro)
    resultado["precio_lista"] = pd.NA
    resultado["promocion"] = None
    resultado["descuento_porcentaje"] = pd.NA
    resultado["precio_final"] = pd.NA
    resultado["vigencia_promocion"] = None
    avisos = []

    try:
        referencias = resultado["referencia"].dropna().astype(str).str.strip().str.upper().unique().tolist()
        if referencias:
            respuesta_precios = (
                supabase.table("lista_precios")
                .select("referencia,precio_lista")
                .in_("referencia", referencias)
                .execute()
            )
            mapa_precios = {
                str(fila["referencia"]).strip().upper(): float(fila["precio_lista"])
                for fila in (respuesta_precios.data or [])
                if fila.get("referencia") and fila.get("precio_lista") is not None
            }
            resultado["precio_lista"] = resultado["referencia"].astype(str).str.strip().str.upper().map(mapa_precios)
    except Exception as ex:
        avisos.append(f"No fue posible consultar la lista de precios: {ex}")

    try:
        hoy = datetime.now().date()
        respuesta_promociones = (
            supabase.table("promociones")
            .select("id,nombre,fecha_inicio,fecha_fin,activa")
            .eq("activa", True)
            .execute()
        )
        promociones_vigentes = {}
        for promocion in (respuesta_promociones.data or []):
            fecha_inicio = pd.to_datetime(promocion.get("fecha_inicio"), errors="coerce")
            fecha_fin = pd.to_datetime(promocion.get("fecha_fin"), errors="coerce")
            if pd.isna(fecha_inicio):
                continue
            inicio = fecha_inicio.date()
            fin = fecha_fin.date() if pd.notna(fecha_fin) else None
            if inicio <= hoy and (fin is None or hoy <= fin):
                promociones_vigentes[str(promocion["id"])] = promocion

        skus = resultado["sku_maestro"].dropna().astype(str).unique().tolist()
        if promociones_vigentes and skus:
            respuesta_productos = (
                supabase.table("promocion_productos")
                .select("promocion_id,sku_maestro,descuento_porcentaje")
                .in_("promocion_id", list(promociones_vigentes))
                .in_("sku_maestro", skus)
                .execute()
            )
            candidatos = {}
            coincidencias_multiples = set()
            for producto_promocion in (respuesta_productos.data or []):
                sku = _normalizar_sku_maestro(producto_promocion.get("sku_maestro"))
                promocion_id = str(producto_promocion.get("promocion_id"))
                promocion = promociones_vigentes.get(promocion_id)
                descuento = _normalizar_descuento(producto_promocion.get("descuento_porcentaje"))
                if not sku or not promocion or descuento is None:
                    continue
                candidato = (descuento, promocion)
                if sku in candidatos:
                    coincidencias_multiples.add(sku)
                if sku not in candidatos or descuento > candidatos[sku][0]:
                    candidatos[sku] = candidato

            if coincidencias_multiples:
                avisos.append(
                    f"{len(coincidencias_multiples)} SKU coinciden con varias promociones vigentes; "
                    "se aplicó el mayor descuento."
                )

            for indice, fila in resultado.iterrows():
                candidato = candidatos.get(fila["sku_maestro"])
                if not candidato:
                    continue
                descuento, promocion = candidato
                resultado.at[indice, "promocion"] = promocion["nombre"]
                resultado.at[indice, "descuento_porcentaje"] = descuento
                fecha_fin_texto = promocion.get("fecha_fin") or "Sin fecha de término"
                resultado.at[indice, "vigencia_promocion"] = (
                    f"{promocion['fecha_inicio']} a {fecha_fin_texto}"
                )
                precio = pd.to_numeric(fila["precio_lista"], errors="coerce")
                if pd.notna(precio):
                    resultado.at[indice, "precio_final"] = round(
                        float(precio) * (1 - descuento / 100),
                        2,
                    )
    except Exception as ex:
        avisos.append(f"No fue posible consultar las promociones: {ex}")

    return resultado, avisos


def construir_tarjetas_resultados(df_resultados, es_admin=False):
    """Construye tarjetas compactas por SKU maestro con tallas y ubicaciones."""
    if df_resultados.empty:
        return ""

    def texto_seguro(valor, predeterminado="—"):
        if pd.isna(valor) or str(valor).strip() in {"", "None", "nan"}:
            return html.escape(predeterminado)
        return html.escape(str(valor).strip())

    def primer_numero(serie):
        numeros = pd.to_numeric(serie, errors="coerce").dropna()
        return float(numeros.iloc[0]) if not numeros.empty else None

    tarjetas = []
    for sku_maestro, grupo in df_resultados.groupby("sku_maestro", sort=False, dropna=False):
        primera_fila = grupo.iloc[0]
        sku_texto = texto_seguro(sku_maestro, primera_fila.get("referencia", "Sin referencia"))
        descripcion = texto_seguro(primera_fila.get("descripcion"), "Sin descripción")
        categoria = texto_seguro(primera_fila.get("nivel1"), "General")
        precio_lista = primer_numero(grupo["precio_lista"])
        precio_final = primer_numero(grupo["precio_final"])
        descuento = primer_numero(grupo["descuento_porcentaje"])
        promociones = grupo["promocion"].dropna().astype(str).str.strip()
        nombre_promocion = texto_seguro(promociones.iloc[0]) if not promociones.empty else ""

        if precio_lista is None:
            bloque_precio = '<span class="sinapsis-price-missing">Precio no disponible</span>'
        elif descuento is not None and precio_final is not None:
            bloque_precio = (
                f'<span class="sinapsis-price-old">${precio_lista:,.2f}</span>'
                f'<span class="sinapsis-promo-badge">-{descuento:g}% · {nombre_promocion}</span>'
                f'<span class="sinapsis-price-final">${precio_final:,.2f}</span>'
            )
        else:
            bloque_precio = f'<span class="sinapsis-price-final">${precio_lista:,.2f}</span>'

        variantes = []
        for talla, grupo_talla in grupo.groupby("talla", sort=False, dropna=False):
            ubicaciones = []
            for ubicacion in grupo_talla["ubicacion"].dropna().astype(str):
                ubicacion = ubicacion.strip()
                if ubicacion and ubicacion not in ubicaciones:
                    ubicaciones.append(ubicacion)
            ubicacion_texto = texto_seguro(", ".join(ubicaciones), "Sin ubicación registrada")
            stock = pd.to_numeric(
                grupo_talla.get("stock_sistema", pd.Series(dtype=float)),
                errors="coerce",
            ).fillna(0).sum()
            codigo_html = ""
            if es_admin and "codigo_limpio" in grupo_talla.columns:
                codigos = grupo_talla["codigo_limpio"].dropna().astype(str).unique().tolist()
                if codigos:
                    codigo_html = (
                        f'<span class="sinapsis-variant-code">Código: '
                        f'{texto_seguro(", ".join(codigos))}</span>'
                    )
            variantes.append(
                '<div class="sinapsis-variant">'
                f'<div class="sinapsis-variant-top"><strong>Talla {texto_seguro(talla)}</strong>'
                f'<span>{stock:g} pza</span></div>'
                f'<div class="sinapsis-location">📍 {ubicacion_texto}</div>'
                f'{codigo_html}</div>'
            )

        tarjetas.append(
            '<article class="sinapsis-product-card">'
            '<div class="sinapsis-product-header">'
            f'<div><div class="sinapsis-product-sku">{sku_texto}</div>'
            f'<div class="sinapsis-product-name">{descripcion}</div>'
            f'<div class="sinapsis-product-category">{categoria}</div></div>'
            f'<div class="sinapsis-price-block">{bloque_precio}</div>'
            '</div>'
            f'<div class="sinapsis-variants">{"".join(variantes)}</div>'
            '</article>'
        )

    return '<div class="sinapsis-results-grid">' + "".join(tarjetas) + "</div>"


def preparar_referencias_inventario_excel(archivo):
    """Lee el inventario ERP en Excel y devuelve referencias con existencia positiva."""
    bruto = pd.read_excel(archivo, header=None, dtype=object)
    aliases_referencia = {"referencia", "ref", "codigo referencia", "referencia articulo"}
    aliases_existencia = {
        "cantidad", "existencia", "existencias", "stock", "stock sistema", "unidades disponibles"
    }
    fila_encabezado = None
    indice_referencia = None
    indice_existencia = None

    for numero_fila, fila in bruto.head(60).iterrows():
        normalizados = [_normalizar_encabezado(valor) for valor in fila.tolist()]
        for indice, encabezado in enumerate(normalizados):
            if indice_referencia is None and encabezado in aliases_referencia:
                indice_referencia = indice
            if indice_existencia is None and encabezado in aliases_existencia:
                indice_existencia = indice
        if indice_referencia is not None and indice_existencia is not None:
            # En el Excel nativo del ERP, "Referencia" puede ser un encabezado
            # combinado. El valor real queda en la última columna de ese bloque.
            siguientes_encabezados = [
                indice for indice in range(indice_referencia + 1, len(normalizados))
                if normalizados[indice]
            ]
            if siguientes_encabezados and siguientes_encabezados[0] - indice_referencia > 1:
                indice_referencia = siguientes_encabezados[0] - 1
            fila_encabezado = numero_fila
            break
        indice_referencia = None
        indice_existencia = None

    if fila_encabezado is None:
        raise ValueError(
            "No se encontró una fila con Referencia y Cantidad (o encabezados equivalentes) "
            "en las primeras 60 filas del inventario."
        )

    datos = bruto.iloc[fila_encabezado + 1:, [indice_referencia, indice_existencia]].copy()
    datos.columns = ["referencia", "existencia"]
    datos["referencia"] = (
        datos["referencia"]
        .where(datos["referencia"].notna(), "")
        .astype(str)
        .str.strip()
        .str.upper()
    )
    datos["existencia"] = pd.to_numeric(
        datos["existencia"].astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    ).fillna(0)
    activas = datos[datos["referencia"].ne("") & datos["existencia"].gt(0)]
    referencias_activas = set(activas["referencia"])
    conteos = {
        "filas_inventario": len(datos),
        "referencias_con_existencia": len(referencias_activas),
        "piezas_con_existencia": float(activas["existencia"].sum()),
        "fila_encabezado_inventario": int(fila_encabezado) + 1,
    }
    return referencias_activas, conteos


def preparar_catalogo_inventario_excel(archivo):
    """Prepara el catálogo completo, incluyendo el formato de encabezados combinados del ERP."""
    bruto = pd.read_excel(archivo, header=None, dtype=object)
    fila_encabezado = None
    indices = None
    aliases = {
        "referencia": {"referencia", "ref", "codigo referencia", "referencia articulo"},
        "codigo_limpio": {"codigo alterno", "codigoalterno", "codigo de barras"},
        "descripcion": {"descripcion", "descripcion articulo", "descripcion producto"},
        "stock_sistema": {"cantidad", "existencia", "existencias", "stock", "stock sistema", "unidades disponibles"},
    }

    for numero_fila, fila in bruto.head(60).iterrows():
        encabezados = [_normalizar_encabezado(valor) for valor in fila.tolist()]
        encontrados = {}
        for destino, opciones in aliases.items():
            for indice, encabezado in enumerate(encabezados):
                if encabezado in opciones:
                    encontrados[destino] = indice
                    break
        if len(encontrados) != len(aliases):
            continue

        indice_referencia = encontrados["referencia"]
        indice_codigo = encontrados["codigo_limpio"]
        if indice_codigo - indice_referencia > 1:
            indice_referencia = indice_codigo - 1

        indices = {
            **encontrados,
            "referencia": indice_referencia,
        }
        niveles = {}
        for numero_nivel in range(1, 5):
            nombre = f"nivel{numero_nivel}"
            if nombre in encabezados:
                niveles[nombre] = encabezados.index(nombre)
        if len(niveles) < 4 and indice_referencia >= 5:
            niveles = {
                "nivel1": indice_referencia - 5,
                "nivel2": indice_referencia - 4,
                "nivel3": indice_referencia - 2,
                "nivel4": indice_referencia - 1,
            }
        indices.update(niveles)
        fila_encabezado = numero_fila
        break

    if fila_encabezado is None or indices is None:
        raise ValueError(
            "No se localizaron Referencia, Código Alterno, Descripción y existencias "
            "en las primeras 60 filas del catálogo."
        )

    columnas = [
        "codigo_limpio", "referencia", "descripcion",
        "nivel1", "nivel2", "nivel3", "nivel4", "stock_sistema",
    ]
    datos = pd.DataFrame(index=bruto.index[fila_encabezado + 1:])
    for columna in columnas:
        datos[columna] = bruto.iloc[fila_encabezado + 1:, indices[columna]].values

    for columna in ["codigo_limpio", "referencia", "descripcion", "nivel1", "nivel2", "nivel3", "nivel4"]:
        datos[columna] = datos[columna].where(datos[columna].notna(), "").astype(str).str.strip()
    datos["referencia"] = datos["referencia"].str.upper()
    datos["stock_sistema"] = pd.to_numeric(
        datos["stock_sistema"].astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    ).fillna(0).astype(int)
    datos = datos[datos["codigo_limpio"].ne("") & datos["referencia"].ne("")].copy()
    datos["talla"] = datos["referencia"].apply(lambda valor: _separar_referencia(valor)[1])
    datos = datos.drop_duplicates(subset=["codigo_limpio"], keep="last")

    activas = datos[datos["stock_sistema"].gt(0)]
    referencias_activas = set(activas["referencia"])
    conteos = {
        "filas_inventario": len(datos),
        "referencias_con_existencia": len(referencias_activas),
        "piezas_con_existencia": float(activas["stock_sistema"].sum()),
        "fila_encabezado_inventario": int(fila_encabezado) + 1,
    }
    return datos, referencias_activas, conteos


def obtener_codigos_catalogo_supabase(tamano_pagina=1000):
    """Obtiene todos los códigos actuales para detectar registros ausentes del nuevo catálogo."""
    codigos = set()
    inicio = 0
    while True:
        respuesta = (
            supabase.table("catalogo_erp")
            .select("codigo_limpio")
            .range(inicio, inicio + tamano_pagina - 1)
            .execute()
        )
        pagina = respuesta.data or []
        for registro in pagina:
            codigo = str(registro.get("codigo_limpio") or "").strip()
            if codigo:
                codigos.add(codigo)
        if len(pagina) < tamano_pagina:
            break
        inicio += tamano_pagina
    return codigos

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

    .sinapsis-results-grid {
        display: grid;
        gap: 16px;
        margin: 12px 0 20px;
    }

    .sinapsis-product-card {
        background: linear-gradient(135deg, rgba(0, 217, 245, 0.055), rgba(57, 255, 136, 0.075));
        border: 1px solid rgba(0, 217, 245, 0.25);
        border-left: 5px solid #39FF88;
        border-radius: 16px;
        padding: 18px;
        box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08);
    }

    .sinapsis-product-header {
        display: flex;
        align-items: flex-start;
        justify-content: space-between;
        gap: 18px;
        margin-bottom: 14px;
    }

    .sinapsis-product-sku {
        color: #39FF88;
        font-family: 'Orbitron', sans-serif;
        font-size: 1.15rem;
        font-weight: 800;
        letter-spacing: 0.6px;
    }

    .sinapsis-product-name {
        font-size: 1rem;
        font-weight: 600;
        margin-top: 3px;
    }

    .sinapsis-product-category,
    .sinapsis-variant-code,
    .sinapsis-price-missing {
        color: rgba(128, 128, 128, 0.95);
        font-size: 0.82rem;
        margin-top: 3px;
    }

    .sinapsis-price-block {
        display: flex;
        align-items: flex-end;
        flex-direction: column;
        gap: 3px;
        min-width: max-content;
    }

    .sinapsis-price-old {
        color: #FFFFFF;
        font-size: 0.92rem;
        font-weight: 600;
        text-decoration: line-through;
        text-decoration-thickness: 2px;
        text-decoration-color: rgba(255, 255, 255, 0.8);
    }

    .sinapsis-price-final {
        color: #39FF88;
        font-family: 'Orbitron', sans-serif;
        font-size: 1.25rem;
        font-weight: 800;
    }

    .sinapsis-promo-badge {
        background: rgba(255, 179, 0, 0.16);
        color: #ffb300;
        border: 1px solid rgba(255, 179, 0, 0.38);
        border-radius: 999px;
        padding: 3px 9px;
        font-size: 0.78rem;
        font-weight: 700;
    }

    .sinapsis-variants {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
        gap: 9px;
    }

    .sinapsis-variant {
        background: rgba(128, 128, 128, 0.075);
        border: 1px solid rgba(128, 128, 128, 0.2);
        border-radius: 10px;
        padding: 10px 12px;
    }

    .sinapsis-variant-top {
        display: flex;
        justify-content: space-between;
        gap: 8px;
        color: inherit;
    }

    .sinapsis-variant-top span {
        color: #39FF88;
        font-weight: 700;
        white-space: nowrap;
    }

    .sinapsis-location {
        margin-top: 6px;
        font-size: 0.88rem;
        overflow-wrap: anywhere;
    }

    @media (max-width: 640px) {
        .sinapsis-product-card {
            padding: 14px;
        }

        .sinapsis-product-header {
            flex-direction: column;
            gap: 10px;
        }

        .sinapsis-price-block {
            align-items: flex-start;
        }

        .sinapsis-variants {
            grid-template-columns: 1fr;
        }
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
        st.caption("v3.27.1 (Neural Core) | Desarrollado por Risal Tech")
        
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
    st.caption("🚀 **Versión:** 3.27.1 (Neural Core)")
    st.caption(f"👤 **Usuario:** {st.session_state.usuario_actual}")
    
    if st.button("🚪 Cerrar Sesión"):
        st.session_state.autenticado = False
        st.session_state.es_admin = False
        st.session_state.felicitacion_mostrada = False
        st.rerun()
    st.markdown("---")

# ==========================================
# FUNCIONES DE IA Y ANÁLISIS
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

def agrupar_top_n(df, campo_valor, campo_categoria, top_n=5):
    df_ordenado = df.sort_values(by=campo_valor, ascending=False).reset_index(drop=True)
    if len(df_ordenado) <= top_n:
        return df_ordenado
    
    df_top = df_ordenado.iloc[:top_n].copy()
    valor_otros = df_ordenado.iloc[top_n:][campo_valor].sum()
    fila_otros = pd.DataFrame({campo_categoria: ["Otros"], campo_valor: [valor_otros]})
    return pd.concat([df_top, fila_otros], ignore_index=True)

def crear_grafica_barras_inteligente(df, campo_x, campo_y, titulo_x, titulo_y, color_base='#39FF88', umbral_adentro=15):
    df_sorted = df.sort_values(by=campo_x, ascending=False).reset_index(drop=True)
    orden_y = df_sorted[campo_y].tolist()
    
    base = alt.Chart(df_sorted).encode(
        y=alt.Y(f'{campo_y}:N', sort=orden_y, title=titulo_y),
        x=alt.X(f'{campo_x}:Q', title=titulo_x)
    )
    
    barras = base.mark_bar(color=color_base)
    
    texto_adentro = base.transform_filter(
        alt.datum[campo_x] > umbral_adentro
    ).mark_text(align='right', dx=-8, baseline='middle', color='#111111', fontWeight='bold', fontSize=11).encode(
        text=alt.Text(f'{campo_x}:Q', format=',.0f')
    )
    
    texto_afuera = base.transform_filter(
        alt.datum[campo_x] <= umbral_adentro
    ).mark_text(align='left', dx=5, baseline='middle', color='#FFFFFF', fontSize=11).encode(
        text=alt.Text(f'{campo_x}:Q', format=',.0f')
    )
    
    return (barras + texto_adentro + texto_afuera)

def mostrar_resumen_piso_ventas(supabase):
    st.header("📊 Resumen Ejecutivo: Piso de Ventas (PV)")
    
    res_ubic_all = supabase.table("ubicaciones").select("*").execute()
    
    if not res_ubic_all.data:
        st.warning("No hay registros en la tabla de ubicaciones.")
        return
        
    df_ubic_all = pd.DataFrame(res_ubic_all.data)
    
    ubi_up = df_ubic_all['ubicacion'].astype(str).str.strip().str.upper()
    is_pv = (ubi_up == 'PV') | (ubi_up.str.startswith('PV -')) | (ubi_up.str.startswith('PV-'))
    
    df_ubic_pv = df_ubic_all[is_pv].copy()
    df_ubic_bodega = df_ubic_all[~is_pv].copy()
    
    if df_ubic_pv.empty:
        st.warning("No hay productos registrados específicamente en la ubicación 'PV' (Piso de Ventas).")
        return

    codigos_a_buscar = df_ubic_all['codigo_limpio'].dropna().astype(str).unique().tolist()
    
    df_cat_list = []
    TAMANO_LOTE = 200
    for i in range(0, len(codigos_a_buscar), TAMANO_LOTE):
        lote = codigos_a_buscar[i:i + TAMANO_LOTE]
        res_cat_lote = supabase.table("catalogo_erp").select("codigo_limpio, referencia, descripcion, nivel1, nivel2, nivel3, nivel4, stock_sistema").in_("codigo_limpio", lote).execute()
        if res_cat_lote.data:
            df_cat_list.extend(res_cat_lote.data)
            
    if not df_cat_list:
        st.warning("No se encontraron coincidencias en el catálogo ERP para los productos escaneados.")
        return
        
    df_cat = pd.DataFrame(df_cat_list)
    
    df_cruce = pd.merge(df_ubic_pv, df_cat, on="codigo_limpio", how="left")
    df_cruce.fillna({
        "nivel1": "Sin Categoría", 
        "nivel2": "Sin Actividad",
        "nivel3": "Sin Subcategoría", 
        "nivel4": "Sin Nivel 4",
        "referencia": "Desconocida", 
        "descripcion": "Sin descripción",
        "stock_sistema": 0
    }, inplace=True)
    
    df_cruce['modelo_base'] = df_cruce['referencia'].astype(str).apply(lambda x: x.split('-')[0].strip())
    
    total_piezas = df_cruce['cantidad'].sum()
    st.metric("Total de Piezas en Piso (PV)", f"{total_piezas:,.0f}")
    st.divider()
    
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("Mezcla por Categoría General")
        df_cat1 = df_cruce.groupby("nivel1")['cantidad'].sum().reset_index()
        df_cat1 = agrupar_top_n(df_cat1, 'cantidad', 'nivel1', top_n=5)
        df_cat1['Porcentaje (%)'] = (df_cat1['cantidad'] / total_piezas) * 100
        df_cat1['Etiqueta_Leyenda'] = df_cat1.apply(lambda r: f"{r['nivel1']} ({r['Porcentaje (%)']:.1f}%)", axis=1)
        orden_leyenda_cat1 = df_cat1.sort_values('cantidad', ascending=False)['Etiqueta_Leyenda'].tolist()
        
        dona_cat1 = alt.Chart(df_cat1).mark_arc(innerRadius=55).encode(
            theta=alt.Theta(field="cantidad", type="quantitative"),
            color=alt.Color(field="Etiqueta_Leyenda", type="nominal", sort=orden_leyenda_cat1, legend=alt.Legend(title="Categoría", orient="right")),
            tooltip=[
                alt.Tooltip('nivel1:N', title='Categoría'), 
                alt.Tooltip('cantidad:Q', title='Piezas', format=',.0f'), 
                alt.Tooltip('Porcentaje (%):Q', format='.1f')
            ]
        ).properties(height=340)
        
        st.altair_chart(dona_cat1, use_container_width=True)
        
    with col2:
        st.subheader("Subcategorías (Volumen)")
        df_cat3 = df_cruce.groupby("nivel3")['cantidad'].sum().reset_index()
        chart_cat3 = crear_grafica_barras_inteligente(df_cat3, 'cantidad', 'nivel3', 'Piezas Físicas', None, color_base='#00D9F5', umbral_adentro=30)
        st.altair_chart(chart_cat3.properties(height=340), use_container_width=True)

    st.divider()
    
    st.subheader("🏃‍♂️ Análisis por Actividad / Deporte")
    st.caption("Visualiza la mezcla por deporte en **Porcentaje (%)**, **Piezas Físicas** y **Variedad de Modelos**.")
    
    st.markdown("##### 🍩 Mezcla Porcentual (%)")
    df_act_pct = df_cruce.groupby("nivel2")['cantidad'].sum().reset_index()
    df_act_pct = agrupar_top_n(df_act_pct, 'cantidad', 'nivel2', top_n=5)
    df_act_pct['Porcentaje (%)'] = (df_act_pct['cantidad'] / total_piezas) * 100
    df_act_pct['Etiqueta_Leyenda'] = df_act_pct.apply(lambda r: f"{r['nivel2']} ({r['Porcentaje (%)']:.1f}%)", axis=1)
    orden_leyenda_act = df_act_pct.sort_values('cantidad', ascending=False)['Etiqueta_Leyenda'].tolist()
    
    dona_act_base = alt.Chart(df_act_pct).encode(
        theta=alt.Theta(field="cantidad", type="quantitative", stack=True),
        color=alt.Color(field="Etiqueta_Leyenda", type="nominal", sort=orden_leyenda_act, legend=alt.Legend(title="Deporte", orient="right")),
        order=alt.Order(field="cantidad", sort="descending"),
        tooltip=[
            alt.Tooltip('nivel2:N', title='Deporte'), 
            alt.Tooltip('cantidad:Q', title='Piezas', format=',.0f'), 
            alt.Tooltip('Porcentaje (%):Q', format='.1f')
        ]
    )
    dona_act_arco = dona_act_base.mark_arc(innerRadius=90, outerRadius=180)
    dona_act_texto = dona_act_base.mark_text(radius=210, fontSize=13, fontWeight='bold').encode(
        text=alt.Text('Porcentaje (%):Q', format='.1f')
    )
    st.altair_chart((dona_act_arco + dona_act_texto).properties(height=480), use_container_width=True)

    col_act2, col_act3 = st.columns(2)
    with col_act2:
        st.markdown("##### 📊 Volumen en Piezas")
        df_act_p = df_cruce.groupby("nivel2")['cantidad'].sum().reset_index()
        chart_act_p = crear_grafica_barras_inteligente(df_act_p, 'cantidad', 'nivel2', 'Total Piezas', None, color_base='#39FF88', umbral_adentro=25)
        st.altair_chart(chart_act_p.properties(height=360), use_container_width=True)
        
    with col_act3:
        st.markdown("##### 📋 Variedad de Modelos")
        df_act_r = df_cruce.groupby("nivel2")['modelo_base'].nunique().reset_index().rename(columns={'modelo_base': 'total_modelos'})
        chart_act_r = crear_grafica_barras_inteligente(df_act_r, 'total_modelos', 'nivel2', 'Modelos', None, color_base='#00D9F5', umbral_adentro=8)
        st.altair_chart(chart_act_r.properties(height=360), use_container_width=True)

    st.divider()
    
    st.subheader("⚠️ Alerta de Picos / Resurtido")
    st.markdown("Modelos con **menos de 4 piezas en total** en Piso de Ventas, cruzados con su existencia en bodega e inventario en sistema del ERP. Utiliza el selector por actividad para asignar tareas de resurtido a cada asesor.")
    
    lista_actividades = sorted(df_cruce['nivel2'].dropna().unique().tolist())
    opciones_filtro = ["Todas las Áreas"] + lista_actividades
    actividad_seleccionada = st.selectbox("🎯 Filtrar Resurtido por Área / Actividad (Responsable):", options=opciones_filtro)

    df_ref_pv = df_cruce.groupby(['modelo_base', 'descripcion', 'nivel2'])['cantidad'].sum().reset_index()
    df_picos = df_ref_pv[df_ref_pv['cantidad'] < 4].copy()
    
    if actividad_seleccionada != "Todas las Áreas":
        df_picos = df_picos[df_picos['nivel2'] == actividad_seleccionada]

    if not df_ubic_bodega.empty:
        df_cruce_bodega = pd.merge(df_ubic_bodega, df_cat, on="codigo_limpio", how="left")
        df_cruce_bodega['referencia'] = df_cruce_bodega['referencia'].fillna("Desconocida")
        df_cruce_bodega['modelo_base'] = df_cruce_bodega['referencia'].astype(str).apply(lambda x: str(x).split('-')[0].strip())
        
        df_bodega_totales = df_cruce_bodega.groupby('modelo_base')['cantidad'].sum().reset_index().rename(columns={'cantidad': 'piezas_bodega'})
        df_bodega_ubic = df_cruce_bodega.groupby('modelo_base')['ubicacion'].apply(lambda x: ", ".join(x.dropna().astype(str).unique())).reset_index().rename(columns={'ubicacion': 'Ubicaciones en Bodega'})
        df_bodega_totales = pd.merge(df_bodega_totales, df_bodega_ubic, on='modelo_base', how='left')
    else:
        df_bodega_totales = pd.DataFrame(columns=['modelo_base', 'piezas_bodega', 'Ubicaciones en Bodega'])
        
    if not df_cat.empty:
        df_cat['modelo_base'] = df_cat['referencia'].astype(str).apply(lambda x: str(x).split('-')[0].strip())
        df_sistema_totales = df_cat.groupby('modelo_base')['stock_sistema'].sum().reset_index().rename(columns={'stock_sistema': 'inventario_sistema'})
    else:
        df_sistema_totales = pd.DataFrame(columns=['modelo_base', 'inventario_sistema'])

    df_picos = pd.merge(df_picos, df_bodega_totales, on='modelo_base', how='left')
    df_picos = pd.merge(df_picos, df_sistema_totales, on='modelo_base', how='left')
    
    df_picos['piezas_bodega'] = df_picos['piezas_bodega'].fillna(0).astype(int)
    df_picos['Ubicaciones en Bodega'] = df_picos['Ubicaciones en Bodega'].fillna("No escaneado")
    df_picos['inventario_sistema'] = df_picos['inventario_sistema'].fillna(0).astype(int)
    df_picos = df_picos.sort_values(by="cantidad")
    
    if not df_picos.empty:
        df_tabla_final = df_picos.rename(columns={
            "modelo_base": "Modelo (Ref Base)", 
            "descripcion": "Descripción", 
            "nivel2": "Área / Actividad",
            "cantidad": "Piezas en Piso (PV)",
            "piezas_bodega": "Disponible en Bodega",
            "Ubicaciones en Bodega": "Ubicaciones en Bodega",
            "inventario_sistema": "Inventario en Sistema"
        })
        
        st.dataframe(
            df_tabla_final[['Modelo (Ref Base)', 'Descripción', 'Área / Actividad', 'Piezas en Piso (PV)', 'Disponible en Bodega', 'Ubicaciones en Bodega', 'Inventario en Sistema']],
            use_container_width=True,
            hide_index=True
        )
        st.info(f"Se detectaron {len(df_picos)} modelos listos para ruta de resurtido en el área seleccionada.")
    else:
        st.success("¡Excelente! No hay modelos con menos de 4 piezas en el área seleccionada.")

# ==========================================
# INTERFAZ PRINCIPAL CON PESTAÑAS
# ==========================================
nombre_usuario_actual = st.session_state.usuario_info.get('nombre_completo', '')
if not nombre_usuario_actual or nombre_usuario_actual == 'None':
    nombre_usuario_actual = st.session_state.usuario_actual

st.title(f"⚡ ¡Bienvenid@, {nombre_usuario_actual}!")

# --- LÓGICA DE FELICITACIÓN Y LIDERAZGO MULTI-KPI ---
if os.path.exists("ventas_diarias_temp.csv"):
    df_v_felicitacion = pd.read_csv("ventas_diarias_temp.csv")
    if not df_v_felicitacion.empty and 'codigo' in df_v_felicitacion.columns:
        codigo_erp_bd_actual = st.session_state.usuario_info.get('codigo_erp', '')
        if not codigo_erp_bd_actual or codigo_erp_bd_actual == 'None':
            codigo_erp_bd_actual = st.session_state.usuario_actual
        usuario_code_actual = str(codigo_erp_bd_actual).strip().lower()
        
        es_top_cualquiera = False
        
        if 'Neto_D_num' in df_v_felicitacion.columns:
            top_neto = df_v_felicitacion.loc[df_v_felicitacion['Neto_D_num'].idxmax()]
            if usuario_code_actual == str(top_neto['codigo']).strip().lower() and top_neto['Neto_D_num'] > 0:
                es_top_cualquiera = True
                if not st.session_state.felicitacion_mostrada:
                    st.balloons()
                    st.session_state.felicitacion_mostrada = True
                st.success(f"🏆 ¡Felicidades, {top_neto['nombre']}! Eres el primer lugar en ventas con ${top_neto['Neto_D_num']:,.2f}. ¡Sigue así, liderando la red!")

        if 'UPT_D_num' in df_v_felicitacion.columns:
            top_upt = df_v_felicitacion.loc[df_v_felicitacion['UPT_D_num'].idxmax()]
            if usuario_code_actual == str(top_upt['codigo']).strip().lower() and top_upt['UPT_D_num'] > 0:
                es_top_cualquiera = True
                st.success(f"🎯 ¡Felicidades, {top_upt['nombre']}! Eres el primer lugar en UPT con {top_upt['UPT_D_num']:,.2f} unidades por ticket. ¡Excelente trabajo!")

        if 'ASP_D_num' in df_v_felicitacion.columns:
            top_asp = df_v_felicitacion.loc[df_v_felicitacion['ASP_D_num'].idxmax()]
            if usuario_code_actual == str(top_asp['codigo']).strip().lower() and top_asp['ASP_D_num'] > 0:
                es_top_cualquiera = True
                st.success(f"💎 ¡Felicidades, {top_asp['nombre']}! Eres el primer lugar en ASP con un precio promedio de ${top_asp['ASP_D_num']:,.2f}. ¡Imparable!")

        if 'ATV_D_num' in df_v_felicitacion.columns:
            top_atv = df_v_felicitacion.loc[df_v_felicitacion['ATV_D_num'].idxmax()]
            if usuario_code_actual == str(top_atv['codigo']).strip().lower() and top_atv['ATV_D_num'] > 0:
                es_top_cualquiera = True
                st.success(f"🔥 ¡Felicidades, {top_atv['nombre']}! Eres el primer lugar en ATV con un ticket promedio de ${top_atv['ATV_D_num']:,.2f}. ¡Sigue liderando!")

        if not st.session_state.felicitacion_mostrada and es_top_cualquiera:
            st.balloons()
            st.session_state.felicitacion_mostrada = True

paginas_admin = {
    "📊 Operación": [
        "📊 Dashboard",
        "📈 Resumen PV",
        "📐 Rendimiento m²",
    ],
    "📦 Inventario y consulta": [
        "🔍 Búsqueda Manual",
        "📦 Scanner Rápido",
    ],
    "⚙️ Administración": [
        "📥 Cargas ERP e inventario",
        "💲 Lista de precios",
        "🏷️ Promociones",
        "🎯 Metas por asesor",
        "👥 Gestión Usuarios",
    ],
}

with st.sidebar:
    st.markdown("#### 🧭 Menú principal")
    if st.session_state.es_admin:
        seccion_menu = st.selectbox(
            "Sección",
            options=list(paginas_admin.keys()),
            key="seccion_menu_admin",
        )
        pagina_actual = st.radio(
            "Pantalla",
            options=paginas_admin[seccion_menu],
            key=f"pagina_menu_{seccion_menu}",
        )
    else:
        pagina_actual = st.radio(
            "Pantalla",
            options=["📊 Dashboard", "🔍 Búsqueda Manual"],
            key="pagina_menu_asesor",
        )

# ------------------------------------------
# 1. PESTAÑA: PERFORMANCE & KPIS (DASHBOARD)
# ------------------------------------------
if pagina_actual == "📊 Dashboard":
    if not os.path.exists("ventas_diarias_temp.csv"):
        st.header("📊 Tablero de Rendimiento Diario")
        st.info("ℹ️ No se ha cargado el reporte de ventas del día. El administrador puede subirlo en '📥 Cargas ERP e inventario'.")
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

        def crear_grafica_kpi(df, campo_y, titulo, promedio_tienda, formato):
            df_sorted = df.sort_values(campo_y, ascending=False).reset_index(drop=True)
            max_val = df_sorted[campo_y].max()
            df_sorted['Color'] = df_sorted[campo_y].apply(lambda x: '#39FF88' if x == max_val and x > 0 else '#00D9F5')
            
            orden_x = df_sorted['nombre'].tolist()
            base = alt.Chart(df_sorted).encode(x=alt.X('nombre:N', sort=orden_x, title='Asesor', axis=alt.Axis(labelAngle=-45)))
            bar = base.mark_bar(opacity=0.85, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                y=alt.Y(f'{campo_y}:Q', title=titulo),
                color=alt.Color('Color:N', scale=None),
                tooltip=[alt.Tooltip('nombre:N', title='Asesor'), alt.Tooltip(f'{campo_y}:Q', title=titulo, format=formato)]
            )
            df_sorted['_mitad'] = df_sorted[campo_y] / 2
            text = alt.Chart(df_sorted).mark_text(align='center', baseline='middle', angle=270, color='white', fontWeight='bold', fontSize=10).encode(
                x=alt.X('nombre:N', sort=orden_x),
                y=alt.Y('_mitad:Q'),
                text=alt.Text(f'{campo_y}:Q', format=formato)
            )
            rule = alt.Chart(pd.DataFrame({campo_y: [promedio_tienda]})).mark_rule(
                color='#FFD700', strokeWidth=3, strokeDash=[5, 5]
            ).encode(y=f'{campo_y}:Q')
            return (bar + text + rule).properties(height=320, title=f"{titulo}")

        if not st.session_state.es_admin:
            codigo_erp_bd = st.session_state.usuario_info.get('codigo_erp', '')
            if not codigo_erp_bd or codigo_erp_bd == 'None':
                codigo_erp_bd = st.session_state.usuario_actual
                
            usuario_code = str(codigo_erp_bd).strip().lower()
            user_row = df_v[df_v['codigo'].astype(str).str.strip().str.lower() == usuario_code]
            
            if not user_row.empty:
                row_asesor = user_row.iloc[0]
                nombre_asesor = row_asesor.get('nombre', usuario_code)
                venta_asesor_neto = row_asesor.get('Neto_D_num', 0.0)
                meta_asesor = row_asesor.get('meta_mensual', 282800.23)
                alcance_asesor_pct = (venta_asesor_neto / meta_asesor) * 100 if meta_asesor > 0 else 0
                falta_asesor = max(0.0, meta_asesor - venta_asesor_neto)
                
                max_venta_red = df_v['Neto_D_num'].max()
                diferencia_primer_lugar = max_venta_red - venta_asesor_neto
                if diferencia_primer_lugar > 0:
                    texto_primer_lugar = f"🎯 <b>Te faltan ${diferencia_primer_lugar:,.2f} para alcanzar al 1.er lugar en ventas</b>"
                else:
                    texto_primer_lugar = "👑 <b>¡Vas a la cabeza del primer lugar en ventas!</b>"

                meta_71 = meta_asesor * 0.71
                falta_71 = max(0.0, meta_71 - venta_asesor_neto)
                txt_71 = f"${falta_71:,.2f}" if falta_71 > 0 else "✅ ¡Alcanzado!"
                
                meta_81 = meta_asesor * 0.81
                falta_81 = max(0.0, meta_81 - venta_asesor_neto)
                txt_81 = f"${falta_81:,.2f}" if falta_81 > 0 else "✅ ¡Alcanzado!"
                
                meta_91 = meta_asesor * 0.91
                falta_91 = max(0.0, meta_91 - venta_asesor_neto)
                txt_91 = f"${falta_91:,.2f}" if falta_91 > 0 else "✅ ¡Alcanzado!"
                
                txt_100 = f"${falta_asesor:,.2f}" if falta_asesor > 0 else "🚀 ¡Meta Superada!"
                
                st.header("📊 Mi Tablero de Rendimiento")
                st.subheader(f"👤 MI PERFORMANCE ({nombre_asesor})")
                st.markdown(f"""
                <div class="kpi-card">
                    <h4>Mi Meta Mensual: ${meta_asesor:,.2f}</h4>
                    <p><b>Mi Venta Neta:</b> ${venta_asesor_neto:,.2f}</p>
                    <p><b>Mi Alcance:</b> {alcance_asesor_pct:.1f}%</p>
                    <p>{texto_primer_lugar}</p>
                    <hr style="border-color: rgba(57,255,136,0.2); margin: 10px 0;">
                    <p>🎯 <b>Falta para Primer Objetivo (71%):</b> {txt_71}</p>
                    <p>🎯 <b>Falta para Segundo Objetivo (81%):</b> {txt_81}</p>
                    <p>🎯 <b>Falta para Tercer Objetivo (91%):</b> {txt_91}</p>
                    <p>🏆 <b>Falta para Meta Final (100%):</b> {txt_100}</p>
                </div>
                """, unsafe_allow_html=True)
                st.progress(min(1.0, alcance_asesor_pct / 100))
                st.markdown("---")

        if st.session_state.es_admin:
            st.header("📊 Tablero Gerencial Diario")
        else:
            st.header("🏢 Tablero General de la Tienda")
            
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
        max_venta = df_chart['Neto_D_num'].max()
        df_chart['Color'] = df_chart['Neto_D_num'].apply(lambda x: '#39FF88' if x == max_venta and x > 0 else '#00D9F5')
        
        sort_vendedor = alt.EncodingSortField(field='Neto_D_num', op='sum', order='descending')
        bars = alt.Chart(df_chart).mark_bar(cornerRadiusTopLeft=6, cornerRadiusTopRight=6).encode(
            x=alt.X('nombre:N', sort=sort_vendedor, title='Asesor', axis=alt.Axis(labelAngle=-45)),
            y=alt.Y('Neto_D_num:Q', title='Venta Neta ($)'),
            color=alt.Color('Color:N', scale=None),
            tooltip=[alt.Tooltip('nombre:N', title='Asesor'), alt.Tooltip('Neto_D_num:Q', title='Venta Neta', format='$,.2f')]
        )
        text = bars.mark_text(align='center', baseline='bottom', dy=-5, color='white').encode(text=alt.Text('Neto_D_num:Q', format='$,.0f'))
        st.altair_chart((bars + text).properties(height=350), use_container_width=True)

        st.markdown("---")
        st.subheader("🎯 Radiografía Operativa: KPIs por Asesor vs Promedio Tienda")
        st.caption("🟡 La línea amarilla punteada indica el promedio general de la tienda (reportado por el ERP).")

        df_kpis = df_v[['nombre', 'UPT_D_num', 'ATV_D_num', 'ASP_D_num']].copy()
        
        kpi_col1, kpi_col2, kpi_col3 = st.columns(3)
        with kpi_col1:
            st.altair_chart(crear_grafica_kpi(df_kpis, 'UPT_D_num', 'UPT (Unidades x Ticket)', upt_tienda, '.2f'), use_container_width=True)
        with kpi_col2:
            st.altair_chart(crear_grafica_kpi(df_kpis, 'ATV_D_num', 'ATV (Ticket Promedio)', atv_tienda, '$,.0f'), use_container_width=True)
        with kpi_col3:
            st.altair_chart(crear_grafica_kpi(df_kpis, 'ASP_D_num', 'ASP (Precio Promedio)', asp_tienda, '$,.0f'), use_container_width=True)

# ------------------------------------------
# 2. PESTAÑA: BÚSQUEDA MANUAL
# ------------------------------------------
if pagina_actual == "🔍 Búsqueda Manual":
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
            df, avisos_precios = enriquecer_resultados_con_precios_promociones(df)

            st.success(f"⚡ Se conectaron {len(df)} artículos en la red.")

            promociones_encontradas = df["promocion"].dropna().astype(str).unique().tolist()
            if promociones_encontradas:
                st.success(
                    "🔥 Promoción vigente: " + ", ".join(promociones_encontradas)
                )
            for aviso_precio in avisos_precios:
                if st.session_state.es_admin:
                    st.warning(aviso_precio)

            st.markdown(
                construir_tarjetas_resultados(
                    df,
                    es_admin=st.session_state.es_admin,
                ),
                unsafe_allow_html=True,
            )

            if st.session_state.es_admin:
                with st.expander("Ver detalle técnico en tabla", expanded=False):
                    columnas_resultado = [
                        'codigo_limpio', 'referencia', 'descripcion', 'talla', 'nivel1',
                        'stock_sistema', 'ubicacion', 'precio_lista', 'promocion',
                        'descuento_porcentaje', 'precio_final'
                    ]
                    df_mostrar = df[columnas_resultado].rename(columns={
                        'codigo_limpio': 'Código',
                        'referencia': 'Referencia',
                        'descripcion': 'Descripción',
                        'talla': 'Talla',
                        'nivel1': 'Categoría',
                        'stock_sistema': 'Stock sistema',
                        'ubicacion': 'Ubicación',
                        'precio_lista': 'Precio de lista',
                        'promocion': 'Promoción',
                        'descuento_porcentaje': 'Descuento (%)',
                        'precio_final': 'Precio final',
                    })
                    st.dataframe(
                        df_mostrar,
                        use_container_width=True,
                        hide_index=True,
                        column_config={
                            "Precio de lista": st.column_config.NumberColumn(format="$%.2f"),
                            "Descuento (%)": st.column_config.NumberColumn(format="%.2f%%"),
                            "Precio final": st.column_config.NumberColumn(format="$%.2f"),
                        },
                    )
            
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
# 3. PESTAÑA: RESUMEN EJECUTIVO (PV) (SÓLO ADMIN)
# ------------------------------------------
if pagina_actual == "📈 Resumen PV":
    if st.session_state.es_admin:
        mostrar_resumen_piso_ventas(supabase)

# ------------------------------------------
# 4. PESTAÑA: RENDIMIENTO M2 (SÓLO ADMIN)
# ------------------------------------------
if pagina_actual == "📐 Rendimiento m²":
    if st.session_state.es_admin:
        st.header("📐 Rendimiento Monetario por Sub-ubicación ($ / m²)")
        st.markdown(
            "Carga tu reporte de ventas del ERP. El rendimiento se calcula siempre sobre el total "
            "de la tienda (**268.61 m²**)."
        )

        M2_TIENDA_TOTAL = 268.61
        archivo_ventas_ref = st.file_uploader(
            "📥 Subir: Extracto Referencia - Documento (Costo) [Excel]",
            type=["xlsx", "xls"],
            key="ventas_ref_m2"
        )

        if archivo_ventas_ref is not None:
            with st.spinner("Analizando rendimiento y ventas por categoría..."):
                try:
                    # --------------------------------------------------
                    # 1) LOCALIZAR LA FILA REAL DE ENCABEZADOS DEL ERP
                    # --------------------------------------------------
                    df_ventas = None
                    columnas_requeridas = {"Referencia", "Venta Neta"}

                    for h in range(15):
                        try:
                            archivo_ventas_ref.seek(0)
                            df_temp = pd.read_excel(archivo_ventas_ref, header=h)
                            df_temp.columns = df_temp.columns.astype(str).str.strip()

                            if columnas_requeridas.issubset(set(df_temp.columns)):
                                df_ventas = df_temp
                                break
                        except Exception:
                            continue

                    if df_ventas is None:
                        raise ValueError(
                            "No fue posible localizar las columnas 'Referencia' y 'Venta Neta' "
                            "en el reporte ERP."
                        )

                    df_ventas.columns = df_ventas.columns.astype(str).str.strip()
                    df_ventas = df_ventas.dropna(subset=["Referencia"]).copy()

                    # --------------------------------------------------
                    # 2) LIMPIEZA DE CAMPOS NUMÉRICOS
                    # --------------------------------------------------
                    def convertir_numero(serie):
                        return pd.to_numeric(
                            serie.astype(str)
                            .str.replace(r"[^0-9.\-]", "", regex=True),
                            errors="coerce"
                        ).fillna(0)

                    df_ventas["Venta Neta"] = convertir_numero(df_ventas["Venta Neta"])

                    if "Unidades" in df_ventas.columns:
                        df_ventas["Unidades"] = convertir_numero(df_ventas["Unidades"])
                    else:
                        # Se mantiene la aplicación funcional si un reporte futuro no trae unidades.
                        df_ventas["Unidades"] = 0

                    # --------------------------------------------------
                    # 3) SKU MAESTRO
                    #    La referencia ERP contiene SKU + talla.
                    #    Ejemplo: M20324-3 -> M20324
                    # --------------------------------------------------
                    df_ventas["sku_maestro"] = (
                        df_ventas["Referencia"]
                        .astype(str)
                        .str.strip()
                        .str.upper()
                        .str[:6]
                    )

                    total_venta_erp = float(df_ventas["Venta Neta"].sum())
                    rendimiento_global = total_venta_erp / M2_TIENDA_TOTAL

                    # ==================================================
                    # TARJETAS PRINCIPALES: SÓLO 3 MÉTRICAS
                    # ==================================================
                    st.divider()
                    c1, c2, c3 = st.columns(3)

                    c1.metric("💰 Venta Total ERP", f"${total_venta_erp:,.2f}")
                    c2.metric("📐 M² Totales de la Tienda", f"{M2_TIENDA_TOTAL:,.2f} m²")
                    c3.metric(
                        "📈 Rendimiento Global",
                        f"${rendimiento_global:,.2f} / m²"
                    )

                    # ==================================================
                    # RESUMEN FOOTWEAR
                    # El reporte ERP real ya trae Nivel 1, Nivel 2,
                    # Unidades y Venta Neta; no necesita depender de
                    # ubicaciones físicas ni del escaneo de cada zapato.
                    # ==================================================
                    st.divider()
                    st.subheader("👟 Resumen de Ventas Footwear")
                    st.caption(
                        "Las ventas de calzado se obtienen directamente del reporte ERP y se "
                        "clasifican por Nivel 1 / Nivel 2. El rendimiento se calcula como "
                        "Venta Neta ÷ 268.61 m²."
                    )

                    if "Nivel 1" in df_ventas.columns and "Nivel 2" in df_ventas.columns:
                        nivel1 = (
                            df_ventas["Nivel 1"]
                            .astype(str)
                            .str.strip()
                            .str.upper()
                        )
                        nivel2 = (
                            df_ventas["Nivel 2"]
                            .astype(str)
                            .str.strip()
                            .str.upper()
                        )

                        df_footwear = df_ventas[nivel1.eq("FOOTWEAR")].copy()
                        nivel2_fw = (
                            df_footwear["Nivel 2"]
                            .astype(str)
                            .str.strip()
                            .str.upper()
                        )

                        # UNISEX primero para evitar clasificaciones ambiguas.
                        clasificacion_fw = pd.Series("OTROS", index=df_footwear.index)
                        clasificacion_fw.loc[nivel2_fw.str.contains("UNISEX", na=False)] = "Calzado Unisex"
                        clasificacion_fw.loc[
                            nivel2_fw.str.contains(r"\bWOMEN\b", regex=True, na=False)
                        ] = "Calzado Mujer"
                        clasificacion_fw.loc[
                            nivel2_fw.str.contains(r"\bMEN\b", regex=True, na=False)
                        ] = "Calzado Hombre"

                        df_footwear["Categoria_Footwear"] = clasificacion_fw

                        categorias_fw = [
                            "Calzado Hombre",
                            "Calzado Mujer",
                            "Calzado Unisex"
                        ]

                        resumen_fw = (
                            df_footwear[
                                df_footwear["Categoria_Footwear"].isin(categorias_fw)
                            ]
                            .groupby("Categoria_Footwear", as_index=False)
                            .agg(
                                Piezas_Vendidas=("Unidades", "sum"),
                                Venta_Neta=("Venta Neta", "sum")
                            )
                        )

                        # Asegurar que siempre aparezcan las tres categorías.
                        df_categorias = pd.DataFrame(
                            {"Categoria_Footwear": categorias_fw}
                        )
                        resumen_fw = df_categorias.merge(
                            resumen_fw,
                            on="Categoria_Footwear",
                            how="left"
                        ).fillna({
                            "Piezas_Vendidas": 0,
                            "Venta_Neta": 0
                        })

                        resumen_fw["Piezas_Vendidas"] = (
                            resumen_fw["Piezas_Vendidas"].round().astype(int)
                        )
                        resumen_fw["Rendimiento_m2"] = (
                            resumen_fw["Venta_Neta"] / M2_TIENDA_TOTAL
                        )

                        fw_cols = st.columns(3)
                        iconos_fw = {
                            "Calzado Hombre": "👨",
                            "Calzado Mujer": "👩",
                            "Calzado Unisex": "👟"
                        }

                        for col, categoria in zip(fw_cols, categorias_fw):
                            fila = resumen_fw[
                                resumen_fw["Categoria_Footwear"] == categoria
                            ].iloc[0]

                            with col:
                                st.markdown(f"### {iconos_fw[categoria]} {categoria}")
                                st.metric(
                                    "Piezas Vendidas",
                                    f"{int(fila['Piezas_Vendidas']):,}"
                                )
                                st.metric(
                                    "Venta Neta",
                                    f"${fila['Venta_Neta']:,.2f}"
                                )
                                st.metric(
                                    "Rendimiento",
                                    f"${fila['Rendimiento_m2']:,.2f} / m²"
                                )

                        st.markdown("#### 📋 Detalle de Ventas Footwear")
                        df_fw_mostrar = resumen_fw.rename(columns={
                            "Categoria_Footwear": "Categoría",
                            "Piezas_Vendidas": "Piezas Vendidas",
                            "Venta_Neta": "Venta Neta ($)",
                            "Rendimiento_m2": "Rendimiento por M² ($)"
                        })

                        st.dataframe(
                            df_fw_mostrar.style.format({
                                "Piezas Vendidas": "{:,.0f}",
                                "Venta Neta ($)": "${:,.2f}",
                                "Rendimiento por M² ($)": "${:,.2f}"
                            }),
                            use_container_width=True,
                            hide_index=True
                        )
                    else:
                        st.warning(
                            "El reporte cargado no contiene las columnas 'Nivel 1' y 'Nivel 2', "
                            "por lo que no fue posible generar el resumen Footwear."
                        )

                    # ==================================================
                    # RENDIMIENTO POR UBICACIÓN / MUEBLE
                    # ==================================================
                    st.divider()
                    st.subheader("📊 Rendimiento por Sub-ubicación")

                    # Catálogo: EAN/código -> Referencia -> SKU maestro.
                    res_catalogo = supabase.table("catalogo_erp").select(
                        "codigo_limpio,referencia"
                    ).execute()
                    df_catalogo = pd.DataFrame(res_catalogo.data)

                    res_ubic_all = supabase.table("ubicaciones").select("*").execute()
                    df_ubic_all = pd.DataFrame(res_ubic_all.data)

                    if df_ubic_all.empty:
                        st.warning("No hay ubicaciones escaneadas en la red aún.")
                    elif df_catalogo.empty:
                        st.warning(
                            "No fue posible obtener el catálogo ERP para relacionar los EAN con sus SKUs."
                        )
                    else:
                        if "ubicacion" not in df_ubic_all.columns:
                            raise ValueError("La tabla de ubicaciones no contiene la columna 'ubicacion'.")
                        if "codigo_limpio" not in df_ubic_all.columns:
                            raise ValueError("La tabla de ubicaciones no contiene la columna 'codigo_limpio'.")
                        if "cantidad" not in df_ubic_all.columns:
                            raise ValueError("La tabla de ubicaciones no contiene la columna 'cantidad'.")

                        ubi_up = (
                            df_ubic_all["ubicacion"]
                            .astype(str)
                            .str.strip()
                            .str.upper()
                        )
                        is_pv = (
                            ubi_up.eq("PV")
                            | ubi_up.str.startswith("PV -")
                            | ubi_up.str.startswith("PV-")
                        )
                        df_pv = df_ubic_all[is_pv].copy()

                        if df_pv.empty:
                            st.warning(
                                "No hay productos con ubicaciones de Piso de Ventas (PV) registrados."
                            )
                        else:
                            # Normalización de EAN/código para el join.
                            df_pv["codigo_match"] = (
                                df_pv["codigo_limpio"]
                                .astype(str)
                                .str.strip()
                                .str.replace(r"^['|]+", "", regex=True)
                            )
                            df_catalogo["codigo_match"] = (
                                df_catalogo["codigo_limpio"]
                                .astype(str)
                                .str.strip()
                                .str.replace(r"^['|]+", "", regex=True)
                            )

                            df_catalogo["sku_maestro"] = (
                                df_catalogo["referencia"]
                                .astype(str)
                                .str.strip()
                                .str.upper()
                                .str[:6]
                            )

                            # Evitar duplicar ubicaciones si el catálogo contiene repetidos.
                            df_catalogo_join = (
                                df_catalogo[
                                    ["codigo_match", "referencia", "sku_maestro"]
                                ]
                                .drop_duplicates(subset=["codigo_match"])
                            )

                            df_pv = df_pv.merge(
                                df_catalogo_join,
                                on="codigo_match",
                                how="left"
                            )

                            # Fallback: si el código escaneado ya parece una referencia,
                            # conservar los primeros 6 caracteres como SKU maestro.
                            df_pv["sku_maestro"] = df_pv["sku_maestro"].fillna(
                                df_pv["codigo_match"]
                                .astype(str)
                                .str.strip()
                                .str.upper()
                                .str[:6]
                            )

                            # Ventas agrupadas por SKU maestro.
                            df_v_agrup = (
                                df_ventas.groupby("sku_maestro", as_index=False)
                                .agg(
                                    Venta_Neta=("Venta Neta", "sum"),
                                    Piezas_Vendidas=("Unidades", "sum")
                                )
                            )

                            # Inventario físico total por SKU en todas las ubicaciones PV.
                            totales_por_sku = (
                                df_pv.groupby("sku_maestro", as_index=False)["cantidad"]
                                .sum()
                                .rename(columns={"cantidad": "total_piezas_pv"})
                            )

                            df_pv = df_pv.merge(
                                totales_por_sku,
                                on="sku_maestro",
                                how="left"
                            )
                            df_pv = df_pv.merge(
                                df_v_agrup,
                                on="sku_maestro",
                                how="left"
                            )

                            df_pv["Venta_Neta"] = df_pv["Venta_Neta"].fillna(0)
                            df_pv["Piezas_Vendidas"] = df_pv["Piezas_Vendidas"].fillna(0)
                            df_pv["total_piezas_pv"] = (
                                df_pv["total_piezas_pv"].replace(0, 1).fillna(1)
                            )

                            # Prorrateo proporcional cuando un SKU está en varios muebles.
                            df_pv["factor_atribucion"] = (
                                df_pv["cantidad"] / df_pv["total_piezas_pv"]
                            )
                            df_pv["venta_atribuida"] = (
                                df_pv["Venta_Neta"] * df_pv["factor_atribucion"]
                            )
                            df_pv["piezas_vendidas_atribuidas"] = (
                                df_pv["Piezas_Vendidas"] * df_pv["factor_atribucion"]
                            )

                            resumen_muebles = (
                                df_pv.groupby("ubicacion", as_index=False)
                                .agg(
                                    SKUs_Distintos=("sku_maestro", "nunique"),
                                    Piezas_Fisicas=("cantidad", "sum"),
                                    Piezas_Vendidas=("piezas_vendidas_atribuidas", "sum"),
                                    Venta_Total=("venta_atribuida", "sum")
                                )
                            )

                            resumen_muebles["Rendimiento_m2"] = (
                                resumen_muebles["Venta_Total"] / M2_TIENDA_TOTAL
                            )
                            resumen_muebles = resumen_muebles.sort_values(
                                "Rendimiento_m2",
                                ascending=False
                            ).reset_index(drop=True)

                            chart_m2 = alt.Chart(resumen_muebles).mark_bar(
                                cornerRadiusTopLeft=4,
                                cornerRadiusTopRight=4
                            ).encode(
                                x=alt.X(
                                    "ubicacion:N",
                                    sort="-y",
                                    title="Ubicación en Piso de Ventas",
                                    axis=alt.Axis(labelAngle=-45)
                                ),
                                y=alt.Y(
                                    "Rendimiento_m2:Q",
                                    title="Rendimiento ($ / m²)"
                                ),
                                tooltip=[
                                    alt.Tooltip("ubicacion:N", title="Mueble/Área"),
                                    alt.Tooltip("SKUs_Distintos:Q", title="SKUs Diferentes"),
                                    alt.Tooltip("Piezas_Fisicas:Q", title="Total Piezas"),
                                    alt.Tooltip(
                                        "Piezas_Vendidas:Q",
                                        title="Piezas Vendidas",
                                        format=",.2f"
                                    ),
                                    alt.Tooltip(
                                        "Venta_Total:Q",
                                        title="Venta Generada ($)",
                                        format=",.2f"
                                    ),
                                    alt.Tooltip(
                                        "Rendimiento_m2:Q",
                                        title="Rendimiento ($/m²)",
                                        format=",.2f"
                                    )
                                ]
                            )

                            st.altair_chart(
                                chart_m2.properties(height=450),
                                use_container_width=True
                            )

                            st.markdown("### 📋 Desglose Completo de Sub-ubicaciones")

                            df_mostrar = resumen_muebles.rename(columns={
                                "ubicacion": "Ubicación / Mueble",
                                "SKUs_Distintos": "SKUs Diferentes Exhibidos",
                                "Piezas_Fisicas": "Total Piezas",
                                "Piezas_Vendidas": "Total Piezas Vendidas",
                                "Venta_Total": "Venta Generada ($)",
                                "Rendimiento_m2": "Rendimiento por M² de Tienda ($)"
                            }).copy()

                            df_mostrar["Total Piezas Vendidas"] = (
                                df_mostrar["Total Piezas Vendidas"].round(2)
                            )

                            st.dataframe(
                                df_mostrar.style.format({
                                    "Total Piezas": "{:,.0f}",
                                    "Total Piezas Vendidas": "{:,.2f}",
                                    "Venta Generada ($)": "${:,.2f}",
                                    "Rendimiento por M² de Tienda ($)": "${:,.2f}"
                                }),
                                use_container_width=True,
                                hide_index=True
                            )

                except Exception as e:
                    st.error(
                        "⚠️ Hubo un error al procesar el archivo. "
                        f"Detalle técnico: {e}"
                    )


# ------------------------------------------
# 5. PESTAÑA: SCANNER RÁPIDO (SÓLO ADMIN)
# ------------------------------------------
if pagina_actual == "📦 Scanner Rápido":
    if st.session_state.es_admin:
        codigo = st.text_input("Escanea o escribe el código de barras:", key="scan")
        if codigo:
            prod = supabase.table("catalogo_erp").select("*").eq("codigo_limpio", codigo.strip()).execute().data
            if prod:
                p = prod[0]
                st.success(f"Producto: {p.get('descripcion')} | Ref: {p.get('referencia')} | Stock: {p.get('stock_sistema')}")
            else:
                st.warning("Producto no encontrado en el núcleo.")

# ------------------------------------------
# 6. PESTAÑA: CARGAS ERP E INVENTARIO (SÓLO ADMIN)
# ------------------------------------------
if pagina_actual == "📥 Cargas ERP e inventario":
    if st.session_state.es_admin:
        st.subheader("📥 Cargar Reporte de Ventas Diario del ERP (Excel)")
        archivo_sales = st.file_uploader("Subir Archivo Excel de Ventas", type=["xlsx", "xls"], key="sales_excel")
        
        if archivo_sales is not None:
            try:
                df_raw = pd.read_excel(archivo_sales, header=2)
                df_raw = df_raw[df_raw['No. Línea'].notna() & (df_raw['No. Línea'].astype(str).str.strip().str.upper() != 'TOTALES')]
                
                df_raw['codigo'] = df_raw['Código de Vendedor'].astype(str).str.strip()
                df_raw['nombre'] = df_raw['Vendedor'].astype(str).str.strip()
                
                df_raw['Neto_D_num'] = pd.to_numeric(df_raw['Venta Neta'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                df_raw['UPT_D_num'] = pd.to_numeric(df_raw['Items x Doc.'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                df_raw['ATV_D_num'] = pd.to_numeric(df_raw['Venta x Documento'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                df_raw['ASP_D_num'] = pd.to_numeric(df_raw['Precio x Unidad'].astype(str).str.replace(',', ''), errors='coerce').fillna(0)
                
                df_raw_totales = pd.read_excel(archivo_sales, header=2)
                row_totales = df_raw_totales[df_raw_totales['No. Línea'].astype(str).str.strip().str.upper() == 'TOTALES']
                
                if not row_totales.empty:
                    df_raw['Neto_T_num'] = float(str(row_totales.iloc[0]['Venta Neta']).replace(',', ''))
                    df_raw['UPT_T_num'] = float(str(row_totales.iloc[0]['Items x Doc.']).replace(',', ''))
                    df_raw['ATV_T_num'] = float(str(row_totales.iloc[0]['Venta x Documento']).replace(',', ''))
                    df_raw['ASP_T_num'] = float(str(row_totales.iloc[0]['Precio x Unidad']).replace(',', ''))
                else:
                    df_raw['Neto_T_num'] = df_raw['Neto_D_num'].sum()
                    df_raw['UPT_T_num'] = df_raw['UPT_D_num'].mean()
                    df_raw['ATV_T_num'] = df_raw['ATV_D_num'].mean()
                    df_raw['ASP_T_num'] = df_raw['ASP_D_num'].mean()
                
                df_raw.to_csv("ventas_diarias_temp.csv", index=False)
                st.session_state.felicitacion_mostrada = False
                
                st.success("✅ Reporte de ventas en Excel procesado por la red correctamente.")
                st.info("👉 Ahora puedes ir a la pestaña '📊 Dashboard' para ver los resultados actualizados.")
            except Exception as ex:
                st.error(f"Error al procesar el archivo Excel: {ex}")
        
        st.markdown("---")
        st.subheader("📦 Cargar Catálogo de Inventario al Núcleo")
        st.info("Sube aquí el archivo de tu ERP (RPInv_Extracto_Referencia) en formato Excel para sincronizar la red en Supabase.")

        archivo_catalogo = st.file_uploader("Subir Catálogo (.xlsx)", type=["xlsx"], key="cat_csv")

        if archivo_catalogo is not None:
            if st.button("⚡ Sincronizar Catálogo en la Nube"):
                try:
                    with st.spinner("Estableciendo sinapsis y sincronizando el núcleo... Esto puede tomar unos segundos."):
                        contenido_catalogo = archivo_catalogo.getvalue()
                        df_cat, referencias_activas_catalogo, validacion_catalogo = preparar_catalogo_inventario_excel(
                            io.BytesIO(contenido_catalogo)
                        )
                        columnas_obligatorias = {"codigo_limpio", "referencia", "stock_sistema"}
                        columnas_faltantes = columnas_obligatorias - set(df_cat.columns)
                        if columnas_faltantes:
                            raise ValueError(
                                "El catálogo no contiene las columnas necesarias: "
                                + ", ".join(sorted(columnas_faltantes))
                            )
                        
                        columnas_esperadas = ['codigo_limpio', 'referencia', 'descripcion', 'talla', 'nivel1', 'nivel2', 'nivel3', 'nivel4', 'stock_sistema']
                        columnas_existentes = [col for col in columnas_esperadas if col in df_cat.columns]
                        df_cat = df_cat[columnas_existentes]
                        
                        if 'codigo_limpio' in df_cat.columns:
                            df_cat = df_cat.dropna(subset=['codigo_limpio'])
                            df_cat['codigo_limpio'] = df_cat['codigo_limpio'].astype(str).str.strip()
                            df_cat = df_cat[df_cat['codigo_limpio'].ne('')]
                        
                        df_cat = df_cat.astype(object).where(pd.notna(df_cat), None)
                        registros = df_cat.to_dict(orient="records")

                        TAMANO_BLOQUE = 500
                        total_registros = len(registros)
                        if total_registros == 0:
                            raise ValueError("El catálogo no contiene registros válidos para sincronizar.")

                        codigos_catalogo_anterior = obtener_codigos_catalogo_supabase()
                        codigos_catalogo_nuevo = {
                            str(registro["codigo_limpio"]).strip()
                            for registro in registros
                            if registro.get("codigo_limpio")
                        }
                        
                        barra_progreso = st.progress(0, text=f"Sincronizando 0 de {total_registros} artículos...")

                        for i in range(0, total_registros, TAMANO_BLOQUE):
                            bloque = registros[i:i + TAMANO_BLOQUE]
                            supabase.table("catalogo_erp").upsert(bloque).execute()

                            subidos = min(i + TAMANO_BLOQUE, total_registros)
                            porcentaje = subidos / total_registros
                            barra_progreso.progress(porcentaje, text=f"Sincronizando {subidos} de {total_registros} artículos...")

                        barra_progreso.empty()
                        codigos_ausentes = sorted(codigos_catalogo_anterior - codigos_catalogo_nuevo)
                        total_ausentes = len(codigos_ausentes)
                        if total_ausentes:
                            barra_limpieza = st.progress(
                                0,
                                text=f"Actualizando a cero 0 de {total_ausentes} artículos antiguos...",
                            )
                            tamano_bloque_limpieza = 200
                            for inicio in range(0, total_ausentes, tamano_bloque_limpieza):
                                bloque_ausentes = codigos_ausentes[inicio:inicio + tamano_bloque_limpieza]
                                (
                                    supabase.table("catalogo_erp")
                                    .update({"stock_sistema": 0})
                                    .in_("codigo_limpio", bloque_ausentes)
                                    .execute()
                                )
                                actualizados = min(inicio + tamano_bloque_limpieza, total_ausentes)
                                barra_limpieza.progress(
                                    actualizados / total_ausentes,
                                    text=(
                                        f"Actualizando a cero {actualizados} de "
                                        f"{total_ausentes} artículos antiguos..."
                                    ),
                                )
                            barra_limpieza.empty()

                        st.session_state.inventario_actual_precios = {
                            "archivo": hashlib.sha256(contenido_catalogo).hexdigest(),
                            "referencias": referencias_activas_catalogo,
                            "validacion": validacion_catalogo,
                        }
                        st.success(
                            f"⚡ ¡Núcleo actualizado! Se sincronizaron {total_registros} artículos "
                            f"y {total_ausentes} códigos antiguos quedaron con existencia cero."
                        )
                except Exception as ex:
                    st.error(f"⚠️ Error al sincronizar: {ex}")

# ------------------------------------------
# 7. PESTAÑA: LISTA DE PRECIOS (SÓLO ADMIN)
# ------------------------------------------
if pagina_actual == "💲 Lista de precios":
    if st.session_state.es_admin:
        st.subheader("💲 Actualizar Lista de Precios ERP")
        st.info(
            "Carga la lista de precios en formato Excel (.xlsx). Se utilizarán automáticamente "
            "las referencias con existencia del catálogo sincronizado arriba durante esta sesión."
        )
        archivo_precios = st.file_uploader(
            "Subir Lista de Precios (.xlsx)",
            type=["xlsx"],
            key="price_list_excel",
        )
        inventario_actual_precios = st.session_state.get("inventario_actual_precios")
        if inventario_actual_precios:
            st.success(
                f"Catálogo actual disponible: "
                f"{inventario_actual_precios['validacion']['referencias_con_existencia']} "
                "referencias con existencia."
            )
        else:
            st.warning(
                "Primero sincroniza el catálogo de inventario de arriba en esta misma sesión "
                "para habilitar el cruce de precios."
            )

        if archivo_precios is not None and inventario_actual_precios:
            try:
                contenido_precios = archivo_precios.getvalue()
                identificador_archivo = hashlib.sha256(
                    contenido_precios + inventario_actual_precios["archivo"].encode("ascii")
                ).hexdigest()
                cache_precios = st.session_state.get("lista_precios_procesada")

                if not cache_precios or cache_precios["archivo"] != identificador_archivo:
                    with st.spinner("Leyendo precios y cruzando productos con existencia..."):
                        df_precios_completa, validacion_precios = preparar_lista_precios_excel(
                            io.BytesIO(contenido_precios)
                        )
                        referencias_con_existencia = inventario_actual_precios["referencias"]
                        validacion_inventario = inventario_actual_precios["validacion"]
                        referencias_con_precio = set(df_precios_completa["referencia"])
                        df_precios = df_precios_completa[
                            df_precios_completa["referencia"].isin(referencias_con_existencia)
                        ].copy()
                        validacion_precios.update(validacion_inventario)
                        validacion_precios["activos_sin_precio"] = len(
                            referencias_con_existencia - referencias_con_precio
                        )
                        validacion_precios["registros_a_sincronizar"] = len(df_precios)
                        st.session_state.lista_precios_procesada = {
                            "archivo": identificador_archivo,
                            "datos": df_precios,
                            "validacion": validacion_precios,
                        }
                else:
                    df_precios = cache_precios["datos"]
                    validacion_precios = cache_precios["validacion"]

                st.caption(
                    f"Encabezados detectados: precios en fila {validacion_precios['fila_encabezado']} "
                    f"e inventario en fila {validacion_precios['fila_encabezado_inventario']}."
                )
                col_p1, col_p2, col_p3, col_p4, col_p5 = st.columns(5)
                col_p1.metric("Precios válidos", validacion_precios["registros_validos"])
                col_p2.metric("Con existencia", validacion_precios["referencias_con_existencia"])
                col_p3.metric(
                    "A sincronizar",
                    validacion_precios["registros_a_sincronizar"],
                )
                col_p4.metric("Activos sin precio", validacion_precios["activos_sin_precio"])
                col_p5.metric(
                    "Filas inválidas",
                    validacion_precios["referencias_vacias"] + validacion_precios["precios_invalidos"],
                )

                st.markdown("**Vista previa de productos con existencia que se sincronizarán**")
                st.dataframe(df_precios.head(100), use_container_width=True, hide_index=True)
                if len(df_precios) > 100:
                    st.caption(f"Se muestran 100 de {len(df_precios)} registros que se sincronizarán.")
                if validacion_precios["activos_sin_precio"]:
                    st.warning(
                        f"{validacion_precios['activos_sin_precio']} referencias con existencia no tienen "
                        "un precio coincidente en el archivo y no se sincronizarán."
                    )

                confirmar_precios = st.checkbox(
                    "Confirmo que revisé la vista previa y deseo actualizar `lista_precios`.",
                    key="confirm_price_upload",
                )
                if st.button(
                    "⚡ Sincronizar Lista de Precios",
                    disabled=not confirmar_precios or df_precios.empty,
                    key="sync_price_list",
                ):
                    fecha_actualizacion = datetime.now(timezone.utc).isoformat()
                    df_precios_subida = df_precios.assign(fecha_actualizacion=fecha_actualizacion)
                    df_precios_subida = df_precios_subida.astype(object).where(pd.notna(df_precios_subida), None)
                    registros_precios = df_precios_subida.to_dict(orient="records")
                    tamano_bloque_precios = 500
                    total_precios = len(registros_precios)
                    barra_precios = st.progress(0, text=f"Sincronizando 0 de {total_precios} precios...")

                    for inicio in range(0, total_precios, tamano_bloque_precios):
                        bloque_precios = registros_precios[inicio:inicio + tamano_bloque_precios]
                        supabase.table("lista_precios").upsert(
                            bloque_precios,
                            on_conflict="referencia",
                        ).execute()
                        procesados = min(inicio + tamano_bloque_precios, total_precios)
                        barra_precios.progress(
                            procesados / total_precios,
                            text=f"Sincronizando {procesados} de {total_precios} precios...",
                        )

                    st.success(f"✅ Lista de precios actualizada: {total_precios} registros sincronizados.")
            except Exception as ex:
                st.error(f"⚠️ No se pudo procesar la lista de precios: {ex}")

# ------------------------------------------
# 8. PESTAÑA: PROMOCIONES (SÓLO ADMIN)
# ------------------------------------------
if pagina_actual == "🏷️ Promociones":
    if st.session_state.es_admin:
        st.subheader("🏷️ Crear Nueva Promoción")
        st.info(
            "Define la campaña y carga un Excel con una columna Modelo, SKU o Referencia. "
            "Puedes aplicar un descuento general o leer descuentos individuales desde el archivo."
        )

        col_prom_nombre, col_prom_activa = st.columns([3, 1])
        nombre_promocion = col_prom_nombre.text_input(
            "Nombre de la promoción",
            placeholder="Ejemplo: ADIFEST",
            key="promotion_name",
        )
        promocion_activa = col_prom_activa.checkbox(
            "Promoción activa",
            value=True,
            key="promotion_active",
        )
        descripcion_promocion = st.text_area(
            "Descripción opcional",
            key="promotion_description",
        )
        col_fecha_inicio, col_fecha_fin = st.columns(2)
        fecha_inicio_promocion = col_fecha_inicio.date_input(
            "Fecha de inicio",
            value=datetime.now().date(),
            key="promotion_start_date",
        )
        fecha_fin_promocion = col_fecha_fin.date_input(
            "Fecha de término",
            value=datetime.now().date(),
            key="promotion_end_date",
        )
        modo_descuento = st.radio(
            "Forma de aplicar el descuento",
            ["Descuento general", "Descuentos individuales desde Excel"],
            horizontal=True,
            key="promotion_discount_mode",
        )
        descuento_general = None
        if modo_descuento == "Descuento general":
            descuento_general = st.number_input(
                "Descuento general (%)",
                min_value=0.0,
                max_value=100.0,
                value=40.0,
                step=1.0,
                key="promotion_general_discount",
            )
        else:
            st.caption(
                "El Excel debe incluir una columna Descuento, % Descuento o Porcentaje."
            )

        archivo_promocion = st.file_uploader(
            "Subir productos de la promoción (.xlsx)",
            type=["xlsx"],
            key="promotion_products_excel",
        )

        if archivo_promocion is not None:
            try:
                df_productos_promocion, validacion_promocion = preparar_productos_promocion_excel(
                    io.BytesIO(archivo_promocion.getvalue()),
                    descuento_general=descuento_general,
                )
                st.caption(
                    f"Encabezado detectado en la fila {validacion_promocion['fila_encabezado']}."
                )
                col_vp1, col_vp2, col_vp3, col_vp4 = st.columns(4)
                col_vp1.metric("Filas leídas", validacion_promocion["filas_leidas"])
                col_vp2.metric("Modelos válidos", validacion_promocion["modelos_validos"])
                col_vp3.metric("Duplicados", validacion_promocion["duplicados_descartados"])
                col_vp4.metric(
                    "Filas inválidas",
                    validacion_promocion["modelos_vacios"]
                    + validacion_promocion["descuentos_invalidos"],
                )
                st.markdown("**Vista previa de la promoción**")
                st.dataframe(
                    df_productos_promocion.head(100),
                    use_container_width=True,
                    hide_index=True,
                )
                if len(df_productos_promocion) > 100:
                    st.caption(
                        f"Se muestran 100 de {len(df_productos_promocion)} modelos válidos."
                    )

                confirmar_promocion = st.checkbox(
                    "Confirmo que revisé los datos y deseo crear esta promoción en Supabase.",
                    key="confirm_promotion_upload",
                )
                if st.button(
                    "🏷️ Crear Promoción",
                    disabled=not confirmar_promocion or df_productos_promocion.empty,
                    key="create_promotion",
                ):
                    nombre_limpio = nombre_promocion.strip()
                    if not nombre_limpio:
                        st.error("Escribe un nombre para la promoción.")
                    elif fecha_fin_promocion < fecha_inicio_promocion:
                        st.error("La fecha de término no puede ser anterior a la fecha de inicio.")
                    else:
                        promocion_creada_id = None
                        try:
                            respuesta_promocion = supabase.table("promociones").insert({
                                "nombre": nombre_limpio,
                                "descripcion": descripcion_promocion.strip() or None,
                                "fecha_inicio": fecha_inicio_promocion.isoformat(),
                                "fecha_fin": fecha_fin_promocion.isoformat(),
                                "activa": promocion_activa,
                            }).execute()
                            if not respuesta_promocion.data:
                                raise ValueError("Supabase no devolvió el identificador de la promoción.")
                            promocion_creada_id = respuesta_promocion.data[0]["id"]
                            registros_promocion = (
                                df_productos_promocion[["sku_maestro", "descuento_porcentaje"]]
                                .assign(promocion_id=promocion_creada_id)
                                .astype(object)
                                .where(pd.notna, None)
                                .to_dict(orient="records")
                            )
                            total_productos_promocion = len(registros_promocion)
                            tamano_bloque_promocion = 500
                            barra_promocion = st.progress(
                                0,
                                text=f"Guardando 0 de {total_productos_promocion} modelos...",
                            )
                            for inicio in range(0, total_productos_promocion, tamano_bloque_promocion):
                                bloque_promocion = registros_promocion[
                                    inicio:inicio + tamano_bloque_promocion
                                ]
                                supabase.table("promocion_productos").upsert(
                                    bloque_promocion,
                                    on_conflict="promocion_id,sku_maestro",
                                ).execute()
                                guardados = min(
                                    inicio + tamano_bloque_promocion,
                                    total_productos_promocion,
                                )
                                barra_promocion.progress(
                                    guardados / total_productos_promocion,
                                    text=(
                                        f"Guardando {guardados} de "
                                        f"{total_productos_promocion} modelos..."
                                    ),
                                )
                            barra_promocion.empty()
                            st.success(
                                f"✅ Promoción '{nombre_limpio}' creada con "
                                f"{total_productos_promocion} modelos."
                            )
                        except Exception:
                            if promocion_creada_id:
                                try:
                                    supabase.table("promociones").delete().eq(
                                        "id", promocion_creada_id
                                    ).execute()
                                except Exception:
                                    pass
                            raise
            except Exception as ex:
                st.error(f"⚠️ No se pudo preparar o crear la promoción: {ex}")

        st.markdown("---")
        st.subheader("📋 Administrar Promociones")
        st.caption(
            "Consulta el historial, ajusta las fechas o cambia el estado sin borrar campañas ni productos."
        )
        try:
            promociones_registradas, conteos_promociones = obtener_promociones_con_conteos()
            if not promociones_registradas:
                st.info("Todavía no hay promociones registradas.")
            else:
                for promocion in promociones_registradas:
                    promocion_id = str(promocion["id"])
                    estado_promocion, icono_estado = clasificar_estado_promocion(promocion)
                    cantidad_modelos = conteos_promociones.get(promocion_id, 0)
                    titulo_promocion = (
                        f"{icono_estado} {promocion['nombre']} · {estado_promocion} · "
                        f"{cantidad_modelos:,} modelos"
                    )
                    with st.expander(titulo_promocion, expanded=False):
                        if promocion.get("descripcion"):
                            st.write(promocion["descripcion"])
                        col_estado, col_modelos = st.columns(2)
                        col_estado.metric("Estado", estado_promocion)
                        col_modelos.metric("Modelos", cantidad_modelos)

                        fecha_inicio_actual = pd.to_datetime(
                            promocion.get("fecha_inicio"), errors="coerce"
                        )
                        fecha_fin_actual = pd.to_datetime(
                            promocion.get("fecha_fin"), errors="coerce"
                        )
                        valor_inicio = (
                            fecha_inicio_actual.date()
                            if pd.notna(fecha_inicio_actual)
                            else datetime.now().date()
                        )
                        valor_fin = (
                            fecha_fin_actual.date()
                            if pd.notna(fecha_fin_actual)
                            else valor_inicio
                        )
                        col_editar_inicio, col_editar_fin = st.columns(2)
                        nueva_fecha_inicio = col_editar_inicio.date_input(
                            "Inicio",
                            value=valor_inicio,
                            key=f"admin_promotion_start_{promocion_id}",
                        )
                        nueva_fecha_fin = col_editar_fin.date_input(
                            "Término",
                            value=valor_fin,
                            key=f"admin_promotion_end_{promocion_id}",
                        )
                        if st.button(
                            "Guardar nuevas fechas",
                            key=f"save_promotion_dates_{promocion_id}",
                        ):
                            if nueva_fecha_fin < nueva_fecha_inicio:
                                st.error("La fecha de término no puede ser anterior al inicio.")
                            else:
                                supabase.table("promociones").update({
                                    "fecha_inicio": nueva_fecha_inicio.isoformat(),
                                    "fecha_fin": nueva_fecha_fin.isoformat(),
                                }).eq("id", promocion_id).execute()
                                st.success("Fechas actualizadas correctamente.")
                                st.rerun()

                        promocion_esta_activa = bool(promocion.get("activa", False))
                        accion = "desactivar" if promocion_esta_activa else "reactivar"
                        confirmar_cambio = st.checkbox(
                            f"Confirmo que deseo {accion} esta promoción.",
                            key=f"confirm_promotion_status_{promocion_id}",
                        )
                        etiqueta_boton = (
                            "⛔ Desactivar ahora"
                            if promocion_esta_activa
                            else "✅ Reactivar promoción"
                        )
                        if st.button(
                            etiqueta_boton,
                            disabled=not confirmar_cambio,
                            key=f"change_promotion_status_{promocion_id}",
                        ):
                            supabase.table("promociones").update({
                                "activa": not promocion_esta_activa,
                            }).eq("id", promocion_id).execute()
                            st.success(
                                "Promoción desactivada inmediatamente."
                                if promocion_esta_activa
                                else "Promoción reactivada correctamente."
                            )
                            st.rerun()

                        mostrar_productos = st.checkbox(
                            "Ver productos y preparar reporte",
                            key=f"show_promotion_products_{promocion_id}",
                        )
                        if mostrar_productos:
                            try:
                                detalle_promocion = obtener_productos_de_promocion(
                                    promocion_id
                                )
                                if detalle_promocion.empty:
                                    st.info("Esta promoción no tiene productos registrados.")
                                else:
                                    texto_busqueda = st.text_input(
                                        "Buscar un SKU dentro de esta promoción",
                                        key=f"search_promotion_sku_{promocion_id}",
                                        placeholder="Ejemplo: BB5497",
                                    ).strip()
                                    detalle_visible = detalle_promocion.copy()
                                    if texto_busqueda:
                                        detalle_visible = detalle_visible[
                                            detalle_visible["sku_maestro"]
                                            .fillna("")
                                            .astype(str)
                                            .str.contains(
                                                texto_busqueda,
                                                case=False,
                                                regex=False,
                                            )
                                        ]

                                    st.caption(
                                        f"Se encontraron {len(detalle_visible):,} de "
                                        f"{len(detalle_promocion):,} productos. "
                                        "La vista muestra como máximo 300 filas."
                                    )
                                    st.dataframe(
                                        detalle_visible.head(300).rename(columns={
                                            "sku_maestro": "SKU maestro",
                                            "descuento_porcentaje": "Descuento (%)",
                                            "created_at": "Fecha de registro",
                                        }),
                                        use_container_width=True,
                                        hide_index=True,
                                    )

                                    nombre_reporte = re.sub(
                                        r"[^A-Za-z0-9_-]+",
                                        "_",
                                        str(promocion.get("nombre") or "promocion").strip(),
                                    ).strip("_") or "promocion"
                                    archivo_reporte = crear_reporte_promocion_excel(
                                        promocion,
                                        detalle_promocion,
                                        estado_promocion,
                                    )
                                    st.download_button(
                                        "⬇️ Descargar historial en Excel",
                                        data=archivo_reporte,
                                        file_name=(
                                            f"historial_promocion_{nombre_reporte}.xlsx"
                                        ),
                                        mime=(
                                            "application/vnd.openxmlformats-officedocument."
                                            "spreadsheetml.sheet"
                                        ),
                                        key=f"download_promotion_{promocion_id}",
                                    )
                            except Exception as ex:
                                st.error(
                                    "⚠️ No se pudo consultar el detalle de esta "
                                    f"promoción: {ex}"
                                )
        except Exception as ex:
            st.error(f"⚠️ No se pudo consultar o actualizar el historial de promociones: {ex}")

# ------------------------------------------
# 9. PESTAÑA: METAS POR ASESOR (SÓLO ADMIN)
# ------------------------------------------
if pagina_actual == "🎯 Metas por asesor":
    if st.session_state.es_admin:
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
# 10. PESTAÑA: GESTIÓN USUARIOS (SÓLO ADMIN)
# ------------------------------------------
if pagina_actual == "👥 Gestión Usuarios":
    if st.session_state.es_admin:
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
