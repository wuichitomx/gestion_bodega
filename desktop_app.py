from datetime import datetime
import os
import sys
import tkinter as tk
from tkinter import messagebox, ttk
import pandas as pd
from supabase import create_client
from dotenv import load_dotenv

# --- CONFIGURACIÓN DE SUPABASE ---
load_dotenv()

URL = os.getenv("SUPABASE_URL")
KEY = os.getenv("SUPABASE_KEY")

if not URL or not KEY:
    messagebox.showerror(
        "Falta configuración",
        "No se encontraron SUPABASE_URL y/o SUPABASE_KEY.\n\n"
        "Revisa que exista un archivo .env en esta misma carpeta con:\n"
        "SUPABASE_URL=tu_url\nSUPABASE_KEY=tu_llave"
    )
    sys.exit(1)

supabase = create_client(URL, KEY)

# --- FUNCIONES DE BASE DE DATOS (SUPABASE) ---
def limpiar_codigo(val):
  if pd.isna(val):
    return ""
  s = str(val).strip()
  if s.endswith(".0"):
    s = s[:-2]
  return s

def extraer_talla(referencia):
  if pd.isna(referencia):
    return "N/A"
  ref_str = str(referencia).strip()
  if "-" in ref_str:
    partes = ref_str.split("-", 1)
    return partes[1].strip()
  return "N/A"

def cargar_inventario():
  """Carga el catálogo de productos directamente desde la tabla 'catalogo_erp' en Supabase."""
  try:
    # Hacemos una consulta paginada o general a Supabase para traernos todo el catálogo
    respuesta = supabase.table("catalogo_erp").select("codigo_limpio, referencia, descripcion, talla").execute()
    
    if respuesta.data:
      df = pd.DataFrame(respuesta.data)
      # Normalizamos los nombres de columnas para que encajen perfectamente con la interfaz
      df = df.rename(columns={
          "codigo_limpio": "CodigoLimpio",
          "referencia": "Referencia",
          "descripcion": "Descripcion",
          "talla": "Talla"
      })
      df["CodigoLimpio"] = df["CodigoLimpio"].apply(limpiar_codigo)
      # Si la talla viene vacía en Supabase, la recalculamos de la referencia por seguridad
      if "Talla" not in df.columns or df["Talla"].isna().all():
        df["Talla"] = df["Referencia"].apply(extraer_talla)
      else:
        df["Talla"] = df["Talla"].fillna("N/A")
        
      return df
  except Exception as e:
    print(f"⚠️ Error al conectar con Supabase para el catálogo: {e}")
  
  return pd.DataFrame()

def guardar_ubicacion(codigo, nueva_ubicacion, cantidad=1):
  codigo_limpio = limpiar_codigo(codigo)
  nueva_ubicacion = nueva_ubicacion.upper().strip()
  
  if not codigo_limpio or not nueva_ubicacion:
    return 0

  fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M")
  
  try:
    res = supabase.table("ubicaciones").select("cantidad").eq("codigo_limpio", codigo_limpio).eq("ubicacion", nueva_ubicacion).execute()
    
    if res.data and len(res.data) > 0:
      cant_actual = res.data[0]["cantidad"] + cantidad
      supabase.table("ubicaciones").update({
          "cantidad": cant_actual,
          "fecha": fecha_actual
      }).eq("codigo_limpio", codigo_limpio).eq("ubicacion", nueva_ubicacion).execute()
    else:
      cant_actual = cantidad
      supabase.table("ubicaciones").insert({
          "codigo_limpio": codigo_limpio,
          "ubicacion": nueva_ubicacion,
          "cantidad": cant_actual,
          "fecha": fecha_actual
      }).execute()
      
    return cant_actual
  except Exception as e:
    messagebox.showerror("Error en Supabase", f"No se pudo guardar.\nDetalle técnico:\n\n{str(e)}")
    print(f"❌ Error crítico al guardar en Supabase: {e}")
    return 0

