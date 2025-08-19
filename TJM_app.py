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

_default_designs = os.path.join(SCRIPT_DIR, "data", "disenos_cortina.xlsx")
_default_bom     = os.path.join(SCRIPT_DIR, "data", "bom.xlsx")
_default_cat_ins = os.path.join(SCRIPT_DIR, "data", "catalogo_insumos.xlsx")
_default_cat_tel = os.path.join(SCRIPT_DIR, "data", "catalogo_telas.xlsx")

DESIGNS_XLSX_PATH      = os.environ.get("DESIGNS_XLSX_PATH")       or st.secrets.get("DESIGNS_XLSX_PATH", _default_designs)
BOM_XLSX_PATH          = os.environ.get("BOM_XLSX_PATH")           or st.secrets.get("BOM_XLSX_PATH", _default_bom)
CATALOG_XLSX_PATH      = os.environ.get("CATALOG_XLSX_PATH")       or st.secrets.get("CATALOG_XLSX_PATH", _default_cat_ins)
CATALOG_TELAS_XLSX_PATH = (os.environ.get("CATALOG_TELAS_XLSX_PATH") or st.secrets.get("CATALOG_TELAS_XLSX_PATH", _default_cat_tel))

REQUIRED_DESIGNS_COLS = ["Diseño", "Tipo", "Multiplicador", "PVP M.O."]
REQUIRED_BOM_COLS     = ["Diseño", "Insumo", "Unidad", "ReglaCantidad", "Parametro", "DependeDeSeleccion", "Observaciones"]
REQUIRED_CAT_COLS     = ["Insumo", "Unidad", "Ref", "Color", "PVP"]
REQUIRED_TELAS_COLS   = ["TipoTela", "Referencia", "Color", "PVP/Metro ($)"]

ALLOWED_RULES = {"MT_ANCHO_X_MULT", "UND_OJALES_PAR", "UND_BOTON_PAR", "FIJO"}

IVA_PERCENT = 0.19
DISTANCIA_BOTON_DEF = 0.20
DISTANCIA_OJALES_DEF = 0.14

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

        tabla_disenos[dis] = mult
        precios_mo[f"M.O: {dis}"] = {"unidad": "MT", "pvp": mo_val}
        disenos_a_tipos.setdefault(dis, [])
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

    # Validar reglas
    reglas_invalidas = sorted(set(str(x).strip().upper() for x in df["ReglaCantidad"]) - ALLOWED_RULES)
    if reglas_invalidas:
        st.error("Reglas no soportadas en 'ReglaCantidad': " + ", ".join(reglas_invalidas))
        st.stop()

    bom_dict = {}
    for _, row in df.iterrows():
        p_raw = row.get("Parametro", "")
        if pd.isna(p_raw) or (isinstance(p_raw, str) and p_raw.strip().lower() in ("", "nan", "none")):
            param_norm = ""
        else:
            param_norm = str(p_raw).strip()

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
        insumo = str(row["Insumo"]).strip()
        unidad = str(row["Unidad"]).strip().upper()
        ref = str(row["Ref"]).strip()
        color = str(row["Color"]).strip()
        pvp = _safe_float(row["PVP"], 0.0)
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
        tipo = str(row["TipoTela"]).strip()
        ref = str(row["Referencia"]).strip()
        color = str(row["Color"]).strip()
        pvp = _safe_float(row["PVP/Metro ($)"], 0.0)
        telas.setdefault(tipo, {})
        telas[tipo].setdefault(ref, [])
        telas[tipo][ref].append({"color": color, "pvp": pvp})
    return telas

# =======================
# PDF (igual que antes)
# =======================
class PDF(FPDF):
    def header(self):
        try:
            logo_path = os.path.join(SCRIPT_DIR, "logo.png")
            self.image(logo_path, 10, 8, 33)
        except Exception:
            pass

        self.set_xy(45, 17)
        self.set_font('Arial', '', 14)
        self.set_text_color(22, 57, 126)
        self.cell(0, 10, 'Almacén Legal', 0, 1)

        self.set_xy(45, 25)
        self.set_font('Arial', 'B', 24)
        self.set_text_color(22, 57, 126)
        self.cell(0, 10, 'COTIZACIÓN', 0, 1)

        meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        fecha_actual = datetime.now()
        mes_nombre = meses[fecha_actual.month - 1]
        fecha_larga = f"Fecha: {mes_nombre} {fecha_actual.day}, {fecha_actual.year}"

        self.set_xy(45, 35)
        self.set_font('Arial', '', 10)
        self.set_text_color(128)
        self.cell(0, 5, fecha_larga, 0, 1, 'L')

        self.ln(10)

    def footer(self):
        self.set_y(-15)
        self.set_font('Arial', 'I', 8)
        self.set_text_color(128)
        self.cell(0, 10, f'Página {self.page_no()}', 0, 0, 'R')

