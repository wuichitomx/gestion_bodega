"""Round-trip real y flujos aislados; nunca conecta al Supabase de producción."""
from datetime import date
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import pandas as pd
from streamlit.testing.v1 import AppTest

from regional_persistencia import (ErrorRegional, RepositorioRegional,
                                  restaurar_reporte, serializar_reporte)
from vista_regional import leer_reporte, referencia_regional
from test_vista_regional import libro


def datos_prueba():
    df, control, _, _ = leer_reporte(libro([
        {'codigo': 'Z1GEX', 'Venta': 100, 'Unidades': 2, '# Doc': 1},
        {'codigo': 'Z1GPP', 'Venta': 900, 'Unidades': 18, '# Doc': 3, 'Mt2': 0}]))
    return df, control


class PersistenciaTests(unittest.TestCase):
    def test_round_trip_y_benchmark(self):
        df, control = datos_prueba()
        datos = serializar_reporte(df, control)
        self.assertNotIn('estado', datos['detalle'][0])
        self.assertNotIn('UPT', datos['detalle'][0])
        self.assertEqual(datos['detalle'][1]['Mt2'], 0)
        nuevo = restaurar_reporte(dict(id='x', fecha_reporte='2026-09-23', **datos))
        pd.testing.assert_frame_equal(nuevo['df'], df)
        pd.testing.assert_frame_equal(nuevo['control'], control)
        self.assertEqual(referencia_regional(nuevo['df'])['Venta x Mt2'], 10)
        self.assertEqual(referencia_regional(nuevo['df'])['ATV'], 250)
        self.assertTrue(math.isnan(nuevo['df'].iloc[1]['Venta x Mt2']))

    def test_area_nula_y_sin_totales(self):
        df, control, _, _ = leer_reporte(libro([{'Mt2': None}], total=False))
        datos = serializar_reporte(df, control)
        self.assertIsNone(datos['detalle'][0]['Mt2'])
        json.dumps(datos, allow_nan=False)
        nuevo = restaurar_reporte(dict(id='x', fecha_reporte='2026-09-23', **datos))
        self.assertTrue(nuevo['control'].empty)
        self.assertIn('No hay fila TOTALES', nuevo['avisos'][0])
        self.assertTrue(math.isnan(nuevo['df'].iloc[0]['Mt2']))

    def test_payload_invalido_y_totales_no_tienda(self):
        df, control = datos_prueba()
        for cambio in ({'codigo': 'TOTALES'}, {'Venta': None}, {'# Doc': 1.5}, {'Venta': float('inf')}):
            datos = serializar_reporte(df, control)
            datos['detalle'][0].update(cambio)
            with self.assertRaises(ValueError):
                restaurar_reporte(dict(id='x', fecha_reporte='2026-09-23', **datos))

    def test_rpc_sin_reporte_y_actualizacion_en_una_llamada(self):
        cliente = Mock()
        cliente.rpc.return_value.execute.return_value = SimpleNamespace(data=None)
        repo = RepositorioRegional(cliente, 'admin_test')
        self.assertIsNone(repo.cargar())
        df, control = datos_prueba()
        repo.guardar(date(2026, 9, 23), df, control)
        nombre, params = cliente.rpc.call_args.args
        self.assertEqual(nombre, 'regional_publicar_reporte')
        self.assertEqual(params['p_fecha'], '2026-09-23')
        self.assertEqual(params['p_actor'], 'admin_test')
        self.assertEqual(len(params['p_detalle']), 2)
        self.assertFalse(cliente.table.called)
        cliente.rpc.return_value.execute.return_value = SimpleNamespace(data=dict(
            id=params['p_id'], fecha_reporte=params['p_fecha'],
            detalle=params['p_detalle'], totales=params['p_totales']))
        otro_dispositivo = RepositorioRegional(cliente, 'admin_test')
        self.assertEqual(otro_dispositivo.cargar()['df'].Venta.sum(), 1000)
        self.assertEqual(repo.cargar()['fecha'], date(2026, 9, 23))

    def test_errores_no_se_confunden_con_reporte_ausente(self):
        cliente = Mock()
        cliente.rpc.return_value.execute.side_effect = RuntimeError('secreto y datos privados')
        repo = RepositorioRegional(cliente, 'admin_test')
        with self.assertRaises(ErrorRegional) as exc:
            repo.cargar()
        self.assertNotIn('secreto', str(exc.exception))
        df, control = datos_prueba()
        with self.assertRaises(ErrorRegional):
            repo.guardar(date(2026, 9, 23), df, control)
        cliente.reset_mock()
        with self.assertRaises(ErrorRegional):
            repo.guardar(None, df, control)
        self.assertFalse(cliente.rpc.called)

    @unittest.skipUnless(os.environ.get('REGIONAL_EXCEL_REAL'), 'Requiere Excel real local')
    def test_excel_real_igual_despues_de_supabase(self):
        df, control, avisos, _ = leer_reporte(Path(os.environ['REGIONAL_EXCEL_REAL']).read_bytes())
        nuevo = restaurar_reporte(dict(id='x', fecha_reporte='2026-09-23',
                                      **serializar_reporte(df, control)))
        pd.testing.assert_frame_equal(nuevo['df'], df)
        pd.testing.assert_frame_equal(nuevo['control'], control)
        self.assertEqual(nuevo['avisos'], avisos)
        self.assertEqual(len(nuevo['df']), 17)
        self.assertEqual(referencia_regional(nuevo['df']), referencia_regional(df))


