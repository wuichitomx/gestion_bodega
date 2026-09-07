-- Ejecutar una vez en el SQL Editor de Supabase. No modifica tablas existentes.
begin;
create table if not exists public.cajas_jornadas (
    usuario text not null,
    fecha date not null,
    version bigint not null default 0,
    estado text not null default 'abierta' check (estado in ('abierta', 'cerrada')),
    datos jsonb not null,
    actualizado_en timestamptz not null default now(),
    primary key (usuario, fecha)
);
create table if not exists public.cajas_auditoria (
    operacion uuid primary key,
    usuario text not null,
    fecha date not null,
    accion text not null,
    version bigint not null,
    anterior jsonb,
    posterior jsonb not null,
    registrado_en timestamptz not null default now(),
    foreign key (usuario, fecha) references public.cajas_jornadas(usuario, fecha)
);
create table if not exists public.cajas_cierres (
    usuario text not null,
    fecha date not null,
    datos jsonb not null,
    cerrado_en timestamptz not null default now(),
    primary key (usuario, fecha),
    foreign key (usuario, fecha) references public.cajas_jornadas(usuario, fecha)
);
alter table public.cajas_jornadas enable row level security;
alter table public.cajas_auditoria enable row level security;
alter table public.cajas_cierres enable row level security;
revoke all on public.cajas_jornadas, public.cajas_auditoria, public.cajas_cierres from public, anon, authenticated;
grant select, insert, update on public.cajas_jornadas to service_role;
grant select, insert on public.cajas_auditoria, public.cajas_cierres to service_role;

create or replace function public.cajas_guardar_jornada(
    p_usuario text, p_fecha date, p_version bigint, p_datos jsonb,
    p_accion text, p_operacion uuid, p_cerrar boolean default false
) returns jsonb language plpgsql security invoker set search_path = '' as $$
declare
    actual public.cajas_jornadas%rowtype;
    anterior jsonb;
begin
    if p_usuario is null or btrim(p_usuario) = '' or p_fecha is null or p_version is null
       or p_version < 0 or p_operacion is null or p_datos is null then
        raise exception 'CAJA_DATOS_INVALIDOS';
    end if;
    if not exists (select 1 from public.usuarios u where u.username = p_usuario
                   and lower(coalesce(u.rol, '')) in ('cajero', 'admin')) then
        raise exception 'CAJA_USUARIO_NO_AUTORIZADO';
    end if;
    if p_accion not in ('importar_sesion', 'agregar_movimiento', 'eliminar_movimiento',
                       'analizar_z', 'guardar_arqueo', 'analizar_x', 'preparar_documentos', 'cerrar')
       or p_accion is null then raise exception 'CAJA_ACCION_INVALIDA'; end if;
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
        insert into public.cajas_jornadas(usuario, fecha, datos)
            values(p_usuario, p_fecha, '{}'::jsonb) returning * into actual;
    end if;
    if actual.estado = 'cerrada' then raise exception 'CAJA_CERRADA'; end if;
    if actual.version <> p_version then raise exception 'CAJA_CONFLICTO'; end if;
    if p_cerrar is distinct from (p_accion = 'cerrar') then raise exception 'CAJA_ACCION_INVALIDA'; end if;
    if p_cerrar and (jsonb_typeof(p_datos->'cierre_datos') is distinct from 'object'
                   or jsonb_typeof(p_datos->'corte_x') is distinct from 'object'
                   or jsonb_array_length(p_datos->'cortes') = 0) then
        raise exception 'CAJA_CIERRE_INCOMPLETO';
    end if;
    if p_cerrar then
        if p_datos is distinct from actual.datos
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
    anterior := actual.datos;
    update public.cajas_jornadas set datos = p_datos, version = version + 1,
        estado = case when p_cerrar then 'cerrada' else 'abierta' end, actualizado_en = now()
        where usuario = p_usuario and fecha = p_fecha returning * into actual;
    insert into public.cajas_auditoria(operacion, usuario, fecha, accion, version, anterior, posterior)
        values(p_operacion, p_usuario, p_fecha, p_accion, actual.version, anterior, p_datos);
    if p_cerrar then
        insert into public.cajas_cierres(usuario, fecha, datos) values(p_usuario, p_fecha, p_datos);
    end if;
    return jsonb_build_object('datos', actual.datos, 'version', actual.version, 'estado', actual.estado);
end;
$$;
revoke all on function public.cajas_guardar_jornada(text,date,bigint,jsonb,text,uuid,boolean) from public, anon, authenticated;
grant execute on function public.cajas_guardar_jornada(text,date,bigint,jsonb,text,uuid,boolean) to service_role;
notify pgrst, 'reload schema';
commit;
