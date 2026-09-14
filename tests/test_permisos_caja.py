import copy
import sys
import unittest
from datetime import date
from email import policy
from email.parser import BytesParser
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from permisos import (PERMISOS, OPERATIVOS, permisos_usuario, identidad_usuario,
                      aplicar_responsabilidad, firma_revision)
from cajas_persistencia import (RepositorioCajas, ErrorPersistenciaCaja, ConflictoCaja,
                               serializar_estado, restaurar_estado)
from arqueo_caja import construir_correo_informacion, CORREO_PARA, CORREO_CC


def persona(username, puesto, permisos):
    return dict(username=username, nombre_completo=username.title(), puesto=puesto,
                rol='asesor', permisos=permisos)


class PermisosTests(unittest.TestCase):
    def test_legacy_admin(self):
        self.assertEqual(permisos_usuario({'rol': ' ADMIN '}), set(PERMISOS))

    def test_legacy_cajero(self):
        self.assertEqual(permisos_usuario({'rol': 'cajero'}), {'realizar_arqueo'})

    def test_cajero_sin_permisos_explicitos_no_hereda_revision_facturacion_o_correo(self):
        for valor in ({'rol': ' CAJERO '}, {'rol': 'cajero', 'permisos': None}):
            for accion in ('cerrar', 'revisar_documentacion', 'facturacion', 'preparar_correo'):
                with self.subTest(usuario=valor, accion=accion), self.assertRaises(ValueError):
                    aplicar_responsabilidad({}, valor, accion)

    def test_asesor_sin_asignacion_no_tiene_permisos(self):
        self.assertEqual(permisos_usuario({'rol': 'asesor', 'permisos': None}), set())

    def test_no_sobrescribe_permisos_explicitos_cajero(self):
        self.assertEqual(permisos_usuario({'rol': 'cajero', 'permisos': ['revisar_cierre']}),
                         {'revisar_cierre'})

    def test_nombre_no_otorga_admin(self):
        self.assertEqual(permisos_usuario({'username': 'admin_aux', 'rol': 'asesor'}), set())

    def test_puesto_no_otorga_permisos(self):
        self.assertEqual(permisos_usuario({'puesto': 'Gerente', 'rol': 'asesor'}), set())

    def test_lista_vacia_revoca_herencia(self):
        self.assertEqual(permisos_usuario({'rol': 'admin', 'permisos': []}), set())

    def test_permiso_desconocido_o_invalido(self):
        self.assertEqual(permisos_usuario({'rol': 'admin', 'permisos': 'administrar_usuarios'}), set())
        self.assertEqual(permisos_usuario({'permisos': ['inventado']}), set())

    def test_flujo_tres_personas(self):
        estado = {'fecha': '2026-09-13', 'vouchers': [], 'cortes': []}
        cajero = persona('ana', 'Cajero', ['realizar_arqueo'])
        revisor = persona('beatriz', 'Subgerente', ['revisar_cierre'])
        gerente = persona('carlos', 'Gerente', ['preparar_correo'])
        aplicar_responsabilidad(estado, cajero, 'guardar_arqueo')
        aplicar_responsabilidad(estado, revisor, 'cerrar')
        estado = restaurar_estado({'datos': serializar_estado(estado), 'version': 3, 'estado': 'cerrada'})
        aplicar_responsabilidad(estado, gerente, 'preparar_correo')
        self.assertEqual(estado['responsable_arqueo']['usuario'], 'ana')
        self.assertEqual(estado['responsable_revision']['usuario'], 'beatriz')
        self.assertEqual(estado['correo_preparado_por']['usuario'], 'carlos')
        self.assertEqual(firma_revision(estado), 'Saludos,\nBeatriz\nSubgerente')

    def test_cajero_limitado_no_revisa(self):
        with self.assertRaises(ValueError):
            aplicar_responsabilidad({}, persona('ana', 'Cajero', ['realizar_arqueo']), 'cerrar')

    def test_historico_sin_revision_no_inventa_firma(self):
        with self.assertRaises(ValueError):
            firma_revision({'cierre_datos': {'responsable': 'Cajero anterior'}})

    def test_facturacion_invalida_revision(self):
        estado = {'revision_documentacion': {'huella': 'a' * 64}}
        aplicar_responsabilidad(estado, persona('bea', 'Subgerente', ['capturar_facturacion']), 'facturacion')
        self.assertNotIn('revision_documentacion', estado)

    def test_responsable_arqueo_no_se_sobrescribe(self):
        estado = {'responsable_arqueo': {'usuario': 'original'}}
        aplicar_responsabilidad(estado, persona('otro', 'Cajero', ['realizar_arqueo']), 'guardar_arqueo')
        self.assertEqual(estado['responsable_arqueo']['usuario'], 'original')

    def test_correo_conserva_destinatarios_y_adjuntos(self):
        estado = {'responsable_revision': identidad_usuario(persona('beatriz', 'Subgerente', []))}
        mensaje = BytesParser(policy=policy.default).parsebytes(
            construir_correo_informacion(date(2026, 9, 13), [('venta.pdf', b'contenido')], estado))
        self.assertEqual(mensaje['To'], CORREO_PARA)
        self.assertEqual(mensaje['Cc'], CORREO_CC)
        self.assertEqual(mensaje['Subject'], 'Venta 13-09-2026')
        self.assertIn('Beatriz\r\nSubgerente', mensaje.get_body().get_content())
        self.assertEqual(next(mensaje.iter_attachments()).get_payload(decode=True), b'contenido')

    def test_historico_se_restaura_sin_campos_nuevos(self):
        original = {'fecha': '2026-09-13', 'vouchers': [], 'cortes': [], 'cierre_datos': {'responsable': 'Ana'}}
        estado = restaurar_estado({'datos': original, 'version': 2, 'estado': 'cerrada'})
        self.assertEqual(estado['cierre_datos']['responsable'], 'Ana')
        self.assertNotIn('responsable_revision', estado)
        self.assertNotIn('_version', original)

    def test_serializacion_no_incluye_archivos_ni_credenciales(self):
        estado = {'fecha': date(2026, 9, 13), 'documentos_cierre': {'corte': b'archivo'}, 'password': 'privada',
                  'responsable_revision': {'nombre': 'Beatriz'}, 'borrador_correo': {'id': 'draft'}}
        resultado = serializar_estado(estado)
        self.assertNotIn('password', resultado)
        self.assertNotIn('documentos_cierre', resultado)
        self.assertEqual(resultado['fecha'], '2026-09-13')
        self.assertEqual(resultado['borrador_correo']['id'], 'draft')


