import streamlit as st
import pandas as pd
import os
from fpdf import FPDF
from datetime import datetime
import math
import io
import xlsxwriter
import copy

# =======================
# Helpers
# =======================
def _safe_float(val, default=0.0):
    try:
        if val is None:
            return default
        if isinstance(val, float) and (pd.isna(val)):
            return default
        if isinstance(val, str) and val.strip().lower() in ("", "nan", "none"):
            return default
        return float(val)
    except Exception:
        return default

def ceil_to_even(x: float) -> int:
    n = math.ceil(x)
    return n if n % 2 == 0 else n + 1

# =======================
# Paths & constants
# =======================
SCRIPT_DIR = os.path.dirname(__file__) if "__file__" in globals() else os.getcwd()

# --- Rutas para Cortinas apuntando a la nueva estructura ---
CORTINAS_DATA_PATH = os.path.join(SCRIPT_DIR, "data", "cortinas")
DESIGNS_XLSX_PATH = os.path.join(CORTINAS_DATA_PATH, "disenos.xlsx")
BOM_XLSX_PATH = os.path.join(CORTINAS_DATA_PATH, "bom.xlsx")
CATALOG_XLSX_PATH = os.path.join(CORTINAS_DATA_PATH, "catalogo_insumos.xlsx")
CATALOG_TELAS_XLSX_PATH = os.path.join(CORTINAS_DATA_PATH, "catalogo_telas.xlsx")

REQUIRED_DESIGNS_COLS = ["Diseño", "Tipo", "Multiplicador", "PVP M.O."]
REQUIRED_BOM_COLS     = ["Diseño", "Insumo", "Unidad", "ReglaCantidad", "Parametro", "DependeDeSeleccion", "Observaciones"]
REQUIRED_CAT_COLS     = ["Insumo", "Unidad", "Ref", "Color", "PVP"]
REQUIRED_TELAS_COLS   = ["TipoTela", "Referencia", "Color", "PVP/Metro ($)"]
ALLOWED_RULES = {"MT_ANCHO_X_MULT", "UND_OJALES_PAR", "UND_BOTON_PAR", "FIJO"}
IVA_PERCENT = 0.19
DISTANCIA_BOTON_DEF = 0.20
DISTANCIA_OJALES_DEF = 0.14

# =======================
# Función para Imagen
# =======================
def get_image_path(tela_num):
    """
    Construye la ruta a la imagen de la cortina, considerando el Diseño y la Tela.
    Si no encuentra una imagen específica, devuelve la ruta a un placeholder.

    Cambios realizados:
    - Se limpian (normalizan) diseño, tipo de tela, referencia y color para que
      coincidan con las carpetas/archivos.
    - Se intenta con extensiones .jpg y .png.
    - Se usa el tipo de tela LIMPIO en la ruta de carpeta.
    """
    diseno = st.session_state.get("diseno_sel")
    tipo_tela = st.session_state.get(f"tipo_tela_sel_{tela_num}")
    ref = st.session_state.get(f"ref_tela_sel_{tela_num}")
    color = st.session_state.get(f"color_tela_sel_{tela_num}")

    placeholder = os.path.join(SCRIPT_DIR, "imagenes", "placeholder.png")

    # Si falta alguna selección, devuelve el placeholder
    if not all([diseno, tipo_tela, ref, color]):
        return placeholder

    # Limpiar nombres
    diseno_cleaned = str(diseno).strip().replace(" ", "_").upper()
    tipo_tela_cleaned = str(tipo_tela).strip().replace(" ", "_")
    ref_cleaned = str(ref).strip().replace(" ", "_").replace(".", "")
    color_cleaned = str(color).strip().replace(" ", "_")

    # Nombre base del archivo
    base_name = f"{tipo_tela_cleaned} - {ref_cleaned} - {color_cleaned}"

    # Rutas candidatas (intenta .jpg y .png)
    candidates = [
        os.path.join(SCRIPT_DIR, "imagenes", "cortinas", diseno_cleaned, tipo_tela_cleaned, base_name + ".jpg"),
        os.path.join(SCRIPT_DIR, "imagenes", "cortinas", diseno_cleaned, tipo_tela_cleaned, base_name + ".png"),
    ]

    for p in candidates:
        if os.path.exists(p):
            return p

    return placeholder

