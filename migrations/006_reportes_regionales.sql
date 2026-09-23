-- Instalación manual; Codex no ejecuta esta migración en producción.
-- Autenticación propia de Sinapsis: sólo el servidor confiable puede invocar RPC.
begin;

create table public.reportes_regionales (
    id uuid primary key,
    fecha_reporte date not null,
    created_at timestamptz not null default now(),
    actor text not null,
    vigente boolean not null default false,
    totales jsonb not null default '[]'::jsonb
        check (jsonb_typeof(totales) = 'array' and jsonb_array_length(totales) <= 1)
);
create unique index reportes_regionales_unico_vigente
    on public.reportes_regionales (vigente) where vigente;

create table public.reporte_regional_detalle (
    reporte_id uuid not null references public.reportes_regionales(id),
    codigo_almacen text not null check (btrim(codigo_almacen) <> '' and upper(codigo_almacen) <> 'TOTALES'),
    datos jsonb not null check (jsonb_typeof(datos) = 'object'),
    primary key (reporte_id, codigo_almacen),
    check (datos->>'codigo' is not null and datos->>'codigo' = codigo_almacen)
);

alter table public.reportes_regionales enable row level security;
alter table public.reporte_regional_detalle enable row level security;
-- Sin políticas públicas: ni anon ni authenticated tienen acceso a estas tablas.
revoke all on public.reportes_regionales, public.reporte_regional_detalle from public, anon, authenticated;
grant select, insert, update on public.reportes_regionales to service_role;
grant select, insert on public.reporte_regional_detalle to service_role;

create function public.regional_verificar_actor(p_actor text)
returns void language plpgsql security invoker set search_path = '' as $$
begin
    -- Misma autorización que el menú y el guard de Vista Regional. El puesto
    -- descriptivo Gerente no confiere permisos por sí mismo en esta app.
    if not exists (select 1 from public.usuarios
                   where username = p_actor and lower(btrim(rol)) = 'admin') then
        raise exception 'REGIONAL_NO_AUTORIZADO';
    end if;
end;
$$;

create function public.regional_publicar_reporte(
    p_actor text, p_id uuid, p_fecha date, p_detalle jsonb, p_totales jsonb
) returns uuid language plpgsql security invoker set search_path = '' as $$
declare
    fila jsonb;
    campo text;
    campos text[] := array['# Doc','Unidades','Venta Bruta','Descuento',
        'Pagos No Venta','Venta','Impuestos','ComiTarjeta','ImpAsumido','Venta Neta'];
begin
    perform public.regional_verificar_actor(p_actor);
    if p_id is null or p_fecha is null
       or jsonb_typeof(p_detalle) is distinct from 'array'
       or jsonb_typeof(p_totales) is distinct from 'array' then
        raise exception 'REGIONAL_DATOS_INVALIDOS';
    end if;
    if jsonb_array_length(p_detalle) = 0 or jsonb_array_length(p_totales) > 1 then
        raise exception 'REGIONAL_DATOS_INVALIDOS';
    end if;
    for fila in select value from jsonb_array_elements(p_detalle || p_totales) loop
        if jsonb_typeof(fila) is distinct from 'object' then
            raise exception 'REGIONAL_DATOS_INVALIDOS';
        end if;
        foreach campo in array campos loop
            if jsonb_typeof(fila->campo) is distinct from 'number' then
                raise exception 'REGIONAL_DATOS_INVALIDOS';
            end if;
        end loop;
        if not (fila ? 'Mt2') or (jsonb_typeof(fila->'Mt2') <> 'null'
                                  and jsonb_typeof(fila->'Mt2') <> 'number') then
            raise exception 'REGIONAL_DATOS_INVALIDOS';
        end if;
        if mod((fila->>'# Doc')::numeric, 1) <> 0 or mod((fila->>'Unidades')::numeric, 1) <> 0 then
            raise exception 'REGIONAL_DATOS_INVALIDOS';
        end if;
    end loop;
    for fila in select value from jsonb_array_elements(p_detalle) loop
        if jsonb_typeof(fila->'codigo') is distinct from 'string'
           or jsonb_typeof(fila->'nombre') is distinct from 'string'
           or btrim(fila->>'codigo') = '' or btrim(fila->>'nombre') = ''
           or fila->>'codigo' <> upper(btrim(fila->>'codigo'))
           or upper(btrim(fila->>'codigo')) = 'TOTALES'
           or upper(btrim(fila->>'nombre')) = 'TOTALES' then
            raise exception 'REGIONAL_DATOS_INVALIDOS';
        end if;
    end loop;
    -- Serializa cargas concurrentes. Lectores ven la versión anterior completa
    -- hasta el commit; cualquier error revierte cabecera, detalle y vigente.
    perform pg_advisory_xact_lock(724631904004::bigint);
    insert into public.reportes_regionales(id, fecha_reporte, actor, totales)
        values (p_id, p_fecha, p_actor, p_totales);
    insert into public.reporte_regional_detalle(reporte_id, codigo_almacen, datos)
        select p_id, value->>'codigo', value from jsonb_array_elements(p_detalle);
    -- La PK de detalle rechaza duplicados antes de retirar el vigente anterior.
    update public.reportes_regionales set vigente = false where vigente;
    update public.reportes_regionales set vigente = true where id = p_id;
    return p_id;
end;
$$;

create function public.regional_obtener_vigente(p_actor text)
returns jsonb language plpgsql security invoker set search_path = '' as $$
declare resultado jsonb;
begin
    perform public.regional_verificar_actor(p_actor);
    -- Una sola consulta conserva una instantánea coherente entre cabecera/detalle.
    select jsonb_build_object('id', r.id, 'fecha_reporte', r.fecha_reporte,
        'totales', r.totales, 'detalle',
        (select jsonb_agg(d.datos order by d.codigo_almacen)
         from public.reporte_regional_detalle d where d.reporte_id = r.id))
    into resultado from public.reportes_regionales r where r.vigente;
    return resultado;
end;
$$;

revoke all on function public.regional_verificar_actor(text) from public, anon, authenticated;
revoke all on function public.regional_obtener_vigente(text) from public, anon, authenticated;
revoke all on function public.regional_publicar_reporte(text,uuid,date,jsonb,jsonb) from public, anon, authenticated;
grant execute on function public.regional_verificar_actor(text) to service_role;
grant execute on function public.regional_obtener_vigente(text) to service_role;
grant execute on function public.regional_publicar_reporte(text,uuid,date,jsonb,jsonb) to service_role;
notify pgrst, 'reload schema';
commit;
