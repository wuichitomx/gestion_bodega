"""Persistencia de jornadas: cliente de servidor, control de versiones y auditoría."""
import base64
import copy
import hashlib
import json
from datetime import date, datetime
from uuid import uuid4


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
    campos = ('fecha', 'vouchers', 'cortes', 'ultimo_corte', 'corte_x', 'cierre_datos', 'texto_z', 'texto_x')
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
    def __init__(self, cliente, usuario):
        if not usuario or not usuario.strip():
            raise ErrorPersistenciaCaja('Inicia sesión para recuperar tu caja.')
        self.cliente = cliente
        self.usuario = usuario.strip()

    def cargar(self, fecha):
        try:
            respuesta = self.cliente.table('cajas_jornadas').select('datos,version,estado').eq(
                'usuario', self.usuario).eq('fecha', str(fecha)).limit(1).execute()
            return restaurar_estado(respuesta.data[0]) if respuesta.data else None
        except Exception:
            raise ErrorPersistenciaCaja('No se pudo recuperar la caja de Supabase. Revisa la conexión y la instalación de las tablas.') from None

    def guardar(self, estado, accion, cerrar=False):
        try:
            respuesta = self.cliente.rpc('cajas_guardar_jornada', {
                'p_usuario': self.usuario, 'p_fecha': estado['fecha'],
                'p_version': estado.get('_version', 0), 'p_datos': serializar_estado(estado),
                'p_accion': accion, 'p_operacion': str(uuid4()), 'p_cerrar': cerrar,
            }).execute()
            return restaurar_estado(respuesta.data)
        except Exception as error:
            if any(codigo in str(error) for codigo in ('CAJA_CONFLICTO', 'CAJA_CERRADA')):
                raise ConflictoCaja('La caja cambió en otra sesión o ya está cerrada. Recarga los datos guardados antes de continuar.') from None
            raise ErrorPersistenciaCaja('No se pudo confirmar el guardado en Supabase. No repitas la captura: recarga los datos guardados para comprobar si llegó.') from None