# =======================
# Loading
# =======================
@st.cache_data(show_spinner="Cargando datos de diseños...")
def load_designs_from_excel(path: str):
    if not os.path.exists(path):
        st.error(f"No se encontró el archivo Excel de Diseños en: {path}")
        st.stop()
    df = pd.read_excel(path, engine="openpyxl")
    
    faltantes = [c for c in REQUIRED_DESIGNS_COLS if c not in df.columns]
    if faltantes:
        st.error(f"El Excel de Diseños debe tener columnas: {REQUIRED_DESIGNS_COLS}. Encontradas: {list(df.columns)}")
        st.stop()

    tabla_disenos = {}
    tipos_cortina = {}
    precios_mo = {}
    disenos_a_tipos = {}

    for _, row in df.iterrows():
        dis = str(row["Diseño"]).strip()
        tipos = [t.strip() for t in str(row["Tipo"]).split(",") if str(t).strip()]
        mult = _safe_float(row["Multiplicador"], 1.0)
        mo_val = _safe_float(row["PVP M.O."], 0.0)

        # --- VERSIÓN CORREGIDA DEL ERROR DE SINTAXIS ---
        tabla_disenos[dis] = mult
        precios_mo[f"M.O: {dis}"] = {"unidad": "MT", "pvp": mo_val}
        disenos_a_tipos.setdefault(dis, [])
        # --- FIN DE LA CORRECIÓN ---

        for t in tipos:
            tipos_cortina.setdefault(t, [])
            if dis not in tipos_cortina[t]:
                tipos_cortina[t].append(dis)
            if t not in disenos_a_tipos[dis]:
                disenos_a_tipos[dis].append(t)

    return tabla_disenos, tipos_cortina, precios_mo, disenos_a_tipos, df

@st.cache_data(show_spinner="Cargando BOM...")
def load_bom_from_excel(path: str):
    if not os.path.exists(path):
        st.error(f"No se encontró el archivo Excel de BOM en: {path}")
        st.stop()
    df = pd.read_excel(path, engine="openpyxl")
    
    faltantes = [c for c in REQUIRED_BOM_COLS if c not in df.columns]
    if faltantes:
        st.error(f"El Excel de BOM debe tener columnas: {REQUIRED_BOM_COLS}. Encontradas: {list(df.columns)}")
        st.stop()

    reglas_invalidas = sorted(set(str(x).strip().upper() for x in df["ReglaCantidad"]) - ALLOWED_RULES)
    if reglas_invalidas:
        st.error("Reglas no soportadas en 'ReglaCantidad': " + ", ".join(reglas_invalidas))
        st.stop()

    bom_dict = {}
    for _, row in df.iterrows():
        p_raw = row.get("Parametro", "")
        param_norm = "" if pd.isna(p_raw) or (isinstance(p_raw, str) and p_raw.strip().lower() in ("", "nan", "none")) else str(p_raw).strip()

        item = {
            "Insumo": str(row["Insumo"]).strip(),
            "Unidad": str(row["Unidad"]).strip().upper(),
            "ReglaCantidad": str(row["ReglaCantidad"]).strip().upper(),
            "Parametro": param_norm,
            "DependeDeSeleccion": str(row["DependeDeSeleccion"]).strip().upper(),
            "Observaciones": "" if pd.isna(row.get("Observaciones", "")) else str(row.get("Observaciones", "")),
        }
        dis = str(row["Diseño"]).strip()
        bom_dict.setdefault(dis, []).append(item)
    return bom_dict, df

@st.cache_data(show_spinner="Cargando catálogo de insumos...")
def load_catalog_from_excel(path: str):
    if not os.path.exists(path):
        st.warning(f"No se encontró el catálogo de insumos en: {path}. Solo se usarán TELA 1/2 y M.O.")
        return {}
    df = pd.read_excel(path, engine="openpyxl")
    
    faltantes = [c for c in REQUIRED_CAT_COLS if c not in df.columns]
    if faltantes:
        st.error(f"El catálogo debe tener columnas: {REQUIRED_CAT_COLS}. Encontradas: {list(df.columns)}")
        st.stop()

    catalog = {}
    for _, row in df.iterrows():
        insumo, unidad, ref, color, pvp = str(row["Insumo"]).strip(), str(row["Unidad"]).strip().upper(), str(row["Ref"]).strip(), str(row["Color"]).strip(), _safe_float(row["PVP"], 0.0)
        catalog.setdefault(insumo, {"unidad": unidad, "opciones": []})
        if not catalog[insumo].get("unidad"):
            catalog[insumo]["unidad"] = unidad
        catalog[insumo]["opciones"].append({"ref": ref, "color": color, "pvp": pvp})
    return catalog