# Almacenamiento simulado compartido entre reruns; la vista nunca conserva el vigente.
UI = '''
from datetime import date
from io import BytesIO
import streamlit as st
from unittest.mock import patch
from vista_regional import mostrar_vista_regional, leer_reporte, ADITIVOS
from regional_persistencia import ErrorRegional, serializar_reporte, restaurar_reporte
from openpyxl import Workbook
w = Workbook()
w.active.append(['Código de Almacén','Nombre de Almacén',*ADITIVOS,'Mt2'])
w.active.append(['Z1GEX','Tienda', *[100 if k == 'Venta' else 2 for k in ADITIVOS],10])
archivo = BytesIO()
w.save(archivo)
archivo.name = 'NOMBRE_PRIVADO.xlsx'
class Repo:
    def cargar(self):
        if st.session_state.get('fallo_lectura'):
            raise ErrorRegional('Error de conexión')
        d = st.session_state.get('base')
        return restaurar_reporte(d) if d else None
    def guardar(self, fecha, df, control):
        if st.session_state.get('fallo_guardado'):
            raise ErrorRegional('Error de guardado')
        st.session_state['base'] = dict(id='nuevo', fecha_reporte=fecha.isoformat(),
                                       **serializar_reporte(df, control))
with patch('streamlit.file_uploader', return_value=archivo):
    mostrar_vista_regional(Repo())
'''


class FlujoTests(unittest.TestCase):
    def vigente(self):
        df, control = datos_prueba()
        return dict(id='anterior', fecha_reporte='2026-09-22', **serializar_reporte(df, control))

    def test_creacion_y_actualizacion_fecha(self):
        app = AppTest.from_string(UI, default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.metric), 0)
        app.button[0].click().run()
        self.assertIn('Selecciona', app.error[0].value)
        app.date_input[0].set_value(date(2026, 9, 23))
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.metric), 8)
        self.assertIn('Fecha del reporte: 23/09/2026', [c.value for c in app.caption])
        app.date_input[0].set_value(date(2026, 9, 24))
        app.button[0].click().run()
        self.assertIn('Fecha del reporte: 24/09/2026', [c.value for c in app.caption])
        self.assertNotIn('NOMBRE_PRIVADO', str([c.value for c in app.caption]))

    def test_error_guardado_conserva_vista(self):
        app = AppTest.from_string(UI, default_timeout=30)
        app.session_state['base'] = self.vigente()
        app.session_state['fallo_guardado'] = True
        app.run()
        app.date_input[0].set_value(date(2026, 9, 23))
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.session_state['base']['id'], 'anterior')
        self.assertEqual(len(app.metric), 8)
        self.assertIn('Fecha del reporte: 22/09/2026', [c.value for c in app.caption])

    def test_error_lectura_no_ofrece_crear_sobre_estado_desconocido(self):
        app = AppTest.from_string(UI, default_timeout=30)
        app.session_state['fallo_lectura'] = True
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.error), 1)
        self.assertEqual(len(app.button), 0)

    def test_nueva_sesion_consulta_vigente_y_refresca(self):
        app = AppTest.from_string(UI, default_timeout=30)
        app.session_state['base'] = self.vigente()
        app.run()
        self.assertEqual(len(app.metric), 8)
        reemplazo = self.vigente()
        reemplazo['fecha_reporte'] = '2026-09-25'
        app.session_state['base'] = reemplazo
        app.run()
        self.assertIn('Fecha del reporte: 25/09/2026', [c.value for c in app.caption])


if __name__ == '__main__':
    unittest.main()
