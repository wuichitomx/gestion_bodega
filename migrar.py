import sqlite3
import pandas as pd

# Conectarse a la base de datos local vieja
conn = sqlite3.connect("bodega_inventario.db")

# Extraer todo lo de la tabla ubicaciones
df = pd.read_sql("SELECT codigo_limpio, ubicacion, cantidad, fecha FROM ubicaciones", conn)
conn.close()

# Guardarlo en un archivo CSV
df.to_csv("datos_viejos.csv", index=False)
print("¡Listo! Se creó el archivo 'datos_viejos.csv' con todos tus escaneos anteriores.")