# Vista Regional — prototipo local

Rama `codex/vista-regional`, creada desde `main` en `15034448a3541cca1cb2b37597155b2b689817d9`.
No requiere SQL, Supabase adicional, cambios de autenticación ni servicios de mapas en ejecución.

## Probar

Desde este worktree, con las dependencias de `requirements.txt` disponibles:

```powershell
python -m streamlit run vista_regional.py --server.address 127.0.0.1
```

La entrada independiente sirve únicamente para revisión local. Para probar la integración habitual, iniciar `app_nuevo.py`, entrar con el rol administrador existente y elegir **Operación → 🌎 Vista Regional**. El menú y la ruta comprueban `es_admin`; un puesto descriptivo de gerente no concede permisos nuevos.

Cargar `RpVtas_Extracto_Almacen.xlsx`. El reporte se procesa en memoria por sesión, sin guardarlo ni compartirlo mediante caché global. El Excel privado no forma parte del repositorio. No se probaron producción ni servicios externos de Sinapsis.

## Fuente y lectura

Validado con el Excel real de Descargas: hoja `RpVtas_Extracto_Almacen`, cabecera en fila 3, sucursales en filas 4–20 y TOTALES en fila 21. Las celdas inspeccionadas contienen resultados numéricos, no fórmulas. No hay fechas que permitan atribuir un periodo al extracto.

El lector busca encabezados por nombre, tolera acentos, espacios, signos y columnas vacías. Conserva códigos alfanuméricos completos; no aplica reglas de SKU. Admite números Excel y texto con separadores `1,234.56` o `1.234,56`; una coma única con tres dígitos finales se interpreta como millares (`1,234`). El formato de origen mexicano prevalece en ese caso ambiguo. No interpreta porcentajes como importes.

Rechaza códigos duplicados, cabeceras repetidas, columnas obligatorias ausentes, números obligatorios vacíos/no finitos y múltiples hojas de extracto o múltiples TOTALES. No combina archivos, periodos ni subtotales de forma implícita. Las fórmulas requieren resultados almacenados por Excel: openpyxl no recalcula. Una ausencia de TOTALES o diferencia queda visible. Códigos desconocidos permanecen en resultados, sin ubicación inventada; faltantes del catálogo se informan, sin imputar ventas cero.

## Indicadores y comparaciones

| Indicador | Base |
|---|---|
| Venta / Venta Neta | Columnas ERP independientes, conservadas |
| UPT | Unidades / # Doc |
| ATV | Venta / # Doc |
| ASP | Venta / Unidades |
| % Dcto | Descuento / Venta Bruta |
| Venta x Mt2 | Venta / Mt2 positivo |

No se reconstruye Venta Neta usando `+ImpAsumido`: se conserva su valor ERP. Las columnas financieras auxiliares se concilian también. Denominadores cero o negativos producen N/D. Mt2 vacío, cero o negativo se muestra N/D; no equivale a rendimiento cero.

La referencia de Venta, Venta Neta, Unidades y # Doc es el **promedio por sucursal presente**. Mt2 usa la media de áreas válidas. UPT, ATV, ASP y descuento regionales son cocientes de sumas (ponderación por documentos, documentos, unidades y venta bruta respectivamente), no medias simples de porcentajes. La referencia incluye la tienda comparada y todas las tiendas del archivo, aun cuando el mapa filtre un estado. La diferencia de descuento se muestra en puntos porcentuales; las otras diferencias, en su unidad original.

Venta por m² regional utiliza exclusivamente tiendas con área positiva **tanto en el numerador como en el denominador**. El ranking descendente no califica un descuento alto como mejor desempeño.

## Conciliación real

| Control | Suma de 17 tiendas | TOTALES |
|---|---:|---:|
| Venta | 13,674,839.30 | 13,674,839.30 |
| Venta Neta | 11,788,656.39 | 11,788,656.39 |
| Unidades | 10,124 | 10,124 |
| # Doc | 6,477 | 6,477 |
| Venta Bruta | 14,786,206.00 | 14,786,206.00 |
| Descuento | 1,111,366.70 | 1,111,366.70 |
| Mt2 | 3,874.30 | 3,874.30 |

