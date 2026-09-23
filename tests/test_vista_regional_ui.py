"""Pruebas locales de widgets; no inicia la app autenticada ni llama a Supabase."""
import os
from pathlib import Path
import unittest
import math

from streamlit.testing.v1 import AppTest


class VistaTests(unittest.TestCase):
    def test_sin_archivo(self):
        app = AppTest.from_string('''
from unittest.mock import Mock
from vista_regional import mostrar_vista_regional
mostrar_vista_regional(Mock(cargar=Mock(return_value=None)))
''', default_timeout=30).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe[0].value), 17)

    @unittest.skipUnless(os.environ.get("REGIONAL_EXCEL_REAL"), "Requiere Excel real local")
    def test_archivo_real_y_navegacion(self):
        app = AppTest.from_string('''
import io, os
from pathlib import Path
from unittest.mock import Mock
from vista_regional import mostrar_vista_regional, leer_reporte
from regional_persistencia import serializar_reporte, restaurar_reporte
df, control, _, _ = leer_reporte(Path(os.environ['REGIONAL_EXCEL_REAL']).read_bytes())
datos = dict(id='prueba', fecha_reporte='2026-09-23', **serializar_reporte(df, control))
mostrar_vista_regional(Mock(cargar=Mock(return_value=restaurar_reporte(datos))))
''', default_timeout=30).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.metric), 8)
        self.assertEqual([h.value for h in app.subheader][:3],
                         ["México por estado", "Dashboard regional de KPIs", "Ranking de sucursales"])
        # Mapa + cuatro KPIs + secundario: detectar una llamada ausente al dashboard.
        self.assertEqual(len(app.get("vega_lite_chart")), 6)
        referencias = [m.value for m in app.metric][4:]
        self.assertEqual(len(app.dataframe[1].value), 17)
        app.selectbox(key="regional_estado").select("Yucatán").run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.dataframe[1].value), 2)
        self.assertEqual([m.value for m in app.metric][4:], referencias)
        self.assertEqual(len(app.get("vega_lite_chart")), 6)
        app.toggle(key="regional_graficas_amplias").set_value(True).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.get("vega_lite_chart")), 6)
        app.selectbox(key="regional_tienda").select("Z1H4Q").run()
        self.assertEqual(app.subheader[-1].value, "YACS KIDS MERIDA")
        app.selectbox(key="regional_estado").select("Michoacán").run()
        self.assertEqual(set(app.dataframe[1].value.codigo), {"Z1GEX", "Z1CFF", "Z1IQF"})
        app.selectbox(key="regional_estado").select("Oaxaca").run()
        self.assertTrue(math.isnan(app.dataframe[1].value.iloc[0]["Venta x Mt2"]))
        self.assertEqual(len(app.get("vega_lite_chart")), 5)
        self.assertIn("Sin tiendas con Mt2 > 0", app.info[0].value)
        self.assertEqual([m.value for m in app.metric][4:], referencias)
        app.selectbox(key="regional_estado").select("Sonora").run()
        self.assertEqual(len(app.exception), 0)
        self.assertIn("no tiene sucursales", app.info[0].value)


if __name__ == "__main__":
    unittest.main()
