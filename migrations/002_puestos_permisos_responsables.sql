-- Instalación manual. No ejecutada por Codex. Requiere las tablas de Cajas existentes.
-- No modifica ni borra jornadas ni plantillas. Permite coexistir a V1 y V2.
begin;
alter table public.usuarios add column if not exists puesto text;
alter table public.usuarios add column if not exists permisos jsonb;
alter table public.cajas_auditoria add column if not exists actor text;

-- Inicializa sólo usuarios sin asignación explícita. Reejecutar no sobreescribe
-- permisos personalizados ni listas vacías; conserva rol, puesto y demás datos.
update public.usuarios
set permisos = case lower(btrim(coalesce(rol, '')))
    when 'admin' then '["realizar_arqueo","revisar_cierre","capturar_facturacion","preparar_correo","administrar_usuarios"]'::jsonb
    when 'cajero' then '["realizar_arqueo"]'::jsonb
    else '[]'::jsonb
end
where permisos is null;

create or replace function public.usuario_tiene_permiso(p_actor text, p_permiso text)
returns boolean language sql stable security invoker set search_path = '' as $$
    select coalesce((select case
        when u.permisos is not null then jsonb_typeof(u.permisos) = 'array' and u.permisos ? p_permiso
        when lower(btrim(coalesce(u.rol,''))) = 'admin' then true
        when lower(btrim(coalesce(u.rol,''))) = 'cajero' then p_permiso = 'realizar_arqueo'
        else false end from public.usuarios u where u.username = p_actor), false)
$$;
revoke all on function public.usuario_tiene_permiso(text,text) from public, anon, authenticated;
grant execute on function public.usuario_tiene_permiso(text,text) to service_role;

