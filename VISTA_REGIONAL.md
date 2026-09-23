# Vista Regional — dashboard de tiendas

Prototipo original `bec0ad1`, creado desde `main` en `15034448a3541cca1cb2b37597155b2b689817d9` e integrado en `desarrollo`. La continuación del 22/09/2026 se realizó únicamente en `desarrollo`.
Antes de editar se comprobaron estado limpio, historial y referencias remotas: `desarrollo=bec0ad1`, `main=1503444`. Ya existían lector, catálogo, mapa, ranking, detalle y referencias ponderadas; no había dashboard comparativo ni cambios sin guardar de esa iteración.
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

El catálogo de 17 almacenes está separado en `regional_data/sucursales.json`, se identifica por Código de Almacén y conserva nombres del archivo. Al iniciar esta continuación faltaban ciudad y estado en cuatro tiendas. Resultado:

- Z1CFF — ORIGINALS AMERICAS: pendiente; “Américas” no identifica una plaza inequívoca.
- Z1IQF — BCS ALTOZANO: pendiente; confirmar si corresponde a Paseo Altozano, Morelia. El nombre abreviado no permite certificar esa relación interna.
- Z1GKM — ORIGINALS PUNTO SUR: Tlajomulco de Zúñiga, Jalisco, clave estatal 14. Ubicación de la plaza respaldada por su [directorio oficial](https://puntosurgdl.com/directorio/) y por el [localizador adidas de Punto Sur](https://www.adidas.mx/stores/mexico/tlajomulco/av-punto-sur-312-c-c-punto-sur-ln19/9990176120).
- Z1GKP — TOWN SQUARE: Metepec, Estado de México, clave estatal 15; [directorio oficial de Adidas en Town Square Metepec](https://townsquaremetepec.com/products/adidas).

Fuentes consultadas el 22/09/2026. La asociación de las dos plazas al código se basa en el nombre del reporte; los directorios públicos no publican códigos ERP. No se asignan direcciones ni coordenadas de tienda. Para resolver las dos pendientes, confirmar ciudad/estado por código y actualizar únicamente el registro correspondiente del JSON con su `cve_ent`. La app lo relee al ejecutar la vista.

Las trece ubicaciones originales se deducen de ciudades explícitas en el nombre comercial; requieren confirmación del catálogo de negocio y no representan direcciones verificadas. El mapa agrega por estado, nunca sitúa marcadores de tiendas. Las dos pendientes participan en el total regional y pueden consultarse mediante “Ubicación pendiente”. Los estados sin tiendas del archivo aparecen grises, no como evidencia de venta cero regional.

## Dashboard comparativo

Debajo del mapa y del selector estatal aparecen cuatro tarjetas con referencias regionales y gráficas de barras horizontales: UPT (Items x Doc.), ASP (Precio x Unidad), ATV (Venta x Documento) y rendimiento por m² (Venta x Mt2). En pantallas amplias se distribuyen en dos columnas; las barras se ordenan por valor y muestran código, tienda y cifra. Una línea amarilla punteada marca la referencia completa. Un panel desplegable permite comparar Venta, Venta Neta y % Dcto. El ranking y el detalle originales permanecen debajo.

Las fórmulas conservan la sección Indicadores: UPT = Σ Unidades / Σ Docs; ASP = Σ Venta / Σ Unidades; ATV = Σ Venta / Σ Docs; rendimiento = Σ Venta de tiendas con Mt2 > 0 / Σ Mt2 de esas mismas tiendas. Los promedios de Venta y Venta Neta son sumas / sucursales presentes; % Dcto = Σ Descuento / Σ Venta Bruta. El filtro estatal solo afecta las barras; tarjetas y líneas mantienen toda la región. N/D conserva la fila y una etiqueta, sin dibujar una barra cero. Si toda la región carece de denominador válido, se muestra referencia N/D y se omite su línea.

Prueba manual: cargar el archivo en Vista Regional; comprobar 17 tiendas, UPT 1.56, ASP 1,350.73, ATV 2,111.29 y rendimiento 3,414.77. Filtrar Yucatán: dos tiendas y las mismas referencias. Filtrar Oaxaca: Bella Oaxaca sin barra de rendimiento, etiqueta N/D y referencia regional 3,414.77. “Ubicación pendiente” debe listar Z1CFF y Z1IQF. Verificar también el selector de indicadores secundarios y conservar el ranking/detalle.

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

### Validación de la continuación (22/09/2026)

Las 11 pruebas regionales pasaron con el Excel real disponible en esta sesión, incluidas navegación de widgets, cocientes de sumas, exclusión de TOTALES, N/D y referencia completa en gráficas filtradas. La compilación de los tres archivos Python modificados y `git diff --check` pasaron. Los hashes de las dos plantillas privadas permanecieron idénticos. Se revisó el dashboard aislado en navegador local; esto no verifica el despliegue ni la sesión autenticada de Streamlit Cloud. El intento de guardar el commit local fue bloqueado por permiso denegado al crear .git/worktrees/Sinapsis/index.lock. Los cinco archivos modificados permanecen en desarrollo sin stage ni commit; no hubo push ni merge a main. Se requiere guardar el commit desde PowerShell habitual o GitHub Desktop con acceso al repositorio.

### Corrección del dashboard sobre 0d69cb5

Diagnóstico reproducido: el commit `0d69cb5` introduce cuatro espacios antes del docstring de la primera línea de `vista_regional.py`. Python rechaza el módulo con `IndentationError: unexpected indent`. Las funciones de gráficas sí existen y `mostrar_dashboard(visibles, df)` sí se invoca después del selector y antes del ranking, salvo cuando el filtro no contiene tiendas. Una app que muestra el ranking sin errores no acredita que haya cargado este módulo desde cero; no se verificó la versión desplegada.

Se corrige la sangría y se titula el bloque **Dashboard regional de KPIs**. Orden: UPT, ATV, ASP, Rendimiento por m². Altair ya es dependencia del proyecto. Hay dos columnas y una opción para ampliar a una gráfica por fila, etiquetas cian y benchmark amarillo. Las fórmulas y el alcance regional completo se indican junto a cada indicador. Las tiendas sin área positiva se excluyen íntegramente del gráfico de rendimiento; siguen como N/D en las tablas. Un filtro sin áreas válidas muestra un aviso y conserva el valor regional.

El catálogo actual ya contiene ORIGINALS AMERICAS y BCS ALTOZANO en Morelia, Michoacán; Punto Sur en Jalisco y Town Square en Estado de México. Las notas de ubicaciones pendientes anteriores son históricas. No se modifica el catálogo: se actualizan las pruebas que aún esperaban esas ubicaciones pendientes.

Validación: 11 pruebas regionales correctas con `RpVtas_Extracto_Almacen.xlsx`; 17 tiendas, TOTALES excluido, 16 barras de rendimiento, orden descendente y referencias completas. AppTest comprueba mapa, cuatro gráficas principales y secundaria, navegación por Yucatán/Michoacán/Oaxaca, opción de una gráfica por fila y conservación del ranking/detalle. Se verifica el módulo aislado, sin iniciar autenticación ni servicios externos.

Para probar en desarrollo: cargar el Excel y buscar el bloque entre el selector estatal y el ranking. Verificar referencias UPT 1.56, ATV 2,111.29, ASP 1,350.73 y rendimiento 3,414.77. En Yucatán deben aparecer dos barras por indicador sin cambiar las referencias; Oaxaca debe mostrar N/D en rendimiento y ninguna barra de ese indicador. Ampliar a una por fila si los nombres quedan pequeños.

Cierre de esta corrección: revisión local en navegador confirma barras, valores y referencias visibles con tema oscuro; la ventana estrecha apila las gráficas. Compilación y `git diff --check` correctos. Catálogo y ambas plantillas privadas conservan sus SHA-256 originales. El commit separado fue bloqueado al crear `.git/worktrees/Sinapsis/index.lock` (Permission denied): no se forzó ni se borró ningún lock. Los cuatro archivos permanecen modificados sin commit en `desarrollo`. No hubo push ni merge a `main`. La consulta remota también falló con `SEC_E_NO_CREDENTIALS`; no se acredita publicación ni versión desplegada.