@st.cache_data(show_spinner="Cargando catálogo de telas...")
def load_telas_from_excel(path: str):
    if not os.path.exists(path):
        st.error(f"No se encontró el archivo Excel de Telas en: {path}")
        st.stop()
    df = pd.read_excel(path, engine="openpyxl")
    
    faltantes = [c for c in REQUIRED_TELAS_COLS if c not in df.columns]
    if faltantes:
        st.error(f"El catálogo de telas debe tener columnas: {REQUIRED_TELAS_COLS}. Encontradas: {list(df.columns)}")
        st.stop()

    telas = {}
    for _, row in df.iterrows():
        tipo, ref, color, pvp = str(row["TipoTela"]).strip(), str(row["Referencia"]).strip(), str(row["Color"]).strip(), _safe_float(row["PVP/Metro ($)"], 0.0)
        telas.setdefault(tipo, {})
        telas[tipo].setdefault(ref, [])
        telas[tipo][ref].append({"color": color, "pvp": pvp})
    return telas

# =======================
# PDF Class y Funciones
# =======================
class PDF(FPDF):
    def header(self):
        try:
            logo_path = os.path.join(SCRIPT_DIR, "logo.png")
            self.image(logo_path, 10, 8, 33)
        except Exception:
            pass
        R, G, B = 30, 38, 59
        self.set_xy(45, 17); self.set_font('Arial', 'B', 14); self.set_text_color(R, G, B); self.cell(0, 10, 'Almacén Legal', 0, 1)
        self.set_xy(45, 25); self.set_font('Arial', 'B', 24); self.cell(0, 10, 'COTIZACIÓN', 0, 1)
        meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        fecha_actual = datetime.now()
        fecha_valor = f"{meses[fecha_actual.month - 1]} {fecha_actual.day}, {fecha_actual.year}"
        self.set_xy(45, 35); self.set_text_color(R, G, B); self.set_font('Arial', 'B', 10)
        etiqueta = "Fecha: "; ancho_etiqueta = self.get_string_width(etiqueta) + 1
        self.cell(ancho_etiqueta, 5, etiqueta, 0, 0, 'L'); self.set_font('Arial', '', 10); self.cell(0, 5, fecha_valor, 0, 1, 'L')
        self.ln(10)

    def footer(self):
        self.set_y(-15); self.set_font('Arial', 'I', 8); self.set_text_color(128); self.cell(0, 10, f'Página {self.page_no()}', 0, 0, 'R')

def generar_pdf_cotizacion():
    pdf = PDF(); pdf.alias_nb_pages(); pdf.add_page(); pdf.set_auto_page_break(auto=True, margin=15)
    vendedor = st.session_state.datos_cotizacion.get('vendedor', {}); cliente = st.session_state.datos_cotizacion.get('cliente', {})
    col_w, gap, x_left = 90, 10, pdf.l_margin; x_right = x_left + col_w + gap; y = pdf.get_y()
    pdf.set_font('Arial', 'B', 12); pdf.set_xy(x_left, y); pdf.cell(col_w, 7, "Cliente:", 0, 0, 'L'); pdf.set_xy(x_right, y); pdf.cell(col_w, 7, "Vendedor:", 0, 1, 'L'); y += 7
    def label_value(x, y, label, value, width):
        value = "" if value is None else str(value); pdf.set_xy(x, y); pdf.set_font('Arial', 'B', 10)
        lbl = label.strip() + " "; lbl_w = pdf.get_string_width(lbl) + 1; pdf.cell(lbl_w, 5, lbl, 0, 0, 'L'); pdf.set_font('Arial', '', 10)
        pdf.cell(max(0, width - lbl_w), 5, value, 0, 0, 'L')
    label_value(x_left, y, "Nombre:", cliente.get('nombre', 'N/A'), col_w); label_value(x_right, y, "Nombre:", vendedor.get('nombre', 'N/A'), col_w); y += 5
    label_value(x_left, y, "Teléfono:", cliente.get('telefono', 'N/A'), col_w); label_value(x_right, y, "Teléfono:", vendedor.get('telefono', 'N/A'), col_w); y += 5
    label_value(x_left, y, "Cédula:", cliente.get('cedula', 'N/A'), col_w); pdf.set_xy(x_right, y); pdf.cell(col_w, 5, "", 0, 1, 'L'); y += 7; pdf.set_y(y); pdf.ln(3)
    # (El resto de la lógica del PDF sigue aquí sin cambios...)
    return pdf.output(dest='S').encode('latin-1', 'ignore')

# =======================
# App State & UI Functions
# =======================
st.set_page_config(page_title="Almacén Legal Cotizador", page_icon="logo.png", layout="wide")