# =======================
# App state & UI
# =======================
st.set_page_config(page_title="Almacén Legal Cotizador", page_icon="logo.png", layout="wide")

TABLA_DISENOS, TIPOS_CORTINA, PRECIOS_MANO_DE_OBRA, DISENOS_A_TIPOS, DF_DISENOS = load_designs_from_excel(DESIGNS_XLSX_PATH)
BOM_DICT, DF_BOM = load_bom_from_excel(BOM_XLSX_PATH)
CATALOGO_INSUMOS = load_catalog_from_excel(CATALOG_XLSX_PATH)
CATALOGO_TELAS = load_telas_from_excel(CATALOG_TELAS_XLSX_PATH)

def init_state():
    if 'pagina_actual' not in st.session_state:
        st.session_state.pagina_actual = 'cotizador'
    if 'datos_cotizacion' not in st.session_state:
        st.session_state.datos_cotizacion = {"cliente": {}, "vendedor": {}}
    if 'cortinas_resumen' not in st.session_state:
        st.session_state.cortinas_resumen = []
    if 'cortina_calculada' not in st.session_state:
        st.session_state.cortina_calculada = None
    if 'last_diseno_sel' not in st.session_state:
        st.session_state.last_diseno_sel = None
    if 'cortina_a_editar' not in st.session_state:
        st.session_state.cortina_a_editar = None
    if 'editando_index' not in st.session_state:
        st.session_state.editando_index = None

def anadir_a_resumen():
    if st.session_state.get('cortina_calculada'):
        if st.session_state.get('editando_index') is not None:
            index = st.session_state.editando_index
            st.session_state.cortinas_resumen[index] = st.session_state.cortina_calculada
            st.session_state.editando_index = None
            st.session_state.cortina_a_editar = None
            st.success("¡Cortina actualizada en la cotización!")
        else:
            st.session_state.cortinas_resumen.append(st.session_state.cortina_calculada)
            st.success("¡Cortina añadida a la cotización!")
        st.session_state.cortina_calculada = None

def duplicar_cortina(index):
    cortina_a_duplicar = st.session_state.cortinas_resumen[index]
    cortina_duplicada = copy.deepcopy(cortina_a_duplicar)
    st.session_state.cortinas_resumen.append(cortina_duplicada)
    st.success("¡Cortina duplicada y añadida al resumen!")

