"""Reporte vigente mediante RPC privadas del servidor Streamlit, sin caché de sesión."""
from datetime import date
import json
from uuid import uuid4

import pandas as pd

from vista_regional import ADITIVOS, reconstruir_reporte


class ErrorRegional(Exception):
    pass


def serializar_reporte(df, control):
    campos = ["codigo", "nombre", *ADITIVOS, "Mt2"]
    base = df[campos].copy()
    base["Mt2"] = df["Mt2 origen"]
    registros = base.astype(object).where(pd.notna(base), None).to_dict("records")
    totales = []
    if not control.empty:
        totales = [{r["Campo"]: r["TOTALES"] if pd.notna(r["TOTALES"]) else None
                    for r in control.to_dict("records")}]
    reconstruir_reporte(registros, totales)
    return json.loads(json.dumps({"detalle": registros, "totales": totales}, allow_nan=False))


def restaurar_reporte(datos):
    fecha = date.fromisoformat(datos["fecha_reporte"])
    df, control, avisos, _ = reconstruir_reporte(datos["detalle"], datos["totales"])
    return {"id": datos["id"], "fecha": fecha, "df": df, "control": control, "avisos": avisos}


class RepositorioRegional:
    def __init__(self, cliente, actor):
        if not actor or not actor.strip():
            raise ErrorRegional("Inicia sesión para consultar la Vista Regional.")
        self.cliente = cliente
        self.actor = actor.strip()

    def cargar(self):
        try:
            datos = self.cliente.rpc("regional_obtener_vigente", {"p_actor": self.actor}).execute().data
            return restaurar_reporte(datos) if datos is not None else None
        except Exception:
            raise ErrorRegional("No se pudo consultar el reporte regional. Revisa la conexión, los permisos y la migración 004.") from None

    def guardar(self, fecha, df, control):
        if type(fecha) is not date:
            raise ErrorRegional("Selecciona la Fecha del reporte.")
        try:
            datos = serializar_reporte(df, control)
        except (ValueError, KeyError, TypeError):
            raise ErrorRegional("El reporte no contiene datos regionales válidos.") from None
        try:
            self.cliente.rpc("regional_publicar_reporte", {
                "p_actor": self.actor, "p_id": str(uuid4()), "p_fecha": fecha.isoformat(),
                "p_detalle": datos["detalle"], "p_totales": datos["totales"],
            }).execute()
        except Exception:
            raise ErrorRegional("No se pudo confirmar el guardado. Recarga para consultar el vigente antes de reintentar.") from None
