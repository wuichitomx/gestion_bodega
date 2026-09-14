# Puestos, permisos y responsables de Caja

Cambios preparados para `desarrollo` desde `8c4e776`. No se ejecutan migraciones,
no se modifican plantillas privadas y no cambia la autenticación de Gmail,
los destinatarios ni la lista de adjuntos del correo.

## Usuarios

`puesto` describe a la persona (Gerente, Subgerente, Cajero, Asesor).
`rol` conserva el perfil de acceso a los módulos anteriores, especialmente los
módulos administrativos que quedan fuera de esta modificación.
`permisos` es una lista explícita de autorizaciones para Caja y administración
de usuarios. Cambiar el puesto no concede permisos automáticamente.

| Usuario anterior, sin permisos explícitos | Arqueo | Revisión/cierre | Facturación | Correo | Usuarios |
|---|---|---|---|---|---|
| admin | Sí | Sí | Sí | Sí | Sí |
| cajero | Sí | No | No | No | No |
| asesor | No | No | No | No | No |

La migración 002 inicializa sólo usuarios con `permisos IS NULL` según esta matriz;
conserva asignaciones explícitas, incluidas las listas vacías. El código Python y
la función SQL aplican la misma matriz si V1 crea después un usuario sin permisos.
Los permisos adicionales se asignan expresamente por usuario. Una lista vacía revoca los cinco permisos,
incluso para un perfil admin. El acceso administrativo ya no se concede por
contener `admin` en el nombre de usuario. Los permisos se consultan de nuevo
en cada interacción; un puesto desconocido o un permiso inválido no eleva acceso.

Configuración sugerida: Cajero con `realizar_arqueo`; Subgerente con
`revisar_cierre`, `capturar_facturacion` y `preparar_correo`; Gerente con estos
y `administrar_usuarios`, añadiendo `realizar_arqueo` cuando también opere caja.
El nombre completo y el puesto se editan desde Gestión Usuarios.

## Continuación de una jornada

1. El cajero captura y guarda el arqueo. Se registra `responsable_arqueo`.
2. El gerente/subgerente elige la fecha y el usuario propietario de la caja.
   Consulta los movimientos, captura Corte X y prepara documentos. Sólo el
   propietario con permiso de arqueo puede modificar sus movimientos.
3. El usuario con permiso de revisión confirma el cierre definitivo. Se registra
   `responsable_revision`. La jornada queda bloqueada; continúa disponible la
   facturación posterior para quien tenga ese permiso.
4. En Correo de información, el revisor carga los adjuntos y pulsa
   **Confirmar revisión de documentación y firma**. La revisión queda vinculada
   al contenido de los documentos y al nombre de los adjuntos externos.
5. Otra persona puede entrar después, elegir esa misma fecha y caja, cargar los
   mismos adjuntos y preparar el borrador. Se registra `correo_preparado_por`,
   pero la firma utiliza el nombre y puesto guardados del revisor.

Las cargas siguen siendo temporales, como antes. No se añaden adjuntos a Drive
ni a Supabase; al volver a entrar puede ser necesario cargarlos de nuevo. Si
cambia un documento (incluido un libro mensual modificado en Drive), se requiere
una nueva revisión. El identificador del borrador se guarda por jornada para
reutilizarlo al continuar desde otra sesión.

Las tres identidades guardan usuario, nombre, puesto y momento de registro.
La base genera las identidades desde `usuarios`, sin aceptar un firmante inventado
por el cliente. Los datos históricos conservan `cierre_datos.responsable`; no se
supone que ese cajero fue también el revisor. Un histórico sin revisión requiere
confirmarla explícitamente antes de preparar correo.

## Migración y puesta en uso

La instalación está pendiente: **no se ha ejecutado SQL**.
Revisar y aplicar únicamente `migrations/002_puestos_permisos_responsables.sql`
para habilitar V2 sin interrumpir la aplicación V1 en `main`. Requiere que las
tablas de Caja ya existan. No volver a ejecutar `001_cajas_persistencia.sql`.

La migración agrega columnas anulables (`usuarios.puesto`, `usuarios.permisos`,
`cajas_auditoria.actor`) y una función nueva `cajas_guardar_jornada_v2`.
Conserva las claves `(usuario, fecha)` y las jornadas existentes. Comprueba
permisos por acción y versión, preserva el bloqueo de jornadas cerradas y limita
facturación/revisión/correo a sus campos. `cajas_cierres` conserva la instantánea
del cierre; las actualizaciones posteriores quedan en jornada y auditoría.

La 002 conserva la implementación de V1 y su permiso de ejecución para
`service_role`; V1 y V2 siguen disponibles en el backend compartido. El código
nuevo llama exclusivamente a V2, que verifica permisos por acción; nunca recurre
a V1 si falla V2. La interfaz bloquea Caja si aún faltan las columnas nuevas.

La 003 (`003_retirar_cajas_guardar_jornada_v1.sql`) queda DIFERIDA. Sólo ejecutarla
después de migrar y validar `main` con V2 y retirar instancias/sesiones antiguas.
Retira la ejecución de V1 sin borrar la función ni datos. No ejecutarla junto con
002. Durante la convivencia, V1 mantiene su autorización antigua basada en rol:
los permisos nuevos limitan V2, pero no restringen retrospectivamente a V1.
V1 tampoco registra las nuevas responsabilidades y puede omitir esos campos al
guardar una jornada abierta; para conservarlos, continuar en V2 las jornadas que
ya tengan intervención de V2. La versión evita sobrescrituras desde una sesión
desactualizada, pero no convierte al cliente V1 en compatible con esos metadatos.

No se realizaron cambios en Supabase Auth ni en el esquema de contraseñas
existente: el servidor Streamlit sigue validando la sesión y usa la credencial
privada de servidor para Caja, sin exponerla al navegador.

## Validación y límites

Pruebas locales con `python -m unittest discover -s tests -v`, incluida la
interfaz Streamlit, compatibilidad de usuarios, denegación de acciones, firma,
serialización, separación actor/propietario y conflictos de versión.
No se ha validado la migración contra una instancia PostgreSQL ni se han hecho
llamadas reales a Supabase, Drive o Gmail.

El guardado en Supabase y las operaciones de Drive/Gmail no son una única
transacción. Se comprueba la versión antes de operar y se informa cuando el
servicio externo responde pero no se confirma el registro. Ante respuesta
incierta de Gmail, revisar el borrador existente antes de reintentar.