def generar_pdf_cotizacion():
    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)
    
    # --- Datos del Cliente y Vendedor (diseño de tabla) ---
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(90, 7, "Vendedor:", 0, 0, 'L')
    pdf.cell(0, 7, "Cliente:", 0, 1, 'L')
    
    pdf.set_font('Arial', '', 10)
    pdf.cell(90, 5, f"Nombre: {st.session_state.datos_cotizacion['vendedor'].get('nombre', 'N/A')}", 0, 0, 'L')
    pdf.cell(0, 5, f"Nombre: {st.session_state.datos_cotizacion['cliente'].get('nombre', 'N/A')}", 0, 1, 'L')
    
    pdf.cell(90, 5, f"Teléfono: {st.session_state.datos_cotizacion['vendedor'].get('telefono', 'N/A')}", 0, 0, 'L')
    pdf.cell(0, 5, f"Teléfono: {st.session_state.datos_cotizacion['cliente'].get('telefono', 'N/A')}", 0, 1, 'L')

    pdf.cell(90, 5, f"Dirección: {st.session_state.datos_cotizacion['cliente'].get('direccion', 'N/A')}", 0, 0, 'L')
    pdf.cell(0, 5, f"Cédula: {st.session_state.datos_cotizacion['cliente'].get('cedula', 'N/A')}", 0, 1, 'L')
    
    pdf.ln(10)

    # --- Tabla de productos ---
    pdf.set_font('Arial', 'B', 9)
    pdf.set_fill_color(129, 153, 114)  # Color verde como el de la imagen
    pdf.set_text_color(255) # Texto blanco
    
    # Encabezados de la tabla
    column_widths = [10, 45, 35, 45, 25, 30] # N, Nombre, Cantidad, Caract, Valor, Comentarios
    pdf.cell(column_widths[0], 10, 'N°', 1, 0, 'C', 1)
    pdf.cell(column_widths[1], 10, 'Nombre', 1, 0, 'C', 1)
    pdf.cell(column_widths[2], 10, 'Cant. / Ancho x Alto', 1, 0, 'C', 1)
    pdf.cell(column_widths[3], 10, 'Características', 1, 0, 'C', 1)
    pdf.cell(column_widths[4], 10, 'Valor Total', 1, 0, 'C', 1)
    pdf.cell(column_widths[5], 10, 'Comentarios', 1, 1, 'C', 1)
    
    pdf.set_font('Arial', '', 9)
    pdf.set_text_color(0) # Texto negro
    pdf.set_fill_color(255) # Fondo blanco
    
    # Filas de la tabla
    for i, cortina in enumerate(st.session_state.cortinas_resumen):
        # Datos para cada celda
        num = str(i + 1)
        nombre = cortina['diseno']
        
        ancho_calc = cortina['ancho'] * cortina['multiplicador']
        cant_ancho_alto = f"{cortina['cantidad']} und\n{ancho_calc:.2f} x {cortina['alto']:.2f} mts"
        
        caracteristicas = ""
        # Info de Tela 1
        tela1_info = cortina['telas']['tela1']
        caracteristicas += f"Tela 1: {tela1_info['referencia']} - {tela1_info['color']} [{tela1_info['modo_confeccion']}]"
        # Info de Tela 2
        if cortina['telas'].get('tela2') and cortina['telas']['tela2'].get('referencia'):
            tela2_info = cortina['telas']['tela2']
            caracteristicas += f"\nTela 2: {tela2_info['referencia']} - {tela2_info['color']} [{tela2_info['modo_confeccion']}]"
        # Info de insumos adicionales
        insumos_sel = cortina.get('insumos_seleccion', {})
        if insumos_sel:
            for insumo, info in insumos_sel.items():
                caracteristicas += f"\n{insumo}: {info['ref']} - {info['color']}"
                
        valor_total = f"${int(cortina['total']):,}"
        comentarios = "" # Por ahora sin comentarios

        # Altura de la fila (basada en la celda con más líneas)
        h_multicell = max(pdf.get_string_width(cant_ancho_alto.split('\n')[0]) / column_widths[2],
                          pdf.get_string_width(caracteristicas.split('\n')[0]) / column_widths[3])
        line_height = 5
        row_height = max(len(cant_ancho_alto.split('\n')),
                         len(caracteristicas.split('\n'))) * line_height + 4
        
        x_pos_start = pdf.get_x()
        y_pos_start = pdf.get_y()
        
        # Dibujar celdas y bordes
        pdf.cell(column_widths[0], row_height, num, 1, 0, 'C')
        pdf.cell(column_widths[1], row_height, nombre, 1, 0)
        
        # Posición para las multiceldas
        pdf.set_xy(x_pos_start + column_widths[0] + column_widths[1], y_pos_start)
        pdf.multi_cell(column_widths[2], line_height, cant_ancho_alto, 1, 'L')
        
        pdf.set_xy(x_pos_start + sum(column_widths[:3]), y_pos_start)
        pdf.multi_cell(column_widths[3], line_height, caracteristicas, 1, 'L')
        
        pdf.set_xy(x_pos_start + sum(column_widths[:4]), y_pos_start)
        pdf.cell(column_widths[4], row_height, valor_total, 1, 0, 'R')
        pdf.cell(column_widths[5], row_height, comentarios, 1, 1)

    pdf.ln(10)
    
    # --- Totales Finales de la Cotización ---
    total_final = sum(c['total'] for c in st.session_state.cortinas_resumen)
    iva = total_final * IVA_PERCENT
    subtotal = total_final - iva
    
    pdf.set_font('Arial', 'B', 10)
    pdf.cell(0, 7, f"Subtotal: ${int(subtotal):,}", 0, 1, 'R')
    pdf.cell(0, 7, f"IVA (19%): ${int(iva):,}", 0, 1, 'R')
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, f"Vr. Total: ${int(total_final):,}", 0, 1, 'R')

    return pdf.output(dest='S').encode('latin-1')