def restar_ubicacion(codigo, ubicacion, cantidad=1):
  codigo_limpio = limpiar_codigo(codigo)
  ubicacion = ubicacion.upper().strip()
  
  if not codigo_limpio or not ubicacion:
    return 0

  try:
    res = supabase.table("ubicaciones").select("cantidad").eq("codigo_limpio", codigo_limpio).eq("ubicacion", ubicacion).execute()
    
    nueva_cant = 0
    if res.data and len(res.data) > 0:
      cant_actual = res.data[0]["cantidad"]
      if cant_actual > cantidad:
        nueva_cant = cant_actual - cantidad
        fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M")
        supabase.table("ubicaciones").update({
            "cantidad": nueva_cant,
            "fecha": fecha_actual
        }).eq("codigo_limpio", codigo_limpio).eq("ubicacion", ubicacion).execute()
      else:
        supabase.table("ubicaciones").delete().eq("codigo_limpio", codigo_limpio).eq("ubicacion", ubicacion).execute()
    return nueva_cant
  except Exception as e:
    messagebox.showerror("Error en Supabase", f"No se pudo restar.\nDetalle técnico:\n\n{str(e)}")
    print(f"❌ Error crítico al restar en Supabase: {e}")
    return 0

# --- INTERFAZ GRÁFICA ---
class SistemaBodegaApp:
  def __init__(self, root):
    self.root = root
    self.root.title("Sistema de Inventario y Bodega (Nube)")
    self.root.geometry("1000x750")
    self.df_inv = cargar_inventario()

    self.notebook = ttk.Notebook(self.root)
    self.notebook.pack(fill="both", expand=True)

    self.tab_escaneo = tk.Frame(self.notebook, padx=10, pady=10)
    self.tab_traspaso = tk.Frame(self.notebook, padx=10, pady=10)

    self.notebook.add(self.tab_escaneo, text=" 📦 Escaneo por Estante ")
    self.notebook.add(self.tab_traspaso, text=" 🔄 Traspaso / Reubicación ")

    self.setup_tab_escaneo()
    self.setup_tab_traspaso()

  # --- PESTAÑA ESCANEO ---
  def setup_tab_escaneo(self):
    self.ubicacion_actual = ""
    self.contador_estante = 0
    self.historial_escaneos = []

    frame_ub = tk.LabelFrame(
        self.tab_escaneo,
        text=" 1. Ubicación Actual ",
        font=("Arial", 11, "bold"),
    )
    frame_ub.pack(fill="x", pady=5)
    self.var_ubicacion = tk.StringVar()
    self.var_ubicacion.trace_add("write", self.al_cambiar_ubicacion_texto)
    tk.Entry(
        frame_ub,
        textvariable=self.var_ubicacion,
        font=("Arial", 15, "bold"),
        bg="#E0F2FE",
    ).pack(fill="x", padx=10, pady=5)

    frame_scan = tk.LabelFrame(
        self.tab_escaneo,
        text=" 2. Disparo de Pistola ",
        font=("Arial", 11, "bold"),
    )
    frame_scan.pack(fill="x", pady=5)
    self.entry_scanner = tk.Entry(
        frame_scan, font=("Arial", 18, "bold"), bg="#FEF08A"
    )
    self.entry_scanner.pack(fill="x", padx=10, pady=5)
    self.entry_scanner.bind("<Return>", self.procesar_disparo_escaneo)
    self.root.bind("<Control-z>", lambda event: self.deshacer_ultimo_escaneo())

    frame_acciones = tk.Frame(self.tab_escaneo)
    frame_acciones.pack(fill="x", pady=5)

    btn_undo = tk.Button(
        frame_acciones,
        text="↩️ Deshacer Último Escaneo (Ctrl+Z)",
        font=("Arial", 10, "bold"),
        bg="#F59E0B",
        fg="white",
        padx=10,
        pady=5,
        command=self.deshacer_ultimo_escaneo,
    )
    btn_undo.pack(side="left", padx=5)

    btn_del_sel = tk.Button(
        frame_acciones,
        text="🗑️ Restar Pieza Seleccionada de la Tabla",
        font=("Arial", 10, "bold"),
        bg="#EF4444",
        fg="white",
        padx=10,
        pady=5,
        command=self.restar_seleccionado,
    )
    btn_del_sel.pack(side="left", padx=5)

    self.lbl_status = tk.Label(
        self.tab_escaneo,
        text="Listo para escanear.",
        font=("Arial", 12, "bold"),
        fg="#1E3A8A",
    )
    self.lbl_status.pack(fill="x", pady=2)
    self.lbl_contador = tk.Label(
        self.tab_escaneo,
        text="Piezas contadas en este estante: 0",
        font=("Arial", 11, "bold"),
        fg="#0369A1",
    )
    self.lbl_contador.pack(fill="x")

    self.tree_escaneo = ttk.Treeview(
        self.tab_escaneo,
        columns=("codigo", "desc", "talla", "ub", "cant"),
        show="headings",
        height=10,
    )
    for col, head, w in [
        ("codigo", "Código", 140),
        ("desc", "Descripción", 360),
        ("talla", "Talla", 80),
        ("ub", "Ubicación", 100),
        ("cant", "Piezas", 100),
    ]:
      self.tree_escaneo.heading(col, text=head)
      self.tree_escaneo.column(col, width=w)
    self.tree_escaneo.pack(fill="both", expand=True, pady=5)

  def al_cambiar_ubicacion_texto(self, *args):
    nueva_ub = self.var_ubicacion.get().strip().upper()
    if nueva_ub != self.ubicacion_actual:
      self.ubicacion_actual = nueva_ub
      self.contador_estante = 0
      self.lbl_contador.config(
          text=f"Piezas contadas en {nueva_ub or 'estante'}: 0"
      )

  def procesar_disparo_escaneo(self, event):
    codigo_raw = self.entry_scanner.get()
    self.entry_scanner.delete(0, tk.END)
    codigo = limpiar_codigo(codigo_raw)
    ubicacion = self.var_ubicacion.get().strip().upper()

    if not ubicacion or not codigo:
      return

    cant = guardar_ubicacion(codigo, ubicacion)
    self.contador_estante += 1
    self.historial_escaneos.append((codigo, ubicacion))

    desc, talla = "Sin catálogo", "-"
    if not self.df_inv.empty:
      match = self.df_inv[self.df_inv["CodigoLimpio"] == codigo]
      if not match.empty:
        desc, talla = str(match.iloc[0]["Descripcion"]), str(
            match.iloc[0]["Talla"]
        )

    self.lbl_status.config(
        text=f"✅ REGISTRADO: {codigo} ({talla}) en '{ubicacion}'", fg="green"
    )
    self.lbl_contador.config(
        text=f"Piezas contadas en {ubicacion}: {self.contador_estante}"
    )
    self.tree_escaneo.insert(
        "", 0, values=(codigo, desc, talla, ubicacion, f"{cant} pza(s)")
    )

  def deshacer_ultimo_escaneo(self):
    if not self.historial_escaneos:
      self.lbl_status.config(
          text="⚠️ No hay escaneos recientes para deshacer.", fg="red"
      )
      return

    codigo, ubicacion = self.historial_escaneos.pop()
    nueva_cant = restar_ubicacion(codigo, ubicacion)

    if self.contador_estante > 0 and ubicacion == self.ubicacion_actual:
      self.contador_estante -= 1

    self.lbl_status.config(
        text=(
            f"↩️ DESHECHO: Código {codigo} en '{ubicacion}' (Quedan:"
            f" {nueva_cant} pzas)"
        ),
        fg="#D97706",
    )
    self.lbl_contador.config(
        text=(
            f"Piezas contadas en {self.ubicacion_actual or 'estante'}:"
            f" {self.contador_estante}"
        )
    )

    items = self.tree_escaneo.get_children()
    if items:
      self.tree_escaneo.delete(items[0])

  def restar_seleccionado(self):
    selected_item = self.tree_escaneo.selection()
    if not selected_item:
      messagebox.showwarning(
          "Atención", "Selecciona una fila de la tabla para restar una pieza."
      )
      return

    item_vals = self.tree_escaneo.item(selected_item[0], "values")
    codigo = item_vals[0]
    ubicacion = item_vals[3]

    nueva_cant = restar_ubicacion(codigo, ubicacion)

    if self.contador_estante > 0 and ubicacion == self.ubicacion_actual:
      self.contador_estante -= 1

    self.lbl_status.config(
        text=(
            f"🗑️ Se restó 1 pieza de {codigo} en '{ubicacion}' (Quedan:"
            f" {nueva_cant} pzas)"
        ),
        fg="red",
    )
    self.lbl_contador.config(
        text=(
            f"Piezas contadas en {self.ubicacion_actual or 'estante'}:"
            f" {self.contador_estante}"
        )
    )

    if nueva_cant > 0:
      self.tree_escaneo.item(
          selected_item[0],
          values=(
              item_vals[0],
              item_vals[1],
              item_vals[2],
              item_vals[3],
              f"{nueva_cant} pza(s)",
          ),
      )
    else:
      self.tree_escaneo.delete(selected_item[0])

  # --- PESTAÑA TRASPASO ---
  def setup_tab_traspaso(self):
    frame_orig = tk.LabelFrame(
        self.tab_traspaso,
        text=" Ubicación ORIGEN (De donde SALE) ",
        font=("Arial", 11, "bold"),
    )
    frame_orig.pack(fill="x", pady=5)
    self.entry_orig = tk.Entry(
        frame_orig, font=("Arial", 14, "bold"), bg="#FEE2E2"
    )
    self.entry_orig.insert(0, "PISO")
    self.entry_orig.pack(fill="x", padx=10, pady=5)

    frame_dest = tk.LabelFrame(
        self.tab_traspaso,
        text=" Ubicación DESTINO (A donde ENTRA) ",
        font=("Arial", 11, "bold"),
    )
    frame_dest.pack(fill="x", pady=5)
    self.entry_dest = tk.Entry(
        frame_dest, font=("Arial", 14, "bold"), bg="#DCFCE7"
    )
    self.entry_dest.pack(fill="x", padx=10, pady=5)

    frame_scan_t = tk.LabelFrame(
        self.tab_traspaso,
        text=" Disparar producto a mover ",
        font=("Arial", 11, "bold"),
    )
    frame_scan_t.pack(fill="x", pady=5)
    self.entry_scan_traspaso = tk.Entry(
        frame_scan_t, font=("Arial", 18, "bold"), bg="#FEF08A"
    )
    self.entry_scan_traspaso.pack(fill="x", padx=10, pady=5)
    self.entry_scan_traspaso.bind(
        "<Return>", self.procesar_disparo_traspaso
    )

    self.lbl_status_t = tk.Label(
        self.tab_traspaso,
        text="Escribe Origen y Destino, luego escanea.",
        font=("Arial", 12, "bold"),
        fg="#1E3A8A",
    )
    self.lbl_status_t.pack(fill="x", pady=5)

    self.tree_traspaso = ttk.Treeview(
        self.tab_traspaso,
        columns=("codigo", "desc", "orig", "dest"),
        show="headings",
        height=10,
    )
    for col, head, w in [
        ("codigo", "Código", 150),
        ("desc", "Descripción", 400),
        ("orig", "Origen", 120),
        ("dest", "Destino", 120),
    ]:
      self.tree_traspaso.heading(col, text=head)
      self.tree_traspaso.column(col, width=w)
    self.tree_traspaso.pack(fill="both", expand=True, pady=5)

  def procesar_disparo_traspaso(self, event):
    codigo = limpiar_codigo(self.entry_scan_traspaso.get())
    self.entry_scan_traspaso.delete(0, tk.END)
    origen = self.entry_orig.get().strip().upper()
    destino = self.entry_dest.get().strip().upper()

    if not origen or not destino or not codigo:
      self.lbl_status_t.config(
          text="⚠️ Define Origen y Destino correctamente.", fg="red"
      )
      return

    cant_orig = restar_ubicacion(codigo, origen, cantidad=1)
    cant_dest = guardar_ubicacion(codigo, destino, cantidad=1)

    desc = "Sin catálogo"
    if not self.df_inv.empty:
      match = self.df_inv[self.df_inv["CodigoLimpio"] == codigo]
      if not match.empty:
        desc = str(match.iloc[0]["Descripcion"])

    self.lbl_status_t.config(
        text=(
            f"🔄 MOVIDO: {codigo} de {origen} ({cant_orig} queda) ➡️ {destino}"
            f" ({cant_dest} total)"
        ),
        fg="blue",
    )
    self.tree_traspaso.insert("", 0, values=(codigo, desc, origen, destino))

if __name__ == "__main__":
  root = tk.Tk()
  app = SistemaBodegaApp(root)
  root.mainloop()