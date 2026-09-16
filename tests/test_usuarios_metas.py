"""Pruebas sin credenciales ni servicios externos; extraen funciones del app real."""
import ast
import io
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
import pandas as pd

APP = Path(__file__).resolve().parents[1] / 'app_nuevo.py'
TREE = ast.parse(APP.read_text(encoding='utf-8-sig'))
NAMES = {'texto_usuario', 'codigo_usuario', 'meta_usuario',
         'guardar_usuario_verificado', 'preparar_metas_dashboard'}
NS = {'pd': pd, 're': re}
exec(compile(ast.Module(body=[n for n in TREE.body if isinstance(n, ast.FunctionDef)
                             and n.name in NAMES], type_ignores=[]), str(APP), 'exec'), NS)
globals().update({n: NS[n] for n in NAMES})

class Cliente:
    def __init__(self, rows, ignorar=False, ocultar=False):
        self.rows = rows
        self.ignorar, self.ocultar = ignorar, ocultar
        self.updates = []
    def table(self, name):
        assert name == 'usuarios'
        return Consulta(self)

class Consulta:
    def __init__(self, cliente):
        self.c = cliente
        self.payload = None
    def update(self, payload):
        self.payload = payload
        return self
    def select(self, campos):
        return self
    def eq(self, campo, valor):
        assert campo == 'username'
        self.username = valor
        return self
    def execute(self):
        rows = [r for r in self.c.rows if not hasattr(self, 'username') or r['username'] == self.username]
        if self.payload is not None:
            self.c.updates.append((self.username, self.payload.copy()))
            if not self.c.ignorar:
                for r in rows:
                    r.update(self.payload)
        return SimpleNamespace(data=[] if self.c.ocultar else [r.copy() for r in rows])

class UsuariosMetasTests(unittest.TestCase):
    def test_persistir_perfil_completo_sin_tocar_otros_campos(self):
        c = Cliente([dict(username='ana', nombre_completo='Mal', password='anterior', meta_mensual=500)])
        cambios = dict(nombre_completo='Ana', puesto='Subgerente', rol='asesor', permisos=['revisar_cierre'])
        self.assertEqual(guardar_usuario_verificado(c, 'ana', cambios)['nombre_completo'], 'Ana')
        self.assertEqual(c.updates, [('ana', cambios)])
        self.assertEqual(c.rows[0]['password'], 'anterior')
        self.assertEqual(c.rows[0]['meta_mensual'], 500)
    def test_guardado_ignorado_no_confirma_exito(self):
        c = Cliente([dict(username='ana', nombre_completo='Original')], ignorar=True)
        with self.assertRaisesRegex(ValueError, 'nombre_completo'):
            guardar_usuario_verificado(c, 'ana', {'nombre_completo': 'Nuevo'})
    def test_registro_inexistente_o_invisible_no_confirma(self):
        for c in (Cliente([]), Cliente([dict(username='ana')], ocultar=True)):
            with self.assertRaises(ValueError):
                guardar_usuario_verificado(c, 'ana', {'nombre_completo': 'Nuevo'})
    def test_password_opcional_explicita(self):
        c = Cliente([dict(username='ana')])
        guardar_usuario_verificado(c, 'ana', {'password': 'nueva'})
        self.assertEqual(c.rows[0]['password'], 'nueva')
    def test_permisos_explicitos_vacios(self):
        c = Cliente([dict(username='ana', rol='admin', permisos=['revisar_cierre'])])
        guardar_usuario_verificado(c, 'ana', {'permisos': []})
        self.assertEqual(c.rows[0]['permisos'], [])
    def test_metas_codigo_normalizado_y_subgerente_sin_ventas(self):
        v = pd.DataFrame({'codigo': [' AB1 ', '123.0'], 'venta': [20, 30]})
        u = pd.DataFrame([dict(username='ana', codigo_erp='ab1', meta_mensual=100),
                          dict(username='bea', codigo_erp=123, meta_mensual=200),
                          dict(username='subgerente', codigo_erp='nuevo', meta_mensual=300)])
        result, total = preparar_metas_dashboard(v, u)
        self.assertEqual(result.meta_mensual.tolist(), [100, 200])
        self.assertEqual(total, 600)
    def test_guardar_y_releer_dashboard(self):
        c = Cliente([dict(username='sub', codigo_erp='SUB', meta_mensual=10)])
        v = pd.DataFrame({'codigo': ['sub']})
        self.assertEqual(preparar_metas_dashboard(v, pd.DataFrame(c.rows))[1], 10)
        guardar_usuario_verificado(c, 'sub', {'meta_mensual': 80.0})
        result, total = preparar_metas_dashboard(v, pd.DataFrame(c.rows))
        self.assertEqual(total, 80)
        self.assertEqual(result.meta_mensual.iloc[0], 80)
    def test_meta_cero_sin_default_inventado(self):
        _, total = preparar_metas_dashboard(pd.DataFrame({'codigo': ['ana']}),
                         pd.DataFrame([dict(username='ana', meta_mensual=0)]))
        self.assertEqual(total, 0)
    def test_compatibilidad_columnas_ausentes_y_codigo_nulo(self):
        result, total = preparar_metas_dashboard(pd.DataFrame({'codigo': ['ana']}),
                             pd.DataFrame([dict(username='ana', meta_mensual=50)]))
        self.assertEqual(result.meta_mensual.iloc[0], 50)
        result, total = preparar_metas_dashboard(pd.DataFrame({'codigo': ['ana']}), pd.DataFrame())
        self.assertEqual(total, 0)
        self.assertEqual(result.meta_mensual.iloc[0], 0)
    def test_duplicados_no_multiplican_ventas(self):
        with self.assertRaisesRegex(ValueError, 'repetidos'):
            preparar_metas_dashboard(pd.DataFrame({'codigo': ['a']}), pd.DataFrame([
                dict(username='uno', codigo_erp='A', meta_mensual=10),
                dict(username='dos', codigo_erp=' a ', meta_mensual=20)]))
    def test_reporte_repetido_no_duplica_total_meta(self):
        result, total = preparar_metas_dashboard(pd.DataFrame({'codigo': ['a', 'a']}),
                  pd.DataFrame([dict(username='a', meta_mensual=40)]))
        self.assertEqual(len(result), 2)
        self.assertEqual(total, 40)
    def test_codigos_ceros_iniciales_no_se_pierden(self):
        df = pd.read_csv(io.StringIO('codigo\n00123\n'), dtype={'codigo': str})
        self.assertEqual(codigo_usuario(df.codigo.iloc[0]), '00123')
        self.assertNotEqual(codigo_usuario('00123'), codigo_usuario('123'))
    def test_valores_meta_invalidos(self):
        for valor in (-1, float('inf'), 'abc'):
            with self.assertRaises(ValueError): meta_usuario(valor)
        for valor in (None, float('nan'), '', 0):
            self.assertEqual(meta_usuario(valor), 0)

