-- DIFERIDA: NO ejecutar junto con 002 ni mientras main utilice V1.
-- Ejecutar manualmente sólo DESPUÉS de migrar main a V2 y validar en main:
-- recuperación de jornadas, arqueo, revisión/cierre, facturación y correo.
-- Confirmar además que no quedan instancias/sesiones de la aplicación V1 activas.
-- No se elimina la función ni se modifican usuarios, jornadas o auditoría.
begin;
revoke execute on function public.cajas_guardar_jornada(text,date,bigint,jsonb,text,uuid,boolean)
    from public, anon, authenticated, service_role;
notify pgrst, 'reload schema';
commit;
