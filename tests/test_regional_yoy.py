import math
import os
from pathlib import Path
import unittest

from openpyxl import load_workbook

from vista_regional import ADITIVOS, leer_reporte, reconstruir_reporte
from regional_yoy import KPIS, comparar, ranking, variacion


def datos(*filas):
    registros = []
    for cod, venta, unidades, docs in filas:
        fila = dict.fromkeys(ADITIVOS, 0)
        fila.update(codigo=cod, nombre="Tienda " + cod, Venta=venta,
                    Unidades=unidades, Mt2=10)
        fila["# Doc"] = docs
        fila["Venta Bruta"] = venta * 1.1
        fila["Descuento"] = venta * .1
        fila["Venta Neta"] = venta / 1.16
        registros.append(fila)
    return reconstruir_reporte(registros)[0]


class ComparacionTests(unittest.TestCase):
    def test_union_base_minima_y_ausencias(self):
        a = datos(("A", 200, 60, 40), ("B", 1000, 100, 50), ("N", 500, 50, 40))
        b = datos(("A", 100, 40, 30), ("B", 2, 4, 2), ("X", 400, 50, 40))
        region, tiendas = comparar(a, b)
        self.assertEqual(region.set_index("KPI").loc["Venta", "Actual"], 1700)
        t = tiendas.set_index("codigo")
        self.assertEqual(t.loc["B", "Estado"], "Base insuficiente")
        self.assertTrue(math.isnan(t.loc["B", "Venta YoY %"]))
        self.assertTrue(math.isnan(t.loc["X", "Venta actual"]))
        self.assertTrue(math.isnan(t.loc["N", "Venta diferencia"]))
        self.assertEqual(t.loc["N", "Estado"], "Nueva / sin base comparable")
        self.assertEqual(t.loc["X", "Estado"], "Sin dato actual")
        self.assertEqual(t.loc["A", "Venta YoY %"], 1)
        region, tiendas = comparar(a, b, True)
        self.assertEqual(set(tiendas.codigo), {"A"})
        self.assertEqual(region.set_index("KPI").loc["Venta", "Anterior"], 100)
        self.assertAlmostEqual(region.set_index("KPI").loc["UPT", "Actual"], 60 / 40)

    def test_cero_negativos_y_umbral(self):
        for base in (0, -1, float("nan")):
            self.assertTrue(math.isnan(variacion(10, base, 30)))
        self.assertTrue(math.isnan(variacion(10, 1, 29)))
        self.assertEqual(variacion(0, 10, 30), -1)
        self.assertEqual(variacion(10, 1, 30), 9)
        _, tiendas = comparar(datos(("A", 100, 40, 40)), datos(("A", 0, 40, 40)))
        self.assertEqual(ranking(tiendas, "Venta").iloc[0].Estado, "Base insuficiente")
        self.assertTrue(math.isnan(ranking(tiendas, "Venta").iloc[0].Cambio))

    def test_sin_codigos_comunes(self):
        regional, tiendas = comparar(datos(("A", 100, 40, 40)), datos(("B", 100, 40, 40)), True)
        self.assertTrue(tiendas.empty)
        self.assertTrue(regional.Actual.isna().all())


@unittest.skipUnless(os.environ.get("YOY_ACTUAL") and os.environ.get("YOY_ANTERIOR"), "Requiere los dos Excel reales")
class ArchivosRealesTests(unittest.TestCase):
    def test_calculo_independiente(self):
        # Ruta independiente: posiciones del ERP verificadas en los dos ejemplos.
        # No usa el lector regional ni sus funciones para obtener los esperados.
        fuentes, leidos = [], []
        for variable in ("YOY_ACTUAL", "YOY_ANTERIOR"):
            path = Path(os.environ[variable])
            wb = load_workbook(path, read_only=True, data_only=True)
            filas = list(wb.active.values)[3:]
            bruto = {str(r[3]).strip(): r for r in filas if len(r) > 15 and r[3] and str(r[3]).strip() != "TOTALES"}
            wb.close()
            df, control, _, _ = leer_reporte(path)
            self.assertEqual(set(df.codigo), set(bruto))
            self.assertTrue(control[control.Campo.ne("Mt2")].Coincide.all())
            fuentes.append(bruto)
            leidos.append(df)
        self.assertEqual([len(f) for f in fuentes], [17, 15])
        comunes = set(fuentes[0]) & set(fuentes[1])
        self.assertEqual(len(comunes), 14)
        suficientes = {c for c in comunes if float(fuentes[1][c][5]) >= 30}
        self.assertEqual(len(suficientes), 13)
        for comparable in (False, True):
            region, tiendas = comparar(*leidos, comparable)
            esperados = []
            for fuente in fuentes:
                filas = [r for c, r in fuente.items() if not comparable or c in suficientes]
                docs, uds, bruta, dcto, venta, neta = [sum(float(r[i]) for r in filas) for i in (5, 6, 7, 8, 10, 15)]
                esperados.append([venta, neta, uds, docs, uds/docs, venta/docs, venta/uds, dcto/bruta])
            for i, k in enumerate(KPIS):
                row = region.set_index("KPI").loc[k]
                self.assertAlmostEqual(row.Actual, esperados[0][i], places=6)
                self.assertAlmostEqual(row.Anterior, esperados[1][i], places=6)
                self.assertAlmostEqual(row["YoY %"], esperados[0][i]/esperados[1][i]-1, places=8)
            if comparable:
                self.assertEqual(set(tiendas.codigo), suficientes)
            else:
                self.assertEqual(len(tiendas[tiendas.codigo.isin(leidos[0].codigo)]), 17)
                bella = tiendas.set_index("codigo").loc["Z1GPP"]
                self.assertEqual(bella.Estado, "Base insuficiente")
                self.assertEqual(bella["# Doc anterior"], 2)
                self.assertTrue(math.isnan(bella["Venta YoY %"]))


