"""Persistencia de jornadas: cliente de servidor, control de versiones y auditoría."""
import base64
import copy
import hashlib
import json
from datetime import date, datetime
from uuid import uuid4
from permisos import ACCIONES, OPERATIVOS, permisos_usuario, tiene_permiso


class ErrorPersistenciaCaja(Exception):
    pass


class ConflictoCaja(ErrorPersistenciaCaja):
    pass


def clave_de_servidor(clave):
    if clave.startswith('sb_secret_'):
        return True
    try:
        parte = clave.split('.')[1]
        return json.loads(base64.urlsafe_b64decode(parte + '=' * (-len(parte) % 4)))['role'] == 'service_role'
    except (ValueError, KeyError, IndexError, TypeError):
        return False


def huella_movimientos(vouchers):
    contenido = [{k: v.get(k) for k in ('id', 'medio', 'importe', 'folio', 'hora')} for v in vouchers]
    return hashlib.sha256(json.dumps(contenido, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def serializar_estado(estado):
    campos = ('fecha', 'vouchers', 'cortes', 'ultimo_corte', 'corte_x', 'cierre_datos', 'texto_z', 'texto_x',
              'responsable_arqueo', 'responsable_revision', 'correo_preparado_por',
              'revision_documentacion', 'borrador_correo')
    return json.loads(json.dumps({k: estado[k] for k in campos if k in estado},
                               default=lambda v: v.isoformat() if isinstance(v, (date, datetime)) else None,
                               allow_nan=False))


def restaurar_estado(registro):
    estado = copy.deepcopy(registro['datos'])
    for nombre in ('ultimo_corte', 'corte_x'):
        if isinstance(estado.get(nombre), dict) and estado[nombre].get('fecha'):
            estado[nombre]['fecha'] = date.fromisoformat(estado[nombre]['fecha'])
    for voucher in estado.get('vouchers', []):
        voucher.setdefault('id', uuid4().hex)
    estado['_version'] = registro['version']
    estado['_cerrada'] = registro['estado'] == 'cerrada'
    return estado


class RepositorioCajas:
    def __init__(self, cliente, usuario, usuario_info=None):
        if not usuario or not usuario.strip():
            raise ErrorPersistenciaCaja('Inicia sesión para recuperar tu caja.')
        self.cliente = cliente
        self.usuario = usuario.strip()
        self.actor = self.usuario
        self.usuario_info = usuario_info or {'username': self.actor}

    def listar_jornadas(self, fecha):
        if not permisos_usuario(self.usuario_info) & (OPERATIVOS - {'realizar_arqueo'}):
            return [self.actor]
        try:
            filas = self.cliente.table('cajas_jornadas').select('usuario').eq(
                'fecha', str(fecha)).order('usuario').execute().data
            return sorted({r['usuario'] for r in filas} | {self.actor})
        except Exception:
            raise ErrorPersistenciaCaja('No se pudieron consultar las jornadas de esta fecha.') from None

    def comprobar_version(self, estado):
        actual = self.cargar(estado['fecha'])
        if (actual or {}).get('_version', 0) != estado.get('_version', 0):
            raise ConflictoCaja('La jornada cambió. Recarga antes de continuar.')

    def cargar(self, fecha):
        if self.usuario != self.actor and not permisos_usuario(self.usuario_info) & (OPERATIVOS - {'realizar_arqueo'}):
            raise ErrorPersistenciaCaja('No tienes permiso para consultar otra caja.')
        try:
            respuesta = self.cliente.table('cajas_jornadas').select('datos,version,estado').eq(
                'usuario', self.usuario).eq('fecha', str(fecha)).limit(1).execute()
            return restaurar_estado(respuesta.data[0]) if respuesta.data else None
        except Exception:
            raise ErrorPersistenciaCaja('No se pudo recuperar la caja de Supabase. Revisa la conexión y la instalación de las tablas.') from None

    def guardar(self, estado, accion, cerrar=False):
        if not tiene_permiso(self.usuario_info, ACCIONES.get(accion, '')):
            raise ErrorPersistenciaCaja('No tienes permiso para esta acción.')
        if ACCIONES.get(accion) == 'realizar_arqueo' and self.usuario != self.actor:
            raise ErrorPersistenciaCaja('Los movimientos sólo los captura el propietario de esta caja.')
        try:
            respuesta = self.cliente.rpc('cajas_guardar_jornada_v2', {
                'p_actor': self.actor,
                'p_usuario': self.usuario, 'p_fecha': estado['fecha'],
                'p_version': estado.get('_version', 0), 'p_datos': serializar_estado(estado),
                'p_accion': accion, 'p_operacion': str(uuid4()), 'p_cerrar': cerrar,
            }).execute()
            return restaurar_estado(respuesta.data)
        eexcept Exception as error:
    detalle = str(error)

    if any(codigo in detalle for codigo in ('CAJA_CONFLICTO', 'CAJA_CERRADA')):
        raise ConflictoCaja(
            'La caja cambió en otra sesión o ya está cerrada. '
            'Recarga los datos guardados antes de continuar.'
        ) from None

    codigos_seguros = (
        'CAJA_DATOS_INVALIDOS',
        'CAJA_USUARIO_NO_AUTORIZADO',
        'CAJA_ACCION_INVALIDA',
        'CAJA_JORNADA_INEXISTENTE',
        'CAJA_CIERRE_INCOMPLETO',
        'CAJA_PUESTO_REQUERIDO',
        'CAJA_REVISION_REQUERIDA',
    )

    codigo_detectado = next(
        (codigo for codigo in codigos_seguros if codigo in detalle),
        None,
    )

    if codigo_detectado:
        raise ErrorPersistenciaCaja(
            f'No se pudo guardar en Supabase. Código: {codigo_detectado}.'
        ) from None

    raise ErrorPersistenciaCaja(
        'No se pudo confirmar el guardado en Supabase. '
        'No repitas la captura: recarga los datos guardados para comprobar si llegó.'
    ) from None