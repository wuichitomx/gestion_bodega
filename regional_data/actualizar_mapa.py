"""Actualización manual del recurso público. No se ejecuta desde la aplicación."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import json
from urllib.request import urlopen

BASE = "https://gaia.inegi.org.mx/wscatgeo/v2/geo/mgee/"
METADATOS = {
    "Fuente_informacion_vectorial": "INEGI. Marco Geoestadístico, diciembre de 2025",
    "Fuente_informacion_estadistica": "INEGI. Censo de Población y Vivienda, 2020",
    "origen": BASE + "{cve_ent}",
    "transformacion": "Extracción geométrica y simplificación local 0.01 grados; redondeo 5 decimales; orientación para D3/Vega. No elaborada por INEGI.",
}


def orientar(geo):
    """D3 usa exteriores horarios e interiores antihorarios (convención esférica)."""
    for f in geo["features"]:
        for poligono in f["geometry"]["coordinates"]:
            for i, anillo in enumerate(poligono):
                area = sum(a[0]*b[1]-b[0]*a[1] for a, b in zip(anillo, anillo[1:]))
                if (i == 0 and area > 0) or (i > 0 and area < 0):
                    anillo.reverse()
    geo["metadatos"] = METADATOS
    return geo


def simplificar(puntos, tolerancia=.01):
    if len(puntos) < 3:
        return puntos
    a, b = puntos[0], puntos[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    def distancia(p):
        t = max(0, min(1, ((p[0]-a[0])*dx+(p[1]-a[1])*dy)/(dx*dx+dy*dy))) if dx or dy else 0
        return ((p[0]-a[0]-t*dx)**2+(p[1]-a[1]-t*dy)**2)**.5
    i, d = max(((i, distancia(p)) for i, p in enumerate(puntos[1:-1], 1)), key=lambda x: x[1])
    return simplificar(puntos[:i+1], tolerancia)[:-1] + simplificar(puntos[i:], tolerancia) if d > tolerancia else [a, b]


def descargar(n):
    with urlopen(BASE + f"{n:02}", timeout=60) as r:
        fuente = json.load(r)
    f = fuente["features"][0]
    p = f["properties"]
    nombre = "Estado de México" if n == 15 else "Michoacán" if n == 16 else p["nomgeo"]
    g = f["geometry"]
    poligonos = g["coordinates"] if g["type"] == "MultiPolygon" else [g["coordinates"]]
    salida = []
    for poligono in poligonos:
        anillos = []
        for anillo in poligono:
            reducido = simplificar(anillo)
            if len(reducido) < 4:
                reducido = anillo
            anillos.append([[round(x, 5), round(y, 5)] for x, y, *resto in reducido])
        salida.append(anillos)
    return {"type": "Feature", "properties": {"cve_ent": p["cve_ent"], "estado": nombre},
            "geometry": {"type": "MultiPolygon", "coordinates": salida}}


if __name__ == "__main__":
    with ThreadPoolExecutor(max_workers=4) as pool:
        features = list(pool.map(descargar, range(1, 33)))
    destino = Path(__file__).with_name("mexico_estados.geojson")
    destino.write_text(json.dumps(orientar({"type": "FeatureCollection", "features": features}), ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(features)} entidades; {destino.stat().st_size} bytes")
