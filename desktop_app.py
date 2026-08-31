from datetime import datetime
import os
import sys
import json
import tkinter as tk
from tkinter import messagebox, ttk
import customtkinter as ctk
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

# --- ARCHIVO LOCAL PARA GUARDAR ESCANEOS PENDIENTES DE SINCRONIZAR ---
ARCHIVO_PENDIENTES = "pendientes_offline.json"

def cargar_pendientes():
    if os.path.exists(ARCHIVO_PENDIENTES):
        try:
            with open(ARCHIVO_PENDIENTES, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

def guardar_pendientes(lista):
    with open(ARCHIVO_PENDIENTES, "w", encoding="utf-8") as f:
        json.dump(lista, f, ensure_ascii=False, indent=2)

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
  try:
    respuesta = supabase.table("catalogo_erp").select("codigo_limpio, referencia, descripcion, talla").execute()
    if respuesta.data:
      df = pd.DataFrame(respuesta.data)
      df = df.rename(columns={
          "codigo_limpio": "CodigoLimpio",
          "referencia": "Referencia",
          "descripcion": "Descripcion",
          "talla": "Talla"
      })
      df["CodigoLimpio"] = df["CodigoLimpio"].apply(limpiar_codigo)
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
    return 0

def mover_ubicacion_completa(origen, destino):
  origen = origen.upper().strip()
  destino = destino.upper().strip()
  
  if not origen or not destino:
    return 0, "Define la ubicación de origen y destino."
  
  if origen == destino:
    return 0, "El origen y el destino no pueden ser iguales."

  try:
    # 1. Borramos todo lo que existía previamente en el DESTINO
    supabase.table("ubicaciones").delete().eq("ubicacion", destino).execute()

    # 2. Consultar todos los registros origen
    res = supabase.table("ubicaciones").select("codigo_limpio, cantidad").eq("ubicacion", origen).execute()
    
    if not res.data or len(res.data) == 0:
      return 0, f"No se encontraron productos en la ubicación origen '{origen}'."
    
    movidos = 0
    fecha_actual = datetime.now().strftime("%Y-%m-%d %H:%M")

    for item in res.data:
      codigo = item["codigo_limpio"]
      cantidad_a_mover = item["cantidad"]

      # 3. Insertar el producto en el destino
      supabase.table("ubicaciones").insert({
          "codigo_limpio": codigo,
          "ubicacion": destino,
          "cantidad": cantidad_a_mover,
          "fecha": fecha_actual
      }).execute()

      # 4. Eliminar del origen
      supabase.table("ubicaciones").delete().eq("codigo_limpio", codigo).eq("ubicacion", origen).execute()
      movidos += 1

    return movidos, f"¡Éxito! Ubicación sobrescrita. Se pasaron {movidos} registros de '{origen}' a '{destino}'."
  except Exception as e:
    return 0, f"Error en Supabase al sobrescribir ubicación: {str(e)}"

def vaciar_ubicacion_completa(ubicacion):
    ubicacion = ubicacion.upper().strip()
    if not ubicacion:
        return False, "Debes definir una ubicación para vaciar."
    
    try:
        res = supabase.table("ubicaciones").select("cantidad").eq("ubicacion", ubicacion).execute()
        if not res.data or len(res.data) == 0:
            return False, f"La ubicación '{ubicacion}' ya está vacía o no existe."
            
        supabase.table("ubicaciones").delete().eq("ubicacion", ubicacion).execute()
        return True, f"¡Éxito! La ubicación '{ubicacion}' ha sido vaciada por completo."
    except Exception as e:
        return False, f"Error al intentar vaciar en Supabase: {str(e)}"

# --- INTERFAZ GRÁFICA MODERNA (CustomTkinter) ---
class SistemaBodegaApp:
  def __init__(self, root):
    self.root = root
    self.root.title("⚡ Sinapsis - Inventario y Bodega")
    self.root.geometry("1050x780")
    
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")

    self.df_inv = cargar_inventario()

    self.modo_offline = False
    self.cola_pendiente = cargar_pendientes()

    self.configurar_estilo_tabla()
    self.setup_barra_offline()

    self.notebook = ctk.CTkTabview(
        self.root, 
        fg_color="#0E1117", 
        segmented_button_selected_color="#00D9F5", 
        segmented_button_selected_hover_color="#00A0B5"
    )
    self.notebook.pack(fill="both", expand=True, padx=10, pady=10)

    self.tab_escaneo_name = " 📦 Escaneo por Estante "
    self.tab_traspaso_name = " 🔄 Traspaso / Reubicación "
    self.tab_masivo_name = " 🚚 Movimientos Masivos "
    
    self.notebook.add(self.tab_escaneo_name)
    self.notebook.add(self.tab_traspaso_name)
    self.notebook.add(self.tab_masivo_name)

    self.tab_escaneo = self.notebook.tab(self.tab_escaneo_name)
    self.tab_traspaso = self.notebook.tab(self.tab_traspaso_name)
    self.tab_masivo = self.notebook.tab(self.tab_masivo_name)

    self.setup_tab_escaneo()
    self.setup_tab_traspaso()
    self.setup_tab_masivo()
    self.actualizar_barra_offline()

  def configurar_estilo_tabla(self):
    style = ttk.Style()
    style.theme_use("default")
    style.configure(
        "Treeview",
        background="#262730",
        foreground="white",
        rowheight=35,
        fieldbackground="#262730",
        borderwidth=0,
        font=("Arial", 10)
    )
    style.map('Treeview', background=[('selected', '#00D9F5')], foreground=[('selected', 'black')])
    style.configure(
        "Treeview.Heading",
        background="#1E293B",
        foreground="white",
        relief="flat",
        font=("Arial", 11, "bold"),
        padding=5
    )
    style.map("Treeview.Heading", background=[('active', '#334155')])

  def setup_barra_offline(self):
    frame_offline = ctk.CTkFrame(self.root, fg_color="#1E293B", corner_radius=0, height=50)
    frame_offline.pack(fill="x", side="top")

    self.lbl_estado_offline = ctk.CTkLabel(
        frame_offline, text="🟢 En línea", font=("Arial", 14, "bold"), text_color="#39FF88"
    )
    self.lbl_estado_offline.pack(side="left", padx=20, pady=10)

    self.btn_toggle_offline = ctk.CTkButton(
        frame_offline, text="🔌 Activar Modo Offline", font=("Arial", 12, "bold"),
        fg_color="#334155", hover_color="#475569", command=self.alternar_modo_offline
    )
    self.btn_toggle_offline.pack(side="left", padx=10)

    self.btn_sincronizar = ctk.CTkButton(
        frame_offline, text="☁️ Sincronizar (0)", font=("Arial", 12, "bold"),
        fg_color="#00D9F5", text_color="black", hover_color="#00A0B5", command=self.sincronizar_pendientes
    )
    self.btn_sincronizar.pack(side="left", padx=10)

  def alternar_modo_offline(self):
    self.modo_offline = not self.modo_offline
    self.actualizar_barra_offline()

  def actualizar_barra_offline(self):
    if self.modo_offline:
      self.lbl_estado_offline.configure(text="🔴 Modo Offline activo", text_color="#EF4444")
      self.btn_toggle_offline.configure(text="🔌 Desactivar Modo Offline")
    else:
      self.lbl_estado_offline.configure(text="🟢 En línea", text_color="#39FF88")
      self.btn_toggle_offline.configure(text="🔌 Activar Modo Offline")

    n = len(self.cola_pendiente)
    self.btn_sincronizar.configure(text=f"☁️ Sincronizar ({n})")

  def encolar_pendiente(self, accion, **datos):
    evento = {"accion": accion, "fecha": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), **datos}
    self.cola_pendiente.append(evento)
    guardar_pendientes(self.cola_pendiente)
    self.actualizar_barra_offline()

  def sincronizar_pendientes(self):
    if not self.cola_pendiente:
      messagebox.showinfo("Sincronizar", "No hay escaneos pendientes por sincronizar.")
      return
    total = len(self.cola_pendiente)
    exitosos = 0
    restantes = []
    for evento in self.cola_pendiente:
      try:
        if evento["accion"] == "guardar":
          guardar_ubicacion(evento["codigo"], evento["ubicacion"], evento.get("cantidad", 1))
        elif evento["accion"] == "restar":
          restar_ubicacion(evento["codigo"], evento["ubicacion"], evento.get("cantidad", 1))
        exitosos += 1
      except Exception:
        restantes.append(evento)
    self.cola_pendiente = restantes
    guardar_pendientes(self.cola_pendiente)
    self.actualizar_barra_offline()
    if exitosos == total:
      messagebox.showinfo("Sincronizar", f"✅ Se sincronizaron los {exitosos} escaneos pendientes con éxito.")
    else:
      messagebox.showwarning("Sincronizar", f"Se sincronizaron {exitosos} de {total}. Los restantes siguen en la cola.")

  # --- PESTAÑA ESCANEO ---
  def setup_tab_escaneo(self):
    self.ubicacion_actual = ""
    self.contador_estante = 0
    self.historial_escaneos = []

    frame_ub = ctk.CTkFrame(self.tab_escaneo, fg_color="#262730", corner_radius=10)
    frame_ub.pack(fill="x", pady=10, padx=10)
    
    frame_cols = ctk.CTkFrame(frame_ub, fg_color="transparent")
    frame_cols.pack(fill="x", padx=15, pady=10)
    
    f_izq = ctk.CTkFrame(frame_cols, fg_color="transparent")
    f_izq.pack(side="left", fill="x", expand=True, padx=(0, 5))
    ctk.CTkLabel(f_izq, text="1. Ubicación (Ej. PV o C3A)", font=("Arial", 14, "bold"), text_color="#00D9F5").pack(anchor="w")
    self.var_ubicacion = ctk.StringVar()
    self.var_ubicacion.trace_add("write", self.al_cambiar_ubicacion_texto)
    self.entry_ubicacion = ctk.CTkEntry(
        f_izq, textvariable=self.var_ubicacion, font=("Arial", 18, "bold"),
        fg_color="#0E1117", border_color="#00D9F5", text_color="white", height=45
    )
    self.entry_ubicacion.pack(fill="x", pady=(5, 0))

    f_der = ctk.CTkFrame(frame_cols, fg_color="transparent")
    f_der.pack(side="right", fill="x", expand=True, padx=(5, 0))
    ctk.CTkLabel(f_der, text="Sección / Sub-ubicación (Opcional)", font=("Arial", 14, "bold"), text_color="#F59E0B").pack(anchor="w")
    self.var_sububicacion = ctk.StringVar()
    self.var_sububicacion.trace_add("write", self.al_cambiar_ubicacion_texto)
    self.entry_sububicacion = ctk.CTkEntry(
        f_der, textvariable=self.var_sububicacion, font=("Arial", 18, "bold"), placeholder_text="Ej. Football, Originals...",
        fg_color="#0E1117", border_color="#F59E0B", text_color="white", height=45
    )
    self.entry_sububicacion.pack(fill="x", pady=(5, 0))

    frame_scan = ctk.CTkFrame(self.tab_escaneo, fg_color="#262730", corner_radius=10)
    frame_scan.pack(fill="x", pady=5, padx=10)
    ctk.CTkLabel(frame_scan, text="2. Disparo de Pistola", font=("Arial", 14, "bold"), text_color="#39FF88").pack(anchor="w", padx=15, pady=(10, 0))
    self.entry_scanner = ctk.CTkEntry(
        frame_scan, font=("Arial", 22, "bold"), fg_color="#0E1117", 
        border_color="#39FF88", text_color="white", height=55
    )
    self.entry_scanner.pack(fill="x", padx=15, pady=(5, 15))
    self.entry_scanner.bind("<Return>", self.procesar_disparo_escaneo)
    self.root.bind("<Control-z>", lambda event: self.deshacer_ultimo_escaneo())

    frame_acciones = ctk.CTkFrame(self.tab_escaneo, fg_color="transparent")
    frame_acciones.pack(fill="x", pady=10, padx=10)

    btn_undo = ctk.CTkButton(
        frame_acciones, text="↩️ Deshacer Último (Ctrl+Z)", font=("Arial", 12, "bold"),
        fg_color="#F59E0B", hover_color="#D97706", text_color="white", command=self.deshacer_ultimo_escaneo
    )
    btn_undo.pack(side="left", padx=5)

    btn_del_sel = ctk.CTkButton(
        frame_acciones, text="🗑️ Restar Seleccionada", font=("Arial", 12, "bold"),
        fg_color="#EF4444", hover_color="#B91C1C", text_color="white", command=self.restar_seleccionado
    )
    btn_del_sel.pack(side="left", padx=5)

    self.lbl_status = ctk.CTkLabel(
        self.tab_escaneo, text="Listo para escanear.", font=("Arial", 14, "bold"), text_color="#94A3B8"
    )
    self.lbl_status.pack(fill="x", pady=5)
    
    self.lbl_contador = ctk.CTkLabel(
        self.tab_escaneo, text="Piezas contadas en este estante: 0", font=("Arial", 14, "bold"), text_color="#00D9F5"
    )
    self.lbl_contador.pack(fill="x")

    self.tree_escaneo = ttk.Treeview(
        self.tab_escaneo, columns=("codigo", "desc", "talla", "ub", "cant"), show="headings", height=10
    )
    for col, head, w in [("codigo", "Código", 140), ("desc", "Descripción", 360), ("talla", "Talla", 80), ("ub", "Ubicación", 130), ("cant", "Piezas", 100)]:
      self.tree_escaneo.heading(col, text=head)
      self.tree_escaneo.column(col, width=w)
    self.tree_escaneo.pack(fill="both", expand=True, pady=10, padx=10)

  def obtener_ubicacion_completa(self):
      ubi = self.var_ubicacion.get().strip().upper()
      sub = self.var_sububicacion.get().strip().upper()
      if ubi and sub:
          return f"{ubi} - {sub}"
      return ubi

  def al_cambiar_ubicacion_texto(self, *args):
    nueva_ub = self.obtener_ubicacion_completa()
    if nueva_ub != self.ubicacion_actual:
      self.ubicacion_actual = nueva_ub
      self.contador_estante = 0
      self.lbl_contador.configure(text=f"Piezas contadas en {nueva_ub or 'estante'}: 0")

  def procesar_disparo_escaneo(self, event):
    codigo_raw = self.entry_scanner.get()
    self.entry_scanner.delete(0, tk.END)
    codigo = limpiar_codigo(codigo_raw)
    ubicacion = self.obtener_ubicacion_completa()

    if not ubicacion or not codigo:
      return

    self.contador_estante += 1
    self.historial_escaneos.append((codigo, ubicacion))

    desc, talla = "Sin catálogo", "-"
    if not self.df_inv.empty:
      match = self.df_inv[self.df_inv["CodigoLimpio"] == codigo]
      if not match.empty:
        desc, talla = str(match.iloc[0]["Descripcion"]), str(match.iloc[0]["Talla"])

    if self.modo_offline:
      self.encolar_pendiente("guardar", codigo=codigo, ubicacion=ubicacion, cantidad=1)
      texto_cantidad = "Pendiente"
      self.lbl_status.configure(text=f"🔌 REGISTRADO (offline): {codigo} ({talla}) en '{ubicacion}'", text_color="#F59E0B")
    else:
      cant = guardar_ubicacion(codigo, ubicacion)
      texto_cantidad = f"{cant} pza(s)"
      self.lbl_status.configure(text=f"✅ REGISTRADO: {codigo} ({talla}) en '{ubicacion}'", text_color="#39FF88")

    self.lbl_contador.configure(text=f"Piezas contadas en {ubicacion}: {self.contador_estante}")
    self.tree_escaneo.insert("", 0, values=(codigo, desc, talla, ubicacion, texto_cantidad))

  def deshacer_ultimo_escaneo(self):
    if not self.historial_escaneos:
      self.lbl_status.configure(text="⚠️ No hay escaneos recientes para deshacer.", text_color="#EF4444")
      return

    codigo, ubicacion = self.historial_escaneos.pop()
    if self.contador_estante > 0 and ubicacion == self.ubicacion_actual:
      self.contador_estante -= 1

    if self.modo_offline:
      self.encolar_pendiente("restar", codigo=codigo, ubicacion=ubicacion, cantidad=1)
      self.lbl_status.configure(text=f"🔌 DESHECHO (offline): Código {codigo} en '{ubicacion}'", text_color="#F59E0B")
    else:
      nueva_cant = restar_ubicacion(codigo, ubicacion)
      self.lbl_status.configure(text=f"↩️ DESHECHO: Código {codigo} en '{ubicacion}' (Quedan: {nueva_cant} pzas)", text_color="#F59E0B")

    self.lbl_contador.configure(text=f"Piezas contadas en {self.ubicacion_actual or 'estante'}: {self.contador_estante}")
    items = self.tree_escaneo.get_children()
    if items:
      self.tree_escaneo.delete(items[0])

  def restar_seleccionado(self):
    selected_item = self.tree_escaneo.selection()
    if not selected_item:
      messagebox.showwarning("Atención", "Selecciona una fila de la tabla para restar una pieza.")
      return

    item_vals = self.tree_escaneo.item(selected_item[0], "values")
    codigo = item_vals[0]
    ubicacion = item_vals[3]

    if self.contador_estante > 0 and ubicacion == self.ubicacion_actual:
      self.contador_estante -= 1

    if self.modo_offline:
      self.encolar_pendiente("restar", codigo=codigo, ubicacion=ubicacion, cantidad=1)
      self.lbl_status.configure(text=f"🔌 Se restó 1 pieza de {codigo} en '{ubicacion}' (offline)", text_color="#F59E0B")
      self.lbl_contador.configure(text=f"Piezas contadas en {self.ubicacion_actual or 'estante'}: {self.contador_estante}")
      self.tree_escaneo.item(selected_item[0], values=(item_vals[0], item_vals[1], item_vals[2], item_vals[3], "Resta pendiente"))
      return

    nueva_cant = restar_ubicacion(codigo, ubicacion)
    self.lbl_status.configure(text=f"🗑️ Se restó 1 pieza de {codigo} en '{ubicacion}' (Quedan: {nueva_cant} pzas)", text_color="#EF4444")
    self.lbl_contador.configure(text=f"Piezas contadas en {self.ubicacion_actual or 'estante'}: {self.contador_estante}")

    if nueva_cant > 0:
      self.tree_escaneo.item(selected_item[0], values=(item_vals[0], item_vals[1], item_vals[2], item_vals[3], f"{nueva_cant} pza(s)"))
    else:
      self.tree_escaneo.delete(selected_item[0])

  # --- PESTAÑA TRASPASO ---
  def setup_tab_traspaso(self):
    frame_orig = ctk.CTkFrame(self.tab_traspaso, fg_color="#262730", corner_radius=10)
    frame_orig.pack(fill="x", pady=10, padx=10)
    ctk.CTkLabel(frame_orig, text="Ubicación ORIGEN (De donde SALE)", font=("Arial", 14, "bold"), text_color="#EF4444").pack(anchor="w", padx=15, pady=(10, 0))
    
    f_orig_cols = ctk.CTkFrame(frame_orig, fg_color="transparent")
    f_orig_cols.pack(fill="x", padx=15, pady=5)
    
    self.entry_orig = ctk.CTkEntry(f_orig_cols, font=("Arial", 16, "bold"), placeholder_text="Ubicación (Ej. PV)", fg_color="#0E1117", border_color="#EF4444", text_color="white", height=40)
    self.entry_orig.pack(side="left", fill="x", expand=True, padx=(0, 5))
    self.entry_orig.insert(0, "PV")
    
    self.entry_orig_sub = ctk.CTkEntry(f_orig_cols, font=("Arial", 16, "bold"), placeholder_text="Sub-ubicación (Ej. FOOTBALL)", fg_color="#0E1117", border_color="#F59E0B", text_color="white", height=40)
    self.entry_orig_sub.pack(side="right", fill="x", expand=True, padx=(5, 0))

    frame_dest = ctk.CTkFrame(self.tab_traspaso, fg_color="#262730", corner_radius=10)
    frame_dest.pack(fill="x", pady=10, padx=10)
    ctk.CTkLabel(frame_dest, text="Ubicación DESTINO (A donde ENTRA)", font=("Arial", 14, "bold"), text_color="#39FF88").pack(anchor="w", padx=15, pady=(10, 0))
    
    f_dest_cols = ctk.CTkFrame(frame_dest, fg_color="transparent")
    f_dest_cols.pack(fill="x", padx=15, pady=5)
    
    self.entry_dest = ctk.CTkEntry(f_dest_cols, font=("Arial", 16, "bold"), placeholder_text="Ubicación (Ej. PV o C3A)", fg_color="#0E1117", border_color="#39FF88", text_color="white", height=40)
    self.entry_dest.pack(side="left", fill="x", expand=True, padx=(0, 5))
    
    self.entry_dest_sub = ctk.CTkEntry(f_dest_cols, font=("Arial", 16, "bold"), placeholder_text="Sub-ubicación (Opcional)", fg_color="#0E1117", border_color="#F59E0B", text_color="white", height=40)
    self.entry_dest_sub.pack(side="right", fill="x", expand=True, padx=(5, 0))

    frame_scan_t = ctk.CTkFrame(self.tab_traspaso, fg_color="#262730", corner_radius=10)
    frame_scan_t.pack(fill="x", pady=10, padx=10)
    ctk.CTkLabel(frame_scan_t, text="Disparar producto a mover", font=("Arial", 14, "bold"), text_color="#00D9F5").pack(anchor="w", padx=15, pady=(10, 0))
    self.entry_scan_traspaso = ctk.CTkEntry(frame_scan_t, font=("Arial", 22, "bold"), fg_color="#0E1117", border_color="#00D9F5", text_color="white", height=55)
    self.entry_scan_traspaso.pack(fill="x", padx=15, pady=(5, 15))
    self.entry_scan_traspaso.bind("<Return>", self.procesar_disparo_traspaso)

    self.lbl_status_t = ctk.CTkLabel(self.tab_traspaso, text="Escribe Origen y Destino, luego escanea.", font=("Arial", 14, "bold"), text_color="#94A3B8")
    self.lbl_status_t.pack(fill="x", pady=5)

    self.tree_traspaso = ttk.Treeview(self.tab_traspaso, columns=("codigo", "desc", "orig", "dest"), show="headings", height=10)
    for col, head, w in [("codigo", "Código", 150), ("desc", "Descripción", 400), ("orig", "Origen", 140), ("dest", "Destino", 140)]:
      self.tree_traspaso.heading(col, text=head)
      self.tree_traspaso.column(col, width=w)
    self.tree_traspaso.pack(fill="both", expand=True, pady=10, padx=10)

  def procesar_disparo_traspaso(self, event):
    codigo = limpiar_codigo(self.entry_scan_traspaso.get())
    self.entry_scan_traspaso.delete(0, tk.END)
    
    orig_main = self.entry_orig.get().strip().upper()
    orig_sub = self.entry_orig_sub.get().strip().upper()
    origen = f"{orig_main} - {orig_sub}" if orig_main and orig_sub else orig_main
    
    dest_main = self.entry_dest.get().strip().upper()
    dest_sub = self.entry_dest_sub.get().strip().upper()
    destino = f"{dest_main} - {dest_sub}" if dest_main and dest_sub else dest_main

    if not origen or not destino or not codigo:
      self.lbl_status_t.configure(text="⚠️ Define Origen y Destino correctamente.", text_color="#EF4444")
      return

    desc = "Sin catálogo"
    if not self.df_inv.empty:
      match = self.df_inv[self.df_inv["CodigoLimpio"] == codigo]
      if not match.empty:
        desc = str(match.iloc[0]["Descripcion"])

    if self.modo_offline:
      self.encolar_pendiente("restar", codigo=codigo, ubicacion=origen, cantidad=1)
      self.encolar_pendiente("guardar", codigo=codigo, ubicacion=destino, cantidad=1)
      self.lbl_status_t.configure(text=f"🔌 MOVIDO (offline): {codigo} de {origen} ➡️ {destino}", text_color="#F59E0B")
    else:
      cant_orig = restar_ubicacion(codigo, origen, cantidad=1)
      cant_dest = guardar_ubicacion(codigo, destino, cantidad=1)
      self.lbl_status_t.configure(text=f"🔄 MOVIDO: {codigo} de {origen} ({cant_orig} queda) ➡️ {destino} ({cant_dest} total)", text_color="#00D9F5")

    self.tree_traspaso.insert("", 0, values=(codigo, desc, origen, destino))

  # --- PESTAÑA: MOVIMIENTOS MASIVOS (SOBRESCRITURA Y VACIADO) ---
  def setup_tab_masivo(self):
    # SECCIÓN 1: MOVER Y SOBRESCRIBIR
    frame_instruccion = ctk.CTkFrame(self.tab_masivo, fg_color="#262730", corner_radius=10)
    frame_instruccion.pack(fill="x", pady=(10, 5), padx=10)
    ctk.CTkLabel(
        frame_instruccion, 
        text="⚠️ Sobrescribir: Mueve TODO el inventario de un origen, borrando lo que había en el destino.", 
        font=("Arial", 12, "bold"), text_color="#F59E0B"
    ).pack(padx=15, pady=10)

    frame_inputs = ctk.CTkFrame(self.tab_masivo, fg_color="transparent")
    frame_inputs.pack(fill="x", pady=5, padx=10)

    # Origen Masivo (Ubicación + Sub-ubicación)
    frame_mo = ctk.CTkFrame(frame_inputs, fg_color="#262730", corner_radius=10)
    frame_mo.pack(side="left", fill="both", expand=True, padx=(0, 5))
    ctk.CTkLabel(frame_mo, text="Ubicación ORIGEN (A vaciar)", font=("Arial", 13, "bold"), text_color="#EF4444").pack(anchor="w", padx=15, pady=(10, 0))
    
    f_mo_cols = ctk.CTkFrame(frame_mo, fg_color="transparent")
    f_mo_cols.pack(fill="x", padx=15, pady=5)
    self.entry_masivo_orig = ctk.CTkEntry(f_mo_cols, font=("Arial", 14, "bold"), placeholder_text="Ubicación (Ej. PV)", fg_color="#0E1117", border_color="#EF4444", text_color="white", height=40)
    self.entry_masivo_orig.pack(side="left", fill="x", expand=True, padx=(0, 5))
    self.entry_masivo_orig_sub = ctk.CTkEntry(f_mo_cols, font=("Arial", 14, "bold"), placeholder_text="Sub-ubicación (Ej. FOOTBALL)", fg_color="#0E1117", border_color="#F59E0B", text_color="white", height=40)
    self.entry_masivo_orig_sub.pack(side="right", fill="x", expand=True, padx=(5, 0))

    # Destino Masivo (Ubicación + Sub-ubicación)
    frame_md = ctk.CTkFrame(frame_inputs, fg_color="#262730", corner_radius=10)
    frame_md.pack(side="right", fill="both", expand=True, padx=(5, 0))
    ctk.CTkLabel(frame_md, text="Ubicación DESTINO (A sobrescribir)", font=("Arial", 13, "bold"), text_color="#39FF88").pack(anchor="w", padx=15, pady=(10, 0))
    
    f_md_cols = ctk.CTkFrame(frame_md, fg_color="transparent")
    f_md_cols.pack(fill="x", padx=15, pady=5)
    self.entry_masivo_dest = ctk.CTkEntry(f_md_cols, font=("Arial", 14, "bold"), placeholder_text="Ubicación (Ej. PV)", fg_color="#0E1117", border_color="#39FF88", text_color="white", height=40)
    self.entry_masivo_dest.pack(side="left", fill="x", expand=True, padx=(0, 5))
    self.entry_masivo_dest_sub = ctk.CTkEntry(f_md_cols, font=("Arial", 14, "bold"), placeholder_text="Sub-ubicación (Opcional)", fg_color="#0E1117", border_color="#F59E0B", text_color="white", height=40)
    self.entry_masivo_dest_sub.pack(side="right", fill="x", expand=True, padx=(5, 0))

    self.btn_ejecutar_masivo = ctk.CTkButton(
        self.tab_masivo, text="🚀 SOBRESCRIBIR Y MOVER UBICACIÓN COMPLETA", font=("Arial", 14, "bold"),
        fg_color="#00D9F5", text_color="black", hover_color="#00A0B5", height=45,
        command=self.ejecutar_movimiento_masivo
    )
    self.btn_ejecutar_masivo.pack(fill="x", pady=10, padx=10)

    # SEPARADOR VISUAL
    separador = ctk.CTkFrame(self.tab_masivo, height=2, fg_color="#334155")
    separador.pack(fill="x", pady=15, padx=20)

    # SECCIÓN 2: VACIAR ESTANTE COMPLETO (Con Ubicación + Sub-ubicación también)
    frame_vaciar = ctk.CTkFrame(self.tab_masivo, fg_color="#262730", corner_radius=10, border_width=1, border_color="#EF4444")
    frame_vaciar.pack(fill="x", pady=5, padx=10)
    
    ctk.CTkLabel(
        frame_vaciar, 
        text="🗑️ Vaciar Ubicación Completa (Borrar todo el inventario de un estante/sección)", 
        font=("Arial", 13, "bold"), text_color="#EF4444"
    ).pack(padx=15, pady=(10, 0), anchor="w")

    frame_vaciar_input = ctk.CTkFrame(frame_vaciar, fg_color="transparent")
    frame_vaciar_input.pack(fill="x", padx=15, pady=10)

    self.entry_vaciar_ubi = ctk.CTkEntry(
        frame_vaciar_input, font=("Arial", 14, "bold"), placeholder_text="Ubicación (Ej. PV)",
        fg_color="#0E1117", border_color="#EF4444", text_color="white", height=42
    )
    self.entry_vaciar_ubi.pack(side="left", fill="x", expand=True, padx=(0, 5))

    self.entry_vaciar_ubi_sub = ctk.CTkEntry(
        frame_vaciar_input, font=("Arial", 14, "bold"), placeholder_text="Sub-ubicación (Ej. FOOTBALL)",
        fg_color="#0E1117", border_color="#F59E0B", text_color="white", height=42
    )
    self.entry_vaciar_ubi_sub.pack(side="left", fill="x", expand=True, padx=(5, 10))

    self.btn_vaciar = ctk.CTkButton(
        frame_vaciar_input, text="⚠️ VACIAR AHORA", font=("Arial", 14, "bold"),
        fg_color="#EF4444", text_color="white", hover_color="#B91C1C", height=42, width=160,
        command=self.ejecutar_vaciado
    )
    self.btn_vaciar.pack(side="right")

    self.lbl_status_masivo = ctk.CTkLabel(
        self.tab_masivo, text="Selecciona una acción masiva con cuidado.", font=("Arial", 13, "bold"), text_color="#94A3B8"
    )
    self.lbl_status_masivo.pack(fill="x", pady=10)

  def ejecutar_movimiento_masivo(self):
    orig_main = self.entry_masivo_orig.get().strip().upper()
    orig_sub = self.entry_masivo_orig_sub.get().strip().upper()
    origen = f"{orig_main} - {orig_sub}" if orig_main and orig_sub else orig_main

    dest_main = self.entry_masivo_dest.get().strip().upper()
    dest_sub = self.entry_masivo_dest_sub.get().strip().upper()
    destino = f"{dest_main} - {dest_sub}" if dest_main and dest_sub else dest_main

    if not origen or not destino:
      messagebox.showwarning("Atención", "Debes especificar la ubicación de Origen y la de Destino.")
      return

    confirmar = messagebox.askyesno(
        "Confirmar Traspaso Masivo",
        f"¿Estás completamente seguro de mover TODO el inventario de '{origen}' hacia '{destino}'?\n\n⚠️ ADVERTENCIA: Esto BORRARÁ lo que había previamente en '{destino}' y dejará únicamente lo del origen."
    )

    if not confirmar:
      return

    self.lbl_status_masivo.configure(text="⏳ Procesando sobrescritura masiva en Supabase...", text_color="#F59E0B")
    self.root.update()

    movidos, mensaje = mover_ubicacion_completa(origen, destino)

    if movidos > 0:
      self.lbl_status_masivo.configure(text=f"✅ {mensaje}", text_color="#39FF88")
      messagebox.showinfo("Éxito", mensaje)
      self.entry_masivo_orig.delete(0, tk.END)
      self.entry_masivo_orig_sub.delete(0, tk.END)
      self.entry_masivo_dest.delete(0, tk.END)
      self.entry_masivo_dest_sub.delete(0, tk.END)
    else:
      self.lbl_status_masivo.configure(text=f"❌ {mensaje}", text_color="#EF4444")
      messagebox.showerror("Aviso", mensaje)

  def ejecutar_vaciado(self):
      vac_main = self.entry_vaciar_ubi.get().strip().upper()
      vac_sub = self.entry_vaciar_ubi_sub.get().strip().upper()
      ubicacion = f"{vac_main} - {vac_sub}" if vac_main and vac_sub else vac_main

      if not ubicacion:
          messagebox.showwarning("Atención", "Escribe el nombre de la ubicación que deseas vaciar.")
          return
          
      confirmar = messagebox.askyesno(
          "⚠️ PELIGRO: Confirmar Vaciado",
          f"Estás a punto de BORRAR todo el inventario registrado en la ubicación:\n\n'{ubicacion}'\n\n¿Estás seguro de que deseas vaciarla por completo?"
      )
      
      if not confirmar:
          return
          
      self.lbl_status_masivo.configure(text=f"⏳ Vaciando la ubicación '{ubicacion}' en Supabase...", text_color="#F59E0B")
      self.root.update()
      
      exito, mensaje = vaciar_ubicacion_completa(ubicacion)
      
      if exito:
          self.lbl_status_masivo.configure(text=f"✅ {mensaje}", text_color="#39FF88")
          messagebox.showinfo("Vaciado Exitoso", mensaje)
          self.entry_vaciar_ubi.delete(0, tk.END)
          self.entry_vaciar_ubi_sub.delete(0, tk.END)
      else:
          self.lbl_status_masivo.configure(text=f"❌ {mensaje}", text_color="#EF4444")
          messagebox.showerror("Error", mensaje)

if __name__ == "__main__":
  root = ctk.CTk()
  app = SistemaBodegaApp(root)
  root.mainloop()