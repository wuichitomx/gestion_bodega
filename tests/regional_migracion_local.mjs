// Sólo PostgreSQL WASM en memoria. Nunca recibe una URL de Supabase.
// npm install --prefix <temporal> @electric-sql/pglite@0.5.8
// REGIONAL_PGLITE_MODULE=<temporal>/node_modules/@electric-sql/pglite/dist/index.js
// node tests/regional_migracion_local.mjs
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { pathToFileURL } from 'node:url';
const { PGlite } = await import(pathToFileURL(process.env.REGIONAL_PGLITE_MODULE).href);
const db = new PGlite();
let checks = 0;
const check = (condition) => { assert.ok(condition); checks++; };
const denied = async (sql, args = []) => {
  await assert.rejects(db.query(sql, args)); checks++;
};
try {
  await db.exec(`
    create role anon; create role authenticated; create role service_role bypassrls;
    create table public.usuarios(username text primary key, rol text, puesto text);
    insert into public.usuarios values ('administrador','admin','Gerente'), ('asesor','asesor','Gerente');
    grant usage on schema public to anon, authenticated, service_role;
    grant select on public.usuarios to service_role;
  `);
  await db.exec(await readFile(new URL('../migrations/004_reportes_regionales.sql', import.meta.url), 'utf8'));
  const campos = ['# Doc','Unidades','Venta Bruta','Descuento','Pagos No Venta',
    'Venta','Impuestos','ComiTarjeta','ImpAsumido','Venta Neta'];
  const fila = {codigo: 'Z1GEX', nombre: 'Tienda', Mt2: 0,
    ...Object.fromEntries(campos.map(k => [k, 10]))};
  const publishSql = 'select public.regional_publicar_reporte($1,$2,$3,$4,$5)';
  const publish = (id, detalle = [fila], actor = 'administrador') => db.query(
    publishSql, [actor, id, '2026-09-23', JSON.stringify(detalle), '[]']);
  const current = async () => (await db.query(
    "select public.regional_obtener_vigente('administrador') as reporte")).rows[0].reporte;
  const id1 = '00000000-0000-4000-8000-000000000001';
  const id2 = '00000000-0000-4000-8000-000000000002';
  const id3 = '00000000-0000-4000-8000-000000000003';
  await db.exec('set role service_role');
  check(await current() === null);
  await publish(id1);
  check((await current()).id === id1);
  check((await current()).detalle[0].Mt2 === 0);
  await publish(id2, [{...fila, Venta: 150, Mt2: null}]);
  check((await current()).id === id2);
  check((await current()).detalle[0].Venta === 150);
  check((await db.query('select count(*)::int as n from public.reportes_regionales')).rows[0].n === 2);
  check((await db.query('select count(*)::int as n from public.reportes_regionales where vigente')).rows[0].n === 1);
  for (const invalid of [[fila, fila], [{...fila, codigo: 'TOTALES'}],
      [{...fila, Venta: null}], [{...fila, '# Doc': 1.5}], []]) {
    await assert.rejects(publish(id3, invalid)); checks++;
    check((await current()).id === id2);
  }
  await assert.rejects(publish(id3, [fila], 'asesor')); checks++;
  await denied("select public.regional_obtener_vigente('asesor')");
  await denied("select public.regional_obtener_vigente('inexistente')");
  await denied('update public.reportes_regionales set vigente = true where id = $1', [id1]);
  // Fallo inducido después de escribir cabecera y antes de terminar detalles.
  await db.exec(`reset role;
    create function public.fallo_regional_test() returns trigger language plpgsql as $$
    begin if new.codigo_almacen = 'FALLO' then raise exception 'FALLO_INDUCIDO'; end if; return new; end $$;
    create trigger fallo_test before insert on public.reporte_regional_detalle
      for each row execute function public.fallo_regional_test();
    set role service_role;`);
  await assert.rejects(publish(id3, [fila, {...fila, codigo: 'FALLO'}])); checks++;
  check((await current()).id === id2);
  check((await db.query('select count(*)::int as n from public.reportes_regionales')).rows[0].n === 2);
  check((await db.query('select count(*)::int as n from public.reporte_regional_detalle')).rows[0].n === 2);
  for (const role of ['anon', 'authenticated']) {
    await db.exec(`reset role; set role ${role}`);
    await denied('select * from public.reportes_regionales');
    await denied('select * from public.reporte_regional_detalle');
    await denied("select public.regional_obtener_vigente('administrador')");
    await denied(publishSql, ['administrador', id3, '2026-09-23', JSON.stringify([fila]), '[]']);
  }
  await db.exec("reset role; update public.usuarios set rol = 'asesor' where username = 'administrador'; set role service_role;");
  await denied("select public.regional_obtener_vigente('administrador')");
  await assert.rejects(publish(id3)); checks++;
  console.log(`${checks} comprobaciones PostgreSQL locales correctas: permisos, histórico, vigente único y rollback.`);
} finally {
  await db.close();
}