class RepositorioTests(unittest.TestCase):
    def setUp(self):
        self.cliente = Mock()
        self.revisor = persona('bea', 'Subgerente', ['revisar_cierre'])
        self.repo = RepositorioCajas(self.cliente, 'bea', self.revisor)

    def test_guarda_actor_distinto_de_propietario(self):
        self.repo.usuario = 'ana'
        estado = {'fecha': '2026-09-13', 'vouchers': [], 'cortes': [], '_version': 4}
        self.cliente.rpc.return_value.execute.return_value.data = {'datos': estado, 'version': 5, 'estado': 'cerrada'}
        self.repo.guardar(estado, 'cerrar', cerrar=True)
        nombre, parametros = self.cliente.rpc.call_args.args
        self.assertEqual(nombre, 'cajas_guardar_jornada_v2')
        self.assertEqual((parametros['p_actor'], parametros['p_usuario'], parametros['p_version']), ('bea', 'ana', 4))

    def test_sin_permiso_no_llama_rpc(self):
        with self.assertRaises(ErrorPersistenciaCaja):
            self.repo.guardar({'fecha': '2026-09-13'}, 'agregar_movimiento')
        self.cliente.rpc.assert_not_called()

    def test_cajero_legacy_no_recurre_a_v1_para_cerrar(self):
        repo = RepositorioCajas(self.cliente, 'ana', {'username': 'ana', 'rol': 'cajero'})
        with self.assertRaises(ErrorPersistenciaCaja):
            repo.guardar({'fecha': '2026-09-13'}, 'cerrar', cerrar=True)
        self.cliente.rpc.assert_not_called()

    def test_fallo_v2_no_recurre_a_v1_disponible(self):
        self.cliente.rpc.side_effect = Exception('V2 no disponible; V1 sigue habilitada')
        with self.assertRaises(ErrorPersistenciaCaja):
            self.repo.guardar({'fecha': '2026-09-13'}, 'cerrar', cerrar=True)
        self.assertEqual(self.cliente.rpc.call_count, 1)
        self.assertEqual(self.cliente.rpc.call_args.args[0], 'cajas_guardar_jornada_v2')

    def test_conflicto(self):
        self.cliente.rpc.side_effect = Exception('CAJA_CONFLICTO')
        with self.assertRaises(ConflictoCaja):
            self.repo.guardar({'fecha': '2026-09-13'}, 'cerrar')

    def test_lectura_otra_caja_denegada_cajero(self):
        repo = RepositorioCajas(self.cliente, 'ana', persona('ana', 'Cajero', ['realizar_arqueo']))
        repo.usuario = 'bea'
        with self.assertRaises(ErrorPersistenciaCaja):
            repo.cargar('2026-09-13')
        self.cliente.table.assert_not_called()

    def test_conflicto_antes_de_efecto_externo(self):
        self.repo.cargar = Mock(return_value={'_version': 7})
        with self.assertRaises(ConflictoCaja):
            self.repo.comprobar_version({'fecha': '2026-09-13', '_version': 6})


class InterfazTests(unittest.TestCase):
    def ejecutar(self, permisos, puesto='Asesor'):
        from streamlit.testing.v1 import AppTest
        script = '''import streamlit as st
from arqueo_caja import mostrar_arqueo_caja
mostrar_arqueo_caja()
'''
        app = AppTest.from_string(script)
        app.session_state['usuario_actual'] = 'prueba'
        app.session_state['usuario_info'] = persona('prueba', puesto, permisos)
        app.run(timeout=20)
        self.assertEqual(len(app.exception), 0)
        return app

    def test_cajero_puede_capturar(self):
        app = self.ejecutar(['realizar_arqueo'], 'Cajero')
        boton = next(b for b in app.button if b.label == 'Agregar movimiento')
        self.assertFalse(boton.disabled)

    def test_revisor_no_captura_sin_permiso(self):
        app = self.ejecutar(['revisar_cierre'], 'Subgerente')
        boton = next(b for b in app.button if b.label == 'Agregar movimiento')
        self.assertTrue(boton.disabled)

    def test_sin_permiso_no_muestra_captura(self):
        app = self.ejecutar([])
        self.assertTrue(app.error)
        self.assertFalse(any(b.label == 'Agregar movimiento' for b in app.button))