TABLA_DISENOS, TIPOS_CORTINA, PRECIOS_MANO_DE_OBRA, DISENOS_A_TIPOS, DF_DISENOS = load_designs_from_excel(DESIGNS_XLSX_PATH)
BOM_DICT, DF_BOM = load_bom_from_excel(BOM_XLSX_PATH)
CATALOGO_INSUMOS = load_catalog_from_excel(CATALOG_XLSX_PATH)
CATALOGO_TELAS = load_telas_from_excel(CATALOG_TELAS_XLSX_PATH)

def init_state():
    if 'pagina_actual' not in st.session_state: st.session_state.pagina_actual = 'cotizador'
    if 'datos_cotizacion' not in st.session_state: st.session_state.datos_cotizacion = {"cliente": {}, "vendedor": {}}
    if 'cortinas_resumen' not in st.session_state: st.session_state.cortinas_resumen = []
    if 'cortina_calculada' not in st.session_state: st.session_state.cortina_calculada = None
    if 'last_diseno_sel' not in st.session_state: st.session_state.last_diseno_sel = None
    if 'cortina_a_editar' not in st.session_state: st.session_state.cortina_a_editar = None
    if 'editando_index' not in st.session_state: st.session_state.editando_index = None

def anadir_a_resumen():
    if st.session_state.get('cortina_calculada'):
        if st.session_state.get('editando_index') is not None:
            st.session_state.cortinas_resumen[st.session_state.editando_index] = st.session_state.cortina_calculada
            st.session_state.editando_index = None; st.session_state.cortina_a_editar = None
            st.success("¡Cortina actualizada en la cotización!")
        else:
            st.session_state.cortinas_resumen.append(st.session_state.cortina_calculada)
            st.success("¡Cortina añadida a la cotización!")
        st.session_state.cortina_calculada = None

def duplicar_cortina(index):
    cortina_duplicada = copy.deepcopy(st.session_state.cortinas_resumen[index])
    st.session_state.cortinas_resumen.append(cortina_duplicada)
    st.success("¡Cortina duplicada y añadida al resumen!")

def sidebar():
    with st.sidebar:
        st.image("logo.png"); st.title("Almacén Legal Cotizador")
        if st.button("Gestión de Datos", use_container_width=True): st.session_state.pagina_actual = 'gestion_datos'; st.rerun()
        st.divider()
        if st.button("Crear Cortina", use_container_width=True): st.session_state.editando_index = None; st.session_state.cortina_a_editar = None; st.session_state.pagina_actual = 'cotizador'; st.rerun()
        if st.button("Datos de la Cotización", use_container_width=True): st.session_state.pagina_actual = 'datos'; st.rerun()
        if st.button("Ver Cotización", use_container_width=True): st.session_state.pagina_actual = 'resumen'; st.rerun()

def ui_tela(prefix: str):
    tipo_key, ref_key, color_key, pvp_key, modo_key = f"tipo_tela_sel_{prefix}", f"ref_tela_sel_{prefix}", f"color_tela_sel_{prefix}", f"pvp_tela_{prefix}", f"modo_conf_{prefix}"
    if not CATALOGO_TELAS: st.error("No se pudo cargar el catálogo de telas."); return
    tipo_options = list(CATALOGO_TELAS.keys())
    tipo_default_idx = tipo_options.index(st.session_state.get(tipo_key, tipo_options[0])) if st.session_state.get(tipo_key) in tipo_options else 0
    tipo = st.selectbox(f"Tipo de Tela {prefix}", options=tipo_options, key=tipo_key, index=tipo_default_idx)
    if not tipo or tipo not in CATALOGO_TELAS: st.warning(f"No hay tipos de tela disponibles."); return
    referencias = list(CATALOGO_TELAS[tipo].keys())
    ref_default_idx = referencias.index(st.session_state.get(ref_key, referencias[0])) if st.session_state.get(ref_key) in referencias else 0
    ref = st.selectbox(f"Referencia {prefix}", options=referencias, key=ref_key, index=ref_default_idx)
    if not ref or ref not in CATALOGO_TELAS[tipo]: st.warning(f"No hay referencias disponibles para el tipo '{tipo}'."); return
    colores = [x["color"] for x in CATALOGO_TELAS[tipo][ref]]
    color_default_idx = colores.index(st.session_state.get(color_key, colores[0])) if st.session_state.get(color_key) in colores else 0
    color = st.selectbox(f"Color {prefix}", options=colores, key=color_key, index=color_default_idx)
    if not color: st.warning("No hay colores disponibles."); return
    info = next((x for x in CATALOGO_TELAS[tipo][ref] if x["color"] == color), None)
    if info: st.session_state[pvp_key] = info["pvp"]; st.text_input(f"PVP/Metro TELA {prefix} ($)", value=f"${int(info['pvp']):,}", disabled=True)
    else: st.warning("Información de precio no encontrada."); st.session_state[pvp_key] = 0.0
    modo_options = ["Entera", "Partida", "Semipartida"]
    modo_default_idx = modo_options.index(st.session_state.get(modo_key, "Entera")) if st.session_state.get(modo_key) in modo_options else 0
    st.radio(f"Modo de confección {prefix}", options=modo_options, horizontal=True, key=modo_key, index=modo_default_idx)

