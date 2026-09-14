"""Puestos descriptivos y autorizaciones independientes, compatibles con roles antiguos."""
from datetime import datetime, timezone

PUESTOS = ('Gerente', 'Subgerente', 'Cajero', 'Asesor')
PERMISOS = {
    'realizar_arqueo': 'Realizar arqueo de caja',
    'revisar_cierre': 'Revisar / finalizar cierre',
    'capturar_facturacion': 'Capturar facturación',
    'preparar_correo': 'Preparar correo',
    'administrar_usuarios': 'Administrar usuarios',
}
OPERATIVOS = set(PERMISOS) - {'administrar_usuarios'}
ACCIONES = {
    **dict.fromkeys(('importar_sesion', 'agregar_movimiento', 'eliminar_movimiento',
                     'analizar_z', 'guardar_arqueo'), 'realizar_arqueo'),
    **dict.fromkeys(('analizar_x', 'preparar_documentos', 'cerrar', 'revisar_documentacion'), 'revisar_cierre'),
    'facturacion': 'capturar_facturacion',
    'preparar_correo': 'preparar_correo',
}


def permisos_usuario(usuario):
    explicitos = usuario.get('permisos')
    if explicitos is not None:
        return set(explicitos) & set(PERMISOS) if isinstance(explicitos, list) else set()
    rol = str(usuario.get('rol') or '').strip().lower()
    # Misma inicialización que la migración 002, también para usuarios creados por V1.
    return set(PERMISOS) if rol == 'admin' else {'realizar_arqueo'} if rol == 'cajero' else set()


def tiene_permiso(usuario, permiso):
    return permiso in permisos_usuario(usuario)


def puesto_usuario(usuario):
    return str(usuario.get('puesto') or {
        'admin': 'Administrador', 'cajero': 'Cajero', 'asesor': 'Asesor',
    }.get(str(usuario.get('rol') or '').strip().lower(), '')).strip()


def identidad_usuario(usuario):
    return {'usuario': str(usuario.get('username') or '').strip(),
            'nombre': str(usuario.get('nombre_completo') or usuario.get('username') or '').strip(),
            'puesto': puesto_usuario(usuario),
            'registrado_en': datetime.now(timezone.utc).isoformat()}


def firma_revision(estado):
    revision = estado.get('responsable_revision') or {}
    if not all(revision.get(c) for c in ('usuario', 'nombre', 'puesto')):
        raise ValueError('Falta registrar al responsable de revisión con nombre y puesto.')
    return f"Saludos,\n{revision['nombre']}\n{revision['puesto']}"


def aplicar_responsabilidad(estado, usuario, accion):
    if not tiene_permiso(usuario, ACCIONES.get(accion, '')):
        raise ValueError('No tienes permiso para realizar esta acción.')
    if accion in ('importar_sesion', 'guardar_arqueo') and not estado.get('responsable_arqueo'):
        estado['responsable_arqueo'] = identidad_usuario(usuario)
    if accion in ('cerrar', 'revisar_documentacion'):
        estado['responsable_revision'] = identidad_usuario(usuario)
    elif accion == 'preparar_correo':
        firma_revision(estado)
        estado['correo_preparado_por'] = identidad_usuario(usuario)
    else:
        estado.pop('revision_documentacion', None)