class UITests(unittest.TestCase):
    @unittest.skipUnless(os.environ.get("YOY_ACTUAL") and os.environ.get("YOY_ANTERIOR"), "Requiere Excel real")
    def test_carga_reemplazo_invalido_y_retiro(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string('''
import os
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import streamlit as st
from regional_yoy import mostrar_year_to_year
from streamlit.delta_generator import DeltaGenerator
def archivo(self, etiqueta, **kwargs):
    actual = kwargs['key'] == 'yoy_archivo_actual'
    if actual and st.session_state.get('retirar'):
        return None
    if actual and st.session_state.get('invalido'):
        return BytesIO(b'no es Excel')
    return BytesIO(Path(os.environ['YOY_ACTUAL' if actual else 'YOY_ANTERIOR']).read_bytes())
with patch.object(DeltaGenerator, 'file_uploader', archivo):
    mostrar_year_to_year()
''', default_timeout=30).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.metric), 8)
        app.session_state['invalido'] = True
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.metric), 0)
        self.assertEqual(len(app.error), 1)
        app.session_state['invalido'] = False
        app.run()
        self.assertEqual(len(app.metric), 8)
        app.session_state['retirar'] = True
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.metric), 0)

    def test_tabs_y_error_persistencia_no_bloquea_yoy(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string('''
from unittest.mock import Mock
from regional_persistencia import ErrorRegional
from vista_regional import mostrar_vista_regional
mostrar_vista_regional(Mock(cargar=Mock(side_effect=ErrorRegional("Error de prueba"))))
''', default_timeout=30).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual([t.label for t in app.tabs], ["📊 Actual", "📈 Year to Year"])
        self.assertEqual(len(app.get("file_uploader")), 2)

    @unittest.skipUnless(os.environ.get("YOY_ACTUAL"), "Requiere Excel real")
    def test_rankings_y_lecturas(self):
        from streamlit.testing.v1 import AppTest
        app = AppTest.from_string('''
import os
from vista_regional import leer_reporte
from regional_yoy import mostrar_comparativo
mostrar_comparativo(leer_reporte(os.environ['YOY_ACTUAL'])[0], leer_reporte(os.environ['YOY_ANTERIOR'])[0])
''', default_timeout=30).run()
        self.assertEqual(len(app.exception), 0)
        self.assertEqual(len(app.metric), 8)
        app.selectbox(key="yoy_kpi").select("Venta Neta").run()
        tabla = app.dataframe[0].value.set_index("Código")
        self.assertEqual(len(tabla), 17)
        self.assertNotIn("Z1GEY", tabla.index)
        self.assertEqual(tabla.loc["Z1GPP", "Estado del KPI"], "Base insuficiente")
        self.assertGreater(tabla.loc["Z1GPP", "Venta Neta actual"], 0)
        self.assertGreater(tabla.loc["Z1GPP", "Venta Neta anterior"], 0)
        for codigo in ("Z1GKP", "Z1H4Q", "Z1IQF"):
            self.assertEqual(tabla.loc[codigo, "Estado del KPI"], "Nueva / sin base comparable")
            self.assertTrue(math.isnan(tabla.loc[codigo, "Venta Neta YoY %"]))
        self.assertTrue(math.isnan(tabla.loc["Z1GPP", "Venta Neta YoY %"]))
        self.assertTrue(any("13 tiendas con YoY calculable de 17 actuales" in c.value for c in app.caption))
        self.assertTrue(any("Z1GEY" in c.value and "Sin dato actual" in c.value for c in app.caption))
        total_neta = app.metric[1].value
        app.radio(key="yoy_lectura").set_value("Tiendas comparables").run()
        self.assertEqual(len(app.dataframe[0].value), 13)
        self.assertNotEqual(app.metric[1].value, total_neta)
        self.assertNotIn("Z1GPP", set(app.dataframe[0].value["Código"]))
        for kpi in KPIS:
            app.selectbox(key="yoy_kpi").select(kpi).run()
            self.assertEqual(len(app.exception), 0, kpi)
            self.assertEqual(len(app.get("vega_lite_chart")), 1)
        app.radio(key="yoy_lectura").set_value("Región total").run()
        self.assertEqual(len(app.dataframe[0].value), 17)
        self.assertEqual(app.metric[1].value, total_neta)


if __name__ == "__main__":
    unittest.main()