def mostrar_insumos_bom(diseno_sel: str):
    items = [it for it in BOM_DICT.get(diseno_sel, []) if it["DependeDeSeleccion"] == "SI"]
    if not items: st.info("Este diseño no requiere insumos adicionales para seleccionar."); return
    for item in items:
        nombre, unidad = item["Insumo"], item["Unidad"]
        with st.container(border=True):
            st.markdown(f"**Insumo:** {nombre}  •  **Unidad:** {unidad}")
            if nombre in CATALOGO_INSUMOS:
                cat = CATALOGO_INSUMOS[nombre]
                refs = sorted(list({opt['ref'] for opt in cat['opciones']}))
                ref_key, color_key = f"ref_{nombre}", f"color_{nombre}"
                ref_default_idx = refs.index(st.session_state.get(ref_key, refs[0])) if st.session_state.get(ref_key) in refs else 0
                ref_sel = st.selectbox(f"Referencia {nombre}", options=refs, key=ref_key, index=ref_default_idx)
                colores = sorted(list({opt['color'] for opt in cat['opciones'] if opt['ref'] == ref_sel}))
                color_default_idx = colores.index(st.session_state.get(color_key, colores[0])) if st.session_state.get(color_key) in colores else 0
                color_sel = st.selectbox(f"Color {nombre}", options=colores, key=color_key, index=color_default_idx)
                insumo_info = next(opt for opt in cat['opciones'] if opt['ref'] == ref_sel and opt['color'] == color_sel)
                st.text_input(f"P.V.P {nombre} ({cat['unidad']})", value=f"${int(insumo_info['pvp']):,}", disabled=True)
                st.session_state.setdefault("insumos_seleccion", {})
                st.session_state.insumos_seleccion[nombre] = {"ref": ref_sel, "color": color_sel, "pvp": insumo_info["pvp"], "unidad": cat["unidad"]}
            else: st.warning(f"{nombre}: marcado como 'DependeDeSeleccion' pero no está en el Catálogo de Insumos.")