create or replace function public.cajas_guardar_jornada_v2(
    p_actor text, p_usuario text, p_fecha date, p_version bigint, p_datos jsonb,
    p_accion text, p_operacion uuid, p_cerrar boolean default false
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
    actual public.cajas_jornadas%rowtype;
    anterior jsonb;
    perfil public.usuarios%rowtype;
    permiso text;
    identidad jsonb;
    nuevos jsonb;
    campos_seguimiento text[] := array['responsable_revision', 'correo_preparado_por',
                                       'revision_documentacion', 'borrador_correo'];
begin
    if p_usuario is null or btrim(p_usuario) = '' or p_fecha is null or p_version is null
       or p_version < 0 or p_operacion is null or p_datos is null then
        raise exception 'CAJA_DATOS_INVALIDOS';
    end if;
    select * into perfil from public.usuarios where username = p_actor;
    permiso := case
        when p_accion in ('importar_sesion','agregar_movimiento','eliminar_movimiento',
                          'analizar_z','guardar_arqueo') then 'realizar_arqueo'
        when p_accion in ('analizar_x','preparar_documentos','cerrar','revisar_documentacion') then 'revisar_cierre'
        when p_accion = 'facturacion' then 'capturar_facturacion'
        when p_accion = 'preparar_correo' then 'preparar_correo'
        else null end;
    if permiso is null then raise exception 'CAJA_ACCION_INVALIDA'; end if;
    if perfil.username is null or not public.usuario_tiene_permiso(p_actor, permiso) then
        raise exception 'CAJA_USUARIO_NO_AUTORIZADO';
    end if;
    if permiso = 'realizar_arqueo' and p_actor <> p_usuario then
        raise exception 'CAJA_USUARIO_NO_AUTORIZADO';
    end if;
    identidad := jsonb_build_object('usuario', p_actor,
        'nombre', coalesce(nullif(btrim(perfil.nombre_completo), ''), p_actor),
        'puesto', coalesce(nullif(btrim(perfil.puesto), ''), case lower(btrim(perfil.rol))
            when 'admin' then 'Administrador' when 'cajero' then 'Cajero'
            when 'asesor' then 'Asesor' else '' end), 'registrado_en', now());
    if p_datos->>'fecha' is distinct from p_fecha::text
       or jsonb_typeof(p_datos->'vouchers') is distinct from 'array'
       or jsonb_typeof(p_datos->'cortes') is distinct from 'array' then
        raise exception 'CAJA_DATOS_INVALIDOS';
    end if;
    -- Una transacción serializa todos los escritores de la misma jornada.
    perform pg_advisory_xact_lock(hashtextextended(p_usuario || '/' || p_fecha::text, 0));
    select * into actual from public.cajas_jornadas
        where usuario = p_usuario and fecha = p_fecha for update;
    if exists (select 1 from public.cajas_auditoria where operacion = p_operacion
               and usuario = p_usuario and fecha = p_fecha) then
        return jsonb_build_object('datos', actual.datos, 'version', actual.version, 'estado', actual.estado);
    end if;
    if actual.usuario is null then
        if p_version <> 0 then raise exception 'CAJA_CONFLICTO'; end if;
        if permiso <> 'realizar_arqueo' or p_actor <> p_usuario then
            raise exception 'CAJA_JORNADA_INEXISTENTE';
        end if;
        insert into public.cajas_jornadas(usuario, fecha, datos)
            values(p_usuario, p_fecha, '{}'::jsonb) returning * into actual;
    end if;
    if actual.estado = 'cerrada' and p_accion not in ('facturacion','revisar_documentacion','preparar_correo') then raise exception 'CAJA_CERRADA'; end if;
    if actual.version <> p_version then raise exception 'CAJA_CONFLICTO'; end if;
    if p_cerrar is distinct from (p_accion = 'cerrar') then raise exception 'CAJA_ACCION_INVALIDA'; end if;
    if p_cerrar and (jsonb_typeof(p_datos->'cierre_datos') is distinct from 'object'
                   or jsonb_typeof(p_datos->'corte_x') is distinct from 'object'
                   or jsonb_array_length(p_datos->'cortes') = 0) then
        raise exception 'CAJA_CIERRE_INCOMPLETO';
    end if;
    if p_cerrar then
        if (p_datos - campos_seguimiento) is distinct from (actual.datos - campos_seguimiento)
           or p_datos->'cortes'->-1->>'cuadrado' is distinct from 'true'
           or p_datos->'cortes'->-1->>'huella' is distinct from p_datos->'cierre_datos'->>'huella'
           or p_datos->'cierre_datos'->>'fecha_confirmada' is distinct from 'true'
           or coalesce((p_datos->'cierre_datos'->>'piezas')::integer, 0) <= 0
           or abs(coalesce((select sum((v->>'importe')::numeric)
                            from jsonb_array_elements(p_datos->'vouchers') v), 0)
                  - (p_datos->'corte_x'->>'venta')::numeric) >= 0.01 then
            raise exception 'CAJA_CIERRE_INCOMPLETO';
        end if;
    end if;
    -- Las acciones posteriores sólo pueden cambiar su parte del expediente.
    if p_accion in ('revisar_documentacion', 'preparar_correo') then
        if actual.estado <> 'cerrada' or
           (p_datos - campos_seguimiento) is distinct from (actual.datos - campos_seguimiento) then
            raise exception 'CAJA_DATOS_INVALIDOS';
        end if;
    elsif p_accion = 'facturacion' then
        if (p_datos #- '{cierre_datos,facturacion}' - campos_seguimiento)
           is distinct from (actual.datos #- '{cierre_datos,facturacion}' - campos_seguimiento) then
            raise exception 'CAJA_DATOS_INVALIDOS';
        end if;
    elsif p_accion in ('analizar_x', 'preparar_documentos') then
        if (p_datos - array['corte_x','texto_x','cierre_datos'] - campos_seguimiento)
           is distinct from (actual.datos - array['corte_x','texto_x','cierre_datos'] - campos_seguimiento) then
            raise exception 'CAJA_DATOS_INVALIDOS';
        end if;
        if p_accion = 'preparar_documentos' and
           coalesce(p_datos #> '{cierre_datos,facturacion}', '{}'::jsonb) is distinct from
           coalesce(actual.datos #> '{cierre_datos,facturacion}', '{}'::jsonb) then
            raise exception 'CAJA_DATOS_INVALIDOS';
        end if;
    elsif permiso = 'realizar_arqueo' and p_accion <> 'importar_sesion' then
        if actual.version = 0 and
           (p_datos - array['fecha','vouchers','cortes','ultimo_corte','texto_z','responsable_arqueo'] - campos_seguimiento) <> '{}'::jsonb then
            raise exception 'CAJA_DATOS_INVALIDOS';
        end if;
        if (p_datos - array['vouchers','cortes','ultimo_corte','texto_z','cierre_datos','responsable_arqueo'] - campos_seguimiento)
           is distinct from (actual.datos - array['vouchers','cortes','ultimo_corte','texto_z','cierre_datos','responsable_arqueo'] - campos_seguimiento)
           and actual.version > 0 then
            raise exception 'CAJA_DATOS_INVALIDOS';
        end if;
        -- Capturar movimientos puede invalidar documentos, pero no fabricarlos.
        if p_datos ? 'cierre_datos' and p_datos->'cierre_datos' is distinct from actual.datos->'cierre_datos' then
            raise exception 'CAJA_DATOS_INVALIDOS';
        end if;
    elsif p_accion = 'importar_sesion' then
        if actual.version <> 0 then raise exception 'CAJA_CONFLICTO'; end if;
        if (p_datos ? 'corte_x' or p_datos ? 'cierre_datos') and
           not public.usuario_tiene_permiso(p_actor, 'revisar_cierre') then
            raise exception 'CAJA_USUARIO_NO_AUTORIZADO';
        end if;
        if coalesce(p_datos #> '{cierre_datos,facturacion}', '{}'::jsonb) <> '{}'::jsonb and
           not public.usuario_tiene_permiso(p_actor, 'capturar_facturacion') then
            raise exception 'CAJA_USUARIO_NO_AUTORIZADO';
        end if;
    end if;
    nuevos := p_datos - array['responsable_arqueo'] - campos_seguimiento;
    -- Las identidades previas son inmutables salvo la acción específica autorizada.
    nuevos := nuevos || (actual.datos - (select coalesce(array_agg(k), array[]::text[])
        from jsonb_object_keys(actual.datos) k where k not in
        ('responsable_arqueo','responsable_revision','correo_preparado_por','revision_documentacion','borrador_correo')));
    if p_accion in ('guardar_arqueo','importar_sesion') and not (nuevos ? 'responsable_arqueo') then
        nuevos := nuevos || jsonb_build_object('responsable_arqueo', identidad);
    end if;
    if p_accion in ('cerrar','revisar_documentacion') then
        if coalesce(identidad->>'puesto', '') = '' then raise exception 'CAJA_PUESTO_REQUERIDO'; end if;
        nuevos := nuevos || jsonb_build_object('responsable_revision', identidad);
    end if;
    if p_accion = 'revisar_documentacion' then
        if coalesce(p_datos #>> '{revision_documentacion,huella}', '') !~ '^[0-9a-f]{64}$' then
            raise exception 'CAJA_REVISION_REQUERIDA';
        end if;
        nuevos := nuevos || jsonb_build_object('revision_documentacion', p_datos->'revision_documentacion');
    elsif p_accion = 'preparar_correo' then
        if not (actual.datos ? 'responsable_revision') or not (actual.datos ? 'revision_documentacion')
           or p_datos->'revision_documentacion' is distinct from actual.datos->'revision_documentacion'
           or p_datos->'responsable_revision' is distinct from actual.datos->'responsable_revision' then
            raise exception 'CAJA_REVISION_REQUERIDA';
        end if;
        nuevos := nuevos || jsonb_build_object('correo_preparado_por', identidad);
        if p_datos ? 'borrador_correo' then
            nuevos := nuevos || jsonb_build_object('borrador_correo', p_datos->'borrador_correo');
        end if;
    else
        nuevos := nuevos - 'revision_documentacion';
    end if;
    p_datos := nuevos;
    anterior := actual.datos;
    update public.cajas_jornadas set datos = p_datos, version = version + 1,
        estado = case when p_cerrar then 'cerrada' else actual.estado end, actualizado_en = now()
        where usuario = p_usuario and fecha = p_fecha returning * into actual;
    insert into public.cajas_auditoria(operacion, usuario, fecha, accion, version, anterior, posterior, actor)
        values(p_operacion, p_usuario, p_fecha, p_accion, actual.version, anterior, p_datos, p_actor);
    if p_cerrar then
        insert into public.cajas_cierres(usuario, fecha, datos) values(p_usuario, p_fecha, p_datos);
    end if;
    return jsonb_build_object('datos', actual.datos, 'version', actual.version, 'estado', actual.estado);
end;
$$;

revoke all on function public.cajas_guardar_jornada_v2(text,text,date,bigint,jsonb,text,uuid,boolean) from public, anon, authenticated;
grant execute on function public.cajas_guardar_jornada_v2(text,text,date,bigint,jsonb,text,uuid,boolean) to service_role;
-- main todavía utiliza V1 en este mismo backend. No cambiar su implementación
-- ni retirar service_role durante la convivencia. Su retiro corresponde a 003.
grant execute on function public.cajas_guardar_jornada(text,date,bigint,jsonb,text,uuid,boolean) to service_role;
notify pgrst, 'reload schema';
commit;
