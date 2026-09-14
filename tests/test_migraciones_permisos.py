"""Contratos estáticos de las migraciones: no conectan a PostgreSQL ni ejecutan SQL."""
from pathlib import Path
import re
import unittest

MIGRACIONES = Path(__file__).resolve().parents[1] / 'migrations'


def sql_sin_comentarios(nombre):
    return re.sub(r'--[^\n]*', '', (MIGRACIONES / nombre).read_text(encoding='utf-8')).lower()


class ConvivenciaMigracionesTests(unittest.TestCase):
    def test_002_mantiene_v1_y_v2_para_service_role(self):
        sql = sql_sin_comentarios('002_puestos_permisos_responsables.sql')
        self.assertNotRegex(sql, r'revoke\b[^;]*\bcajas_guardar_jornada\s*\(')
        for funcion in ('cajas_guardar_jornada', 'cajas_guardar_jornada_v2'):
            self.assertRegex(sql, rf'grant execute on function public\.{funcion}\([^;]*\) to service_role;')
        self.assertNotRegex(sql, r'create or replace function public\.cajas_guardar_jornada\s*\(')

    def test_002_inicializa_solo_nulos_sin_borrar_datos(self):
        sql = sql_sin_comentarios('002_puestos_permisos_responsables.sql')
        inicializacion = re.search(r'update public\.usuarios\s+set permisos\s*=.*?;', sql, re.S).group()
        self.assertRegex(inicializacion, r'where permisos is null\s*;')
        self.assertIn("when 'cajero' then '[\"realizar_arqueo\"]'::jsonb", inicializacion)
        self.assertIn("else '[]'::jsonb", inicializacion)
        for permiso in ('realizar_arqueo', 'revisar_cierre', 'capturar_facturacion', 'preparar_correo', 'administrar_usuarios'):
            self.assertIn(permiso, inicializacion.split("when 'cajero'")[0])
        self.assertNotRegex(sql, r'\b(delete\s+from|truncate|drop\s+(table|function))\b')
        self.assertIn("= 'cajero' then p_permiso = 'realizar_arqueo'", sql)

    def test_003_solo_retira_ejecucion_v1(self):
        sql = sql_sin_comentarios('003_retirar_cajas_guardar_jornada_v1.sql')
        self.assertRegex(sql, r'revoke execute on function public\.cajas_guardar_jornada\([^;]*service_role;')
        self.assertNotIn('cajas_guardar_jornada_v2', sql)
        self.assertNotRegex(sql, r'\b(update|insert|delete|truncate|drop|alter)\b')


if __name__ == '__main__':
    unittest.main()