class CorreoFlujoTests(unittest.TestCase):
    class Sesion(dict):
        __getattr__ = dict.__getitem__
        __setattr__ = dict.__setitem__

    class Recarga(BaseException):
        pass

    def ejecutar(self, estado, operador, accion, contenido_erp=b'erp', id_borrador='draft-1', conflicto=False):
        import arqueo_caja as caja
        ui = Mock()
        ui.session_state = self.Sesion(usuario_info=operador, usuario_actual=operador['username'])
        def archivo(nombre, contenido):
            return SimpleNamespace(name=nombre, size=len(contenido), getvalue=lambda: contenido)
        ui.file_uploader.side_effect = [archivo('erp.pdf', contenido_erp),
                                       [archivo('terminal.pdf', b'terminal')], [archivo('factura.pdf', b'factura')]]
        ui.button.side_effect = lambda label, **kw: label == accion and not kw.get('disabled', False)
        ui.checkbox.return_value = False
        ui.rerun.side_effect = self.Recarga()
        repo = Mock(usuario='ana')
        if conflicto:
            repo.comprobar_version.side_effect = ConflictoCaja('La jornada cambió.')
        repo.guardar.side_effect = lambda estado, *a, **kw: copy.deepcopy(estado)
        gmail = Mock(return_value={'id': id_borrador})
        with patch.object(caja, 'st', ui), patch.object(caja, 'validar_documentos_correo', return_value=[]):
            try:
                caja._mostrar_correo_informacion(copy.deepcopy(estado), date(2026, 9, 13),
                    lambda _: {'contenido': b'corte'}, lambda _: {'contenido': b'estadillo'},
                    gmail, lambda: None, repo)
            except self.Recarga:
                pass
        return ui, repo, gmail

    def revisada(self):
        estado = {'fecha': '2026-09-13', '_cerrada': True, 'vouchers': [], 'cortes': [],
                  'responsable_arqueo': {'usuario': 'ana', 'nombre': 'Ana', 'puesto': 'Cajero'}}
        ui, repo, gmail = self.ejecutar(estado, persona('bea', 'Subgerente', ['revisar_cierre']),
                                       'Confirmar revisión de documentación y firma')
        gmail.assert_not_called()
        return ui.session_state.arqueo_caja

    def test_otro_usuario_prepara_y_persiste_sin_cambiar_firma(self):
        estado = self.revisada()
        ui, repo, gmail = self.ejecutar(estado, persona('carlos', 'Gerente', ['preparar_correo']),
                                       'Preparar correo de información')
        gmail.assert_called_once()
        mensaje = BytesParser(policy=policy.default).parsebytes(gmail.call_args.args[0])
        self.assertIn('Bea\r\nSubgerente', mensaje.get_body().get_content())
        self.assertNotIn('Carlos', mensaje.get_body().get_content())
        guardado = ui.session_state.arqueo_caja
        self.assertEqual(guardado['correo_preparado_por']['usuario'], 'carlos')
        self.assertEqual(guardado['responsable_revision']['usuario'], 'bea')
        self.assertEqual(guardado['borrador_correo']['id'], 'draft-1')

    def test_documento_cambiado_bloquea_borrador(self):
        _, repo, gmail = self.ejecutar(self.revisada(), persona('carlos', 'Gerente', ['preparar_correo']),
                                      'Preparar correo de información', contenido_erp=b'otra venta')
        gmail.assert_not_called()
        repo.guardar.assert_not_called()

    def test_revisor_sin_permiso_correo_no_crea_borrador(self):
        _, _, gmail = self.ejecutar(self.revisada(), persona('bea', 'Subgerente', ['revisar_cierre']),
                                   'Preparar correo de información')
        gmail.assert_not_called()

    def test_no_duplica_borrador_al_cambiar_sesion(self):
        ui, _, _ = self.ejecutar(self.revisada(), persona('carlos', 'Gerente', ['preparar_correo']),
                                'Preparar correo de información')
        _, _, gmail = self.ejecutar(ui.session_state.arqueo_caja, persona('daniel', 'Gerente', ['preparar_correo']),
                                   'Preparar correo de información')
        gmail.assert_not_called()

    def test_conflicto_impide_operacion_gmail(self):
        _, repo, gmail = self.ejecutar(self.revisada(), persona('carlos', 'Gerente', ['preparar_correo']),
                                      'Preparar correo de información', conflicto=True)
        gmail.assert_not_called()
        repo.guardar.assert_not_called()


if __name__ == '__main__':
    unittest.main()
