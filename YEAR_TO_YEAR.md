# Year to Year regional (MVP)

Dentro de 🌎 Vista Regional, abrir 📈 Year to Year. Cargar el reporte actual y el
del año anterior con periodos equivalentes. Los nombres no determinan el año ni
participan en los cálculos. No se infieren fechas que el extracto no contiene.
Los archivos se conservan en la sesión; hay que volver a cargarlos en una sesión
nueva. No se guardan en Supabase ni sustituyen el reporte vigente de Actual.

## Lecturas y reglas

- Región total: totales completos de cada año; tabla con todas las tiendas actuales.
  Las tiendas solo del año anterior se documentan como Sin dato actual en un bloque secundario.
- Tiendas comparables: intersección exacta de Código de Almacén con al menos
  30 documentos anteriores, aplicada tanto a la tabla como a ambos agregados. No se unen
  tiendas por nombre; compartir código no demuestra un periodo completo operado.
- Reutiliza `leer_reporte`; TOTALES se usa exclusivamente para conciliación.
- Venta, Venta Neta, Unidades y # Doc se suman. UPT = Σ Unidades / Σ # Doc;
  ATV = Σ Venta / Σ # Doc; ASP = Σ Venta / Σ Unidades.
- % Dcto = Σ Descuento / Σ Venta Bruta, igual que Actual. La diferencia se
  presenta en puntos porcentuales. Venta Neta se toma directamente del ERP.
- YoY = (actual − anterior) / anterior. No se promedian ratios individuales.
- Base insuficiente: menos de 30 documentos anteriores o KPI anterior ≤ 0/no
  disponible. Se conserva el importe y diferencia, sin porcentaje ni barra.
  Un KPI actual sin denominador válido se marca no disponible.
- El umbral es una regla prudente del MVP, no una prueba de significancia.
  Región total conserva las bases insuficientes. Tiendas comparables excluye de
  ambos años los códigos con menos de 30 documentos anteriores. Cada KPI requiere
  además una base positiva y datos disponibles para calcular su YoY.
- Nueva / sin base comparable y Sin dato actual conservan valores ausentes
  como N/D; no se sustituyen por cero ni se calcula un crecimiento ficticio.
- La tabla permite ordenar por encabezado; el selector cambia el ranking y
  el estado de comparabilidad del KPI seleccionado. Las barras excluidas se
  enumeran junto al gráfico. El caption distingue las filas de la lectura y las
  tiendas con YoY calculable del total de actuales; la gráfica solo dibuja cambios válidos.

## Validación con ejemplos de agosto

Los dos archivos reales se leen con el lector regional. Actual: 17 tiendas;
anterior: 15; códigos comunes: 14; unión: 18. Bella Oaxaca (Z1GPP) tiene 2
documentos, 4 unidades y venta de $2,079.60 en el anterior: Base insuficiente.
Región total muestra 17 filas actuales; Tiendas comparables muestra 13 filas y
excluye Bella Oaxaca de ambos agregados. Venta Neta tiene 13 barras calculables
en ambas lecturas; el caption lo explica sin confundirlo con las filas de la tabla.

Los nuevos son Z1GKP, Z1H4Q y Z1IQF. Z1GEY no aparece en el actual.
No se emparejan automáticamente ORIGINALS ALTOZANO y BCS ALTOZANO.

| KPI | Región actual | Región anterior | YoY | Comparables actual | Comparables anterior | YoY comparables |
|---|---:|---:|---:|---:|---:|---:|
| Venta | 25,874,201.17 | 26,976,901.18 | -4.09% | 21,773,545.67 | 25,975,838.03 | -16.18% |
| Venta Neta | 22,305,352.16 | 23,255,964.87 | -4.09% | 18,770,303.16 | 22,392,978.78 | -16.18% |
| Unidades | 20,125 | 21,408 | -5.99% | 16,626 | 20,599 | -19.29% |
| # Doc | 12,667 | 13,159 | -3.74% | 10,589 | 12,741 | -16.89% |
| UPT | 1.59 | 1.63 | -2.34% | 1.57 | 1.62 | -2.88% |
| ATV | 2,042.65 | 2,050.07 | -0.36% | 2,056.24 | 2,038.76 | +0.86% |
| ASP | 1,285.67 | 1,260.13 | +2.03% | 1,309.61 | 1,261.02 | +3.85% |
| % Dcto | 12.92% | 3.71% | +9.21 pp | 12.54% | 3.58% | +8.96 pp |

## Pruebas locales

Usar un entorno con las dependencias de `requirements.txt` (Streamlit 1.62.0).
Asignar YOY_ACTUAL y YOY_ANTERIOR a los dos archivos privados y ejecutar:

```powershell
python -m unittest discover -s tests -p 'test_regional_yoy.py' -v
python -m py_compile vista_regional.py regional_yoy.py tests/test_regional_yoy.py
```

La prueba independiente suma las columnas originales con openpyxl, sin utilizar
las funciones del módulo para calcular los valores esperados, y compara ocho
KPIs en ambas lecturas. No copiar los Excel privados al repositorio.

Para las pruebas existentes, REGIONAL_EXCEL_REAL debe apuntar al original
RpVtas_Extracto_Almacen.xlsx: tienen valores esperados de ese archivo específico,
no de los ejemplos de agosto.

```powershell
python -m unittest discover -s tests -p 'test_vista_regional*.py' -v
streamlit run app_nuevo.py
```

En Sinapsis de desarrollo: Vista Regional → Year to Year → cargar ambos Excel →
alternar las dos lecturas → seleccionar Venta, UPT y % Dcto → comprobar Bella
Oaxaca y tiendas sin contraparte. En Actual, verificar mapa, ranking y detalle.
La validación local no acredita el despliegue ni una sesión autenticada de Cloud.

## Resultado de validación del MVP

28 pruebas locales aprobadas (7 de YoY y 21 regionales existentes), sintaxis
Python válida y `git diff --check` sin errores. Revisión visual local de tarjetas
y ranking con los Excel reales. Pruebas de carga, retiro y reemplazo inválido
confirman que no quedan resultados obsoletos. Persistencia probada con mocks,
sin llamadas reales a Supabase. Plantillas privadas con hashes sin cambios.