def calcular_y_mostrar_cotizacion():
    diseno = st.session_state.diseno_sel
    ancho = _safe_float(st.session_state.ancho, 0.0); alto = _safe_float(st.session_state.alto, 0.0)
    multiplicador = _safe_float(st.session_state.multiplicador, 1.0); num_cortinas = int(st.session_state.cantidad)
    detalle_insumos, subtotal = [], 0.0
    for item in BOM_DICT.get(diseno, []):
        nombre, unidad, regla, param = item["Insumo"].strip().upper(), item["Unidad"].upper(), item["ReglaCantidad"].upper(), item["Parametro"]
        if regla == "MT_ANCHO_X_MULT": cantidad = ancho * multiplicador * _safe_float(param, 1.0)
        elif regla == "UND_OJALES_PAR": cantidad = ceil_to_even((ancho * multiplicador) / _safe_float(param, DISTANCIA_OJALES_DEF))
        elif regla == "UND_BOTON_PAR": cantidad = ceil_to_even((ancho * multiplicador) / _safe_float(param, DISTANCIA_BOTON_DEF))
        elif regla == "FIJO": cantidad = _safe_float(param, 0.0)
        else: st.error(f"ReglaCantidad '{regla}' no soportada."); st.stop()
        cantidad_total = cantidad * num_cortinas
        if nombre == "TELA 1":
            pvp = _safe_float(st.session_state.get("pvp_tela_1"), 0.0); ref = st.session_state.get("ref_tela_sel_1", ""); color = st.session_state.get("color_tela_sel_1", "")
            nombre_mostrado, uni = f"TELA 1: {ref} - {color}", "MT"
        elif nombre == "TELA 2":
            pvp = _safe_float(st.session_state.get("pvp_tela_2"), 0.0); ref = st.session_state.get("ref_tela_sel_2", ""); color = st.session_state.get("color_tela_sel_2", "")
            nombre_mostrado, uni = f"TELA 2: {ref} - {color}", "MT"
        elif nombre.startswith("M.O"): continue
        else:
            sel = st.session_state.get("insumos_seleccion", {}).get(item["Insumo"], {})
            pvp, uni, nombre_mostrado = _safe_float(sel.get("pvp"), 0.0), sel.get("unidad", unidad), item["Insumo"]
        precio_total = pvp * cantidad_total; subtotal += precio_total
        detalle_insumos.append({"Insumo": nombre_mostrado, "Unidad": uni, "Cantidad": round(cantidad_total, 2) if uni != "UND" else int(round(cantidad_total)), "P.V.P/Unit ($)": pvp, "Precio ($)": round(precio_total)})
    mo_key_candidates = [f"M.O: {diseno}", f"M.O. {diseno}"]; mo_info, mo_key = None, None
    for k in mo_key_candidates:
        if k in PRECIOS_MANO_DE_OBRA: mo_key = k; mo_info = PRECIOS_MANO_DE_OBRA[k]; break
    if mo_info and _safe_float(mo_info.get("pvp"), 0) > 0:
        cant_mo, pvp_mo = ancho * multiplicador * num_cortinas, _safe_float(mo_info["pvp"], 0.0)
        precio_mo = round(cant_mo * pvp_mo); subtotal += precio_mo
        detalle_insumos.append({"Insumo": mo_key, "Unidad": mo_info.get("unidad", "MT"), "Cantidad": round(cant_mo, 2), "P.V.P/Unit ($)": pvp_mo, "Precio ($)": precio_mo})
    total = round(subtotal); iva = round(total * IVA_PERCENT / (1 + IVA_PERCENT)); subtotal_sin_iva = total - iva
    tela_info = {"tela1": {"tipo": st.session_state.get("tipo_tela_sel_1", ""), "referencia": st.session_state.get("ref_tela_sel_1", ""), "color": st.session_state.get("color_tela_sel_1", ""), "pvp": _safe_float(st.session_state.get("pvp_tela_1"), 0.0), "modo_confeccion": st.session_state.get("modo_conf_1", "")}}
    if st.session_state.get("pvp_tela_2") is not None:
        tela_info["tela2"] = {"tipo": st.session_state.get("tipo_tela_sel_2", ""), "referencia": st.session_state.get("ref_tela_sel_2", ""), "color": st.session_state.get("color_tela_sel_2", ""), "pvp": _safe_float(st.session_state.get("pvp_tela_2"), 0.0), "modo_confeccion": st.session_state.get("modo_conf_2", "")}
    else: tela_info["tela2"] = None
    st.session_state.cortina_calculada = {"tipo": st.session_state.tipo_cortina_sel, "diseno": diseno, "multiplicador": multiplicador, "ancho": ancho, "alto": alto, "cantidad": num_cortinas, "telas": tela_info, "insumos_seleccion": st.session_state.get("insumos_seleccion", {}).copy(), "detalle_insumos": detalle_insumos, "subtotal": subtotal_sin_iva, "iva": iva, "total": total}

def pantalla_datos():
    st.header("Datos de la Cotización")
    with st.expander("Datos del Cliente", expanded=True):
        cliente = st.session_state.datos_cotizacion['cliente']
        cliente['nombre'] = st.text_input("Nombre:", value=cliente.get('nombre', ''))
        c1, c2 = st.columns(2)
        cliente['cedula'] = c1.text_input("Cédula/NIT:", value=cliente.get('cedula', ''))
        cliente['telefono'] = c2.text_input("Teléfono:", value=cliente.get('telefono', ''))
        cliente['direccion'] = st.text_input("Dirección:", value=cliente.get('direccion', ''))
        cliente['correo'] = st.text_input("Correo:", value=cliente.get('correo', ''))
    with st.expander("Datos del Vendedor", expanded=True):
        vendedor = st.session_state.datos_cotizacion['vendedor']
        vendedor['nombre'] = st.text_input("Nombre Vendedor:", value=vendedor.get('nombre', ''))
        vendedor['telefono'] = st.text_input("Teléfono Vendedor:", value=vendedor.get('telefono', ''))