class FormularioTests(unittest.TestCase):
    def ejecutar_formulario(self, ignorar=False):
        from streamlit.testing.v1 import AppTest
        source = APP.read_text(encoding='utf-8')
        pagina = source[source.index('if pagina_actual == "👥 Gestión Usuarios":'):]
        harness = """
import streamlit as st
import runpy
import pandas as pd
ns = runpy.run_path(TEST_PATH)
globals().update({n: ns[n] for n in ns['NAMES']})
if 'cliente_prueba' not in st.session_state:
    st.session_state.cliente_prueba = ns['Cliente']([
        dict(username='ana', nombre_completo='Original', puesto='Asesor',
             rol='asesor', permisos=[], meta_mensual=50)], ignorar=IGNORAR)
supabase = st.session_state.cliente_prueba
st.session_state.usuario_actual = 'admin'
permisos_actuales = {'administrar_usuarios'}
PUESTOS = ('Gerente', 'Subgerente', 'Cajero', 'Asesor')
PERMISOS = {'administrar_usuarios': 'Administrar usuarios', 'revisar_cierre': 'Revisar cierre'}
def puesto_usuario(p): return p['puesto']
def permisos_usuario(p): return set(p.get('permisos') or [])
pagina_actual = '👥 Gestión Usuarios'
""".replace('TEST_PATH', repr(str(Path(__file__).resolve()))).replace('IGNORAR', repr(ignorar))
        return AppTest.from_string(harness + pagina).run()

    def test_formulario_metas_guarda_y_reinicia_editor(self):
        from streamlit.testing.v1 import AppTest
        source = APP.read_text(encoding='utf-8')
        start = source.index('if pagina_actual == "🎯 Metas por asesor":')
        end = source.index('# 10. PESTAÑA:', start)
        pagina = source[start:end]
        harness = """
import streamlit as st
import runpy
import pandas as pd
ns = runpy.run_path(TEST_PATH)
globals().update({n: ns[n] for n in ns['NAMES']})
if 'cliente_prueba' not in st.session_state:
    st.session_state.cliente_prueba = ns['Cliente']([
        dict(username='sub', nombre_completo='Subgerente', codigo_erp='SUB', meta_mensual=50)])
supabase = st.session_state.cliente_prueba
st.session_state.es_admin = True
pagina_actual = '🎯 Metas por asesor'
""".replace('TEST_PATH', repr(str(Path(__file__).resolve())))
        at = AppTest.from_string(harness + pagina).run()
        self.assertFalse(at.exception)
        at.session_state['editor_metas_0'] = {
            'edited_rows': {0: {'meta_mensual': 120.0}}, 'added_rows': [], 'deleted_rows': []}
        [b for b in at.button if b.label == 'Guardar Cambios de Metas'][0].click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state.cliente_prueba.rows[0]['meta_mensual'], 120)
        self.assertEqual(at.session_state.revision_metas, 1)
        self.assertEqual(at.session_state.cliente_prueba.updates, [('sub', {'meta_mensual': 120.0})])

    def test_nombre_formulario_persiste_despues_de_rerun(self):
        at = self.ejecutar_formulario()
        self.assertFalse(at.exception)
        [t for t in at.text_input if t.label == 'Nombre completo'][-1].input('Nombre corregido')
        [b for b in at.button if b.label == 'Guardar puesto y permisos'][0].click()
        at.run()
        self.assertFalse(at.exception)
        self.assertEqual(at.session_state.cliente_prueba.rows[0]['nombre_completo'], 'Nombre corregido')
        self.assertEqual([t for t in at.text_input if t.label == 'Nombre completo'][-1].value, 'Nombre corregido')

    def test_formulario_no_anuncia_exito_si_supabase_ignora_update(self):
        at = self.ejecutar_formulario(ignorar=True)
        [t for t in at.text_input if t.label == 'Nombre completo'][-1].input('Nombre corregido')
        [b for b in at.button if b.label == 'Guardar puesto y permisos'][0].click()
        at.run()
        self.assertFalse(at.exception)
        self.assertTrue(at.error)
        self.assertFalse(at.success)


if __name__ == '__main__':
    unittest.main()