Los once campos aditivos conciliados coinciden con tolerancia de 0.011; las diferencias residuales de punto flotante son inferiores a un centavo. TOTALES nunca entra al ranking ni a las sumas.

UPT regional: 1.5630693; ATV: 2,111.2921569; ASP: 1,350.7348183; descuento: 7.5162398%. Se contrastaron también los indicadores individuales con las columnas del Excel.

`Z1GPP` (BCS BELLA OAXACA) tiene 0 m² y Venta 444,979.90. La métrica regional válida es `(13,674,839.30 − 444,979.90) / 3,874.30 = 3,414.7741269`, con cobertura 16/17. El ERP muestra 3,529.6283974 en TOTALES porque incorpora la venta sin área. La diferencia es deliberada y no un fallo de conciliación monetaria.

## Catálogo y mapa

El catálogo de 17 almacenes se identifica por Código de Almacén y conserva nombres del archivo. Quedan **ciudad y estado pendientes**:

- Z1CFF — ORIGINALS AMERICAS
- Z1IQF — BCS ALTOZANO
- Z1GKM — ORIGINALS PUNTO SUR
- Z1GKP — TOWN SQUARE

Las otras ubicaciones se deducen de ciudades explícitas en el nombre comercial; requieren confirmación del catálogo de negocio y no representan direcciones verificadas. El mapa agrega por estado, nunca sitúa marcadores de tiendas. Las cuatro pendientes participan en el total regional y pueden consultarse mediante “Ubicación pendiente”. Los estados sin tiendas del archivo aparecen grises, no como evidencia de venta cero regional.

Límites públicos: **INEGI, Marco Geoestadístico, diciembre de 2025**, descargados mediante el [servicio oficial de información vectorial](https://www.inegi.org.mx/servicios/catalogounico.html), `https://gaia.inegi.org.mx/wscatgeo/v2/geo/mgee/{01..32}`. Se conserva atribución conforme a los [términos del INEGI](https://www.inegi.org.mx/inegi/terminos.html). Consulta manual realizada el 21–22 de septiembre de 2026. Los metadatos de origen indican coordenadas geográficas EPSG:6365; para esta escala visual se representan en la proyección Mercator del gráfico, sin uso de precisión geodésica.

`regional_data/mexico_estados.geojson` contiene las 32 entidades, simplificadas con tolerancia angular 0.01° y redondeo a cinco decimales, no aptas para medición ni delimitación legal. El script `actualizar_mapa.py` permite actualizar ese recurso públicamente y solo bajo ejecución manual; la aplicación no lo ejecuta ni transmite ventas al INEGI. No se requieren claves de mapas. El selector de estados sigue disponible si falta el recurso.

## Verificación

```powershell
$env:REGIONAL_EXCEL_REAL = 'C:\Users\lfgr\Downloads\RpVtas_Extracto_Almacen.xlsx'
python -m unittest discover -s tests -p 'test_vista_regional*.py' -v
```

Sin esa variable, las pruebas que necesitan el Excel privado se omiten explícitamente. Hay pruebas del lector, normalización, duplicados, TOTALES, áreas inválidas, ponderaciones, catálogo, evento de selección del mapa y navegación de widgets con archivo real. AppTest verifica la vista aislada, no el inicio de sesión ni la conexión de producción. No se modifican Caja, promociones, inventario, autenticación, secretos o plantillas.

La suite completa ejecutó 61 pruebas: 57 correctas y cuatro errores `BadZipFile` en `CorreoFlujoTests` (Caja). Sus simulaciones entregan `b'corte'` como si fuera un XLSX a la edición OOXML; tanto ese módulo como sus pruebas están idénticos a `main`. Se documentan sin modificar el área excluida. Las diez pruebas regionales pasaron.

Revisión visual local realizada con el archivo real mediante un arnés de carga fuera del repositorio: mapa completo de México con entidades grises/coloreadas, clic real en Yucatán reflejado en el selector y detalle de Altabrisa Mérida. Se corrigieron la conservación de geometrías anidadas y su orientación para Vega/D3. La revisión es del módulo regional aislado; no se abrió la sesión autenticada de producción. Las tablas anchas permiten desplazamiento horizontal en ventanas pequeñas.