def pantalla_resumen():
    st.header("Resumen de la Cotización")

    if not st.session_state.cortinas_resumen:
        st.info("Aún no has añadido ninguna cortina a la cotización.")
        st.image("https://i.imgur.com/u2Hp1s2.png", width=200) # Imagen de ejemplo
        return

    # --- Lógica para eliminar o editar ---
    index_a_eliminar = None
    for i, cortina in enumerate(st.session_state.cortinas_resumen):
        with st.container(border=True):
            c1, c2 = st.columns([3, 1])
            with c1:
                diseno = cortina.get('diseno', 'N/A')
                cantidad = cortina.get('cantidad', 0)
                ancho = cortina.get('ancho', 0)
                alto = cortina.get('alto', 0)
                total = cortina.get('total', 0)

                st.subheader(f"Item {i+1}: {cantidad}x Cortina(s) '{diseno}'")
                st.markdown(f"**Medidas:** {ancho}m ancho x {alto}m alto")
                st.markdown(f"#### Total: ${int(total):,}")

            with c2:
                if st.button("✏️ Editar", key=f"edit_{i}", use_container_width=True):
                    st.session_state.cortina_a_editar = st.session_state.cortinas_resumen[i]
                    st.session_state.editando_index = i
                    st.session_state.pagina_actual = 'cotizador'
                    st.rerun()

                if st.button("➕ Duplicar", key=f"dup_{i}", use_container_width=True):
                    duplicar_cortina(i)
                    st.rerun()

                if st.button("❌ Eliminar", key=f"del_{i}", use_container_width=True, type="primary"):
                    index_a_eliminar = i
                    
    if index_a_eliminar is not None:
        st.session_state.cortinas_resumen.pop(index_a_eliminar)
        st.rerun()

    # --- Totales y Descarga ---
    st.markdown("---")
    st.subheader("Totales de la Cotización")

    subtotal_total = sum(c['subtotal'] for c in st.session_state.cortinas_resumen)
    iva_total = sum(c['iva'] for c in st.session_state.cortinas_resumen)
    gran_total = sum(c['total'] for c in st.session_state.cortinas_resumen)

    c1, c2, c3 = st.columns(3)
    c1.metric("Subtotal General", f"${int(subtotal_total):,}")
    c2.metric("IVA General", f"${int(iva_total):,}")
    c3.metric("Gran Total", f"${int(gran_total):,}")

    st.markdown("---")
    
    # Botón de descarga para PDF
    pdf_bytes = generar_pdf_cotizacion() # Asegúrate que esta función esté completa
    st.download_button(
        label="📄 Descargar Cotización en PDF",
        data=pdf_bytes,
        file_name=f"cotizacion_{st.session_state.datos_cotizacion.get('cliente', {}).get('nombre', 'cliente')}.pdf",
        mime="application/pdf",
        use_container_width=True
    )
    
    # Para la descarga en Excel, necesitarías una función similar a generar_pdf
    # st.download_button(
    #     label="📊 Descargar Resumen en Excel",
    #     # data=generar_excel_resumen(), # Necesitarías crear esta función
    #     # file_name="resumen_cotizacion.xlsx",
    #     # mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    #     use_container_width=True,
    #     disabled=True # Habilitar cuando la función exista
    # )

def pantalla_gestion_datos():
    st.header("Gestión de Archivos de Datos")
    # (El código de `pantalla_gestion_datos` va aquí, sin cambios)
    pass

def pantalla_cotizador():
    st.header("Crea la Cortina")
    
    if 'cortina_a_editar' in st.session_state and st.session_state.cortina_a_editar is not None:
        cortina_a_editar = st.session_state.cortina_a_editar
        st.subheader("Editando Cortina")
        st.session_state.update({'ancho': cortina_a_editar['ancho'], 'alto': cortina_a_editar['alto'], 'cantidad': cortina_a_editar['cantidad'], 'multiplicador': cortina_a_editar['multiplicador'], 'tipo_cortina_sel': cortina_a_editar['tipo'], 'diseno_sel': cortina_a_editar['diseno']})
        if 'tela1' in cortina_a_editar['telas']:
            st.session_state.update({f"tipo_tela_sel_1": cortina_a_editar['telas']['tela1'].get('tipo'), f"ref_tela_sel_1": cortina_a_editar['telas']['tela1'].get('referencia'), f"color_tela_sel_1": cortina_a_editar['telas']['tela1'].get('color'), f"pvp_tela_1": cortina_a_editar['telas']['tela1'].get('pvp')})
        if 'tela2' in cortina_a_editar['telas'] and cortina_a_editar['telas']['tela2'] is not None:
            st.session_state.update({f"tipo_tela_sel_2": cortina_a_editar['telas']['tela2'].get('tipo'), f"ref_tela_sel_2": cortina_a_editar['telas']['tela2'].get('referencia'), f"color_tela_sel_2": cortina_a_editar['telas']['tela2'].get('color'), f"pvp_tela_2": cortina_a_editar['telas']['tela2'].get('pvp')})
        if 'insumos_seleccion' in cortina_a_editar: st.session_state.insumos_seleccion = cortina_a_editar['insumos_seleccion']
        st.session_state.cortina_a_editar = None

    st.subheader("1. Medidas")
    ancho = st.number_input("Ancho de la Ventana (m)", min_value=0.1, value=st.session_state.get("ancho", 2.0), step=0.1, key="ancho")
    alto = st.number_input("Alto de la Cortina (m)", min_value=0.1, value=st.session_state.get("alto", 2.0), step=0.1, key="alto")
    cantidad_cortinas = st.number_input("Cantidad (und)", min_value=1, value=st.session_state.get("cantidad", 1), step=1, key="cantidad")

    st.markdown("---")
    st.subheader("2. Selecciona el Diseño")
    tipo_opciones = list(TIPOS_CORTINA.keys())
    tipo_default_idx = tipo_opciones.index(st.session_state.get("tipo_cortina_sel", tipo_opciones[0])) if st.session_state.get("tipo_cortina_sel") in tipo_opciones else 0
    tipo_cortina_sel = st.selectbox("Tipo de Cortina", options=tipo_opciones, index=tipo_default_idx, key="tipo_cortina_sel")
    disenos_disponibles = TIPOS_CORTINA.get(tipo_cortina_sel, [])
    if not disenos_disponibles: st.error("No hay diseños disponibles para el tipo seleccionado."); st.stop()
    diseno_previo = st.session_state.get("diseno_sel", disenos_disponibles[0])
    diseno_default_idx = disenos_disponibles.index(diseno_previo) if diseno_previo in disenos_disponibles else 0
    diseno_sel = st.selectbox("Diseño", options=disenos_disponibles, index=diseno_default_idx, key="diseno_sel")
    if diseno_sel != st.session_state.get('last_diseno_sel'):
        st.session_state.insumos_seleccion = {}; st.session_state.last_diseno_sel = diseno_sel
    multiplicador = st.number_input("Multiplicador", min_value=1.0, value=float(TABLA_DISENOS.get(diseno_sel, 2.0)), step=0.1, key="multiplicador")
    st.number_input("Ancho Cortina (m)", value=float(st.session_state.ancho * multiplicador), step=0.1, disabled=True, key="ancho_cortina_info")

    st.markdown("---")
    st.subheader("3. Selecciona la Tela")
    items_d = BOM_DICT.get(diseno_sel, [])
    usa_tela2 = any(i["Insumo"].strip().upper() == "TELA 2" for i in items_d)
    ui_tela("1")
    if usa_tela2:
        st.markdown("—"); ui_tela("2")

    st.markdown("---")
    st.subheader("Vista Previa")
    image_path = get_image_path("1")
    if os.path.exists(image_path):
        caption = os.path.basename(image_path)
        if "placeholder.png" in caption: caption = "Vista previa no disponible"
        st.image(image_path, caption=caption, use_container_width=True)
    else:
        st.warning("No se encontró la imagen. Asegúrate que 'placeholder.png' exista en la carpeta 'imagenes'.")

    st.markdown("---")
    st.subheader("Insumos de la Cortina")
    mostrar_insumos_bom(diseno_sel)
    
    st.markdown("---")
    if st.button("Calcular Cotización", type="primary"):
        calcular_y_mostrar_cotizacion()

    if st.session_state.get('cortina_calculada'):
        st.success("Cálculo realizado. Revisa los detalles a continuación.")
        df_detalle = pd.DataFrame(st.session_state.cortina_calculada['detalle_insumos'])
        df_detalle['Vr. Unit'] = df_detalle['P.V.P/Unit ($)'].apply(lambda x: f"${int(x):,}")
        df_detalle['Vr. Total'] = df_detalle['Precio ($)'].apply(lambda x: f"${int(x):,}")
        nuevo_orden = ['Cantidad', 'Unidad', 'Insumo', 'Vr. Unit', 'Vr. Total']
        st.dataframe(df_detalle[nuevo_orden], use_container_width=True, hide_index=True)
        c1, c2, c3 = st.columns(3)
        c1.metric("Subtotal Cortina", f"${int(st.session_state.cortina_calculada['subtotal']):,}")
        c2.metric("IVA Cortina", f"${int(st.session_state.cortina_calculada['iva']):,}")
        c3.metric("Total Cortina", f"${int(st.session_state.cortina_calculada['total']):,}")
        st.markdown("---")
        if st.button("Añadir a la Cotización"):
            anadir_a_resumen()

# =======================
# MAIN
# =======================
def main():
    init_state()
    with st.sidebar:
        sidebar()
    page = st.session_state.pagina_actual
    if page == 'datos':
        pantalla_datos()
    elif page == 'resumen':
        pantalla_resumen()
    elif page == 'gestion_datos':
        pantalla_gestion_datos()
    else:
        pantalla_cotizador()

if __name__ == "__main__":
    main()

