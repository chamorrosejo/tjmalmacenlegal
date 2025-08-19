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
        # Logo (igual)
        try:
            logo_path = os.path.join(SCRIPT_DIR, "logo.png")
            self.image(logo_path, 10, 8, 33)
        except Exception:
            pass

        # Color del encabezado de la tabla: #1e263b (RGB 30,38,59)
        R, G, B = 30, 38, 59

        # "Almacén Legal" en negrita y color #1e263b
        self.set_xy(45, 17)
        self.set_font('Arial', 'B', 14)
        self.set_text_color(R, G, B)
        self.cell(0, 10, 'Almacén Legal', 0, 1)

        # "COTIZACIÓN" en negrita y color #1e263b
        self.set_xy(45, 25)
        self.set_font('Arial', 'B', 24)
        self.set_text_color(R, G, B)
        self.cell(0, 10, 'COTIZACIÓN', 0, 1)

        # Fecha: etiqueta en negrita + valor en regular, ambos con el mismo color
        meses = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
                 "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]
        fecha_actual = datetime.now()
        mes_nombre = meses[fecha_actual.month - 1]
        fecha_valor = f"{mes_nombre} {fecha_actual.day}, {fecha_actual.year}"

        self.set_xy(45, 35)
        self.set_text_color(R, G, B)
        self.set_font('Arial', 'B', 10)
        etiqueta = "Fecha: "
        ancho_etiqueta = self.get_string_width(etiqueta) + 1
        self.cell(ancho_etiqueta, 5, etiqueta, 0, 0, 'L')
        self.set_font('Arial', '', 10)
        self.cell(0, 5, fecha_valor, 0, 1, 'L')

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

# Reemplaza la función generar_pdf_cotizacion() con este código
# Reemplaza la función generar_pdf_cotizacion() con este código
def generar_pdf_cotizacion():
    pdf = PDF()
    pdf.alias_nb_pages()
    pdf.add_page()
    pdf.set_auto_page_break(auto=True, margin=15)

    # =======================
    # Datos: Cliente (izq) y Vendedor (der)
    # =======================
    vendedor = st.session_state.datos_cotizacion.get('vendedor', {})
    cliente  = st.session_state.datos_cotizacion.get('cliente', {})

    col_w = 90
    gap = 10
    x_left = pdf.l_margin           # CLIENTE
    x_right = x_left + col_w + gap  # VENDEDOR
    y = pdf.get_y()

    pdf.set_font('Arial', 'B', 12)
    pdf.set_xy(x_left, y);  pdf.cell(col_w, 7, "Cliente:", 0, 0, 'L')
    pdf.set_xy(x_right, y); pdf.cell(col_w, 7, "Vendedor:", 0, 1, 'L')
    y += 7

    def label_value(x, y, label, value, width):
        value = "" if value is None else str(value)
        pdf.set_xy(x, y)
        pdf.set_font('Arial', 'B', 10)
        lbl = label.strip() + " "
        lbl_w = pdf.get_string_width(lbl) + 1
        pdf.cell(lbl_w, 5, lbl, 0, 0, 'L')
        pdf.set_font('Arial', '', 10)
        pdf.cell(max(0, width - lbl_w), 5, value, 0, 0, 'L')

    # Cliente y Vendedor (valores en regular)
    label_value(x_left,  y, "Nombre:",   cliente.get('nombre', 'N/A'),  col_w)
    label_value(x_right, y, "Nombre:",   vendedor.get('nombre', 'N/A'), col_w); y += 5
    label_value(x_left,  y, "Teléfono:", cliente.get('telefono', 'N/A'),  col_w)
    label_value(x_right, y, "Teléfono:", vendedor.get('telefono', 'N/A'), col_w); y += 5
    label_value(x_left,  y, "Cédula:",   cliente.get('cedula', 'N/A'),  col_w)
    pdf.set_xy(x_right, y); pdf.cell(col_w, 5, "", 0, 1, 'L')
    y += 7
    pdf.set_y(y)
    pdf.ln(3)

    # =======================
    # Tabla (sin Comentarios)
    # =======================
    column_widths = [10, 50, 40, 65, 25]  # N°, Nombre, Cant/Dim, Características, Total
    header_h = 10
    line_h = 5  # alto por línea
    safety_pad = 2.0  # margen extra de seguridad a la altura de fila

    def draw_table_header():
        pdf.set_font('Arial', 'B', 9)
        # Encabezado color #1e263b
        pdf.set_fill_color(30, 38, 59)
        pdf.set_text_color(255)
        headers = ['N°', 'Nombre', 'Cant. / Ancho x Alto', 'Características', 'Valor Total']
        for w, htxt in zip(column_widths, headers):
            pdf.cell(w, header_h, htxt, 1, 0, 'C', 1)
        pdf.ln(header_h)
        pdf.set_text_color(0)
        pdf.set_font('Arial', '', 9)

    def wrap_to_width(text: str, col_w: float) -> list[str]:
        """Envuelve texto al ancho col_w con la fuente actual."""
        text = "" if text is None else str(text)
        out = []
        for para in text.split("\n"):
            words, line = para.split(" "), ""
            for w in words:
                cand = w if not line else line + " " + w
                if pdf.get_string_width(cand) <= col_w - 2:
                    line = cand
                else:
                    if line:
                        out.append(line); line = w
                    else:
                        token = w
                        while pdf.get_string_width(token) > col_w - 2 and len(token) > 1:
                            k = len(token)
                            while k > 1 and pdf.get_string_width(token[:k]) > col_w - 2:
                                k -= 1
                            out.append(token[:k]); token = token[k:]
                        line = token
            out.append(line)
        return out

    def measure_pairs_height(pairs, col_w, line_h):
        """
        Calcula la altura necesaria para imprimir pares (label, value)
        con label en negrita y value normal, usando el mismo wrapping.
        """
        total_lines = 0
        for label, value in pairs:
            lbl = label.strip() + " "
            pdf.set_font('Arial', 'B', 9)
            lbl_w = pdf.get_string_width(lbl) + 1
            avail_w = max(1, col_w - lbl_w)
            pdf.set_font('Arial', '', 9)
            wrapped_vals = wrap_to_width(value, avail_w) or [""]
            total_lines += len(wrapped_vals)  # una línea por cada segmento envuelto
        return total_lines * line_h

    def draw_label_value_in_cell(x, y, w, line_h, pairs):
        """Dibuja los pares con label en negrita y value normal (con wrapping)."""
        cur_y = y
        for label, value in pairs:
            lbl = label.strip() + " "
            pdf.set_font('Arial', 'B', 9)
            lbl_w = pdf.get_string_width(lbl) + 1
            avail_w = max(1, w - lbl_w)

            pdf.set_font('Arial', '', 9)
            wrapped_vals = wrap_to_width(value, avail_w) or [""]

            # primera línea: label + primer segmento del valor
            pdf.set_xy(x, cur_y)
            pdf.set_font('Arial', 'B', 9); pdf.cell(lbl_w, line_h, lbl, 0, 0, 'L')
            pdf.set_font('Arial', '', 9);  pdf.cell(avail_w, line_h, wrapped_vals[0], 0, 1, 'L')
            cur_y += line_h

            # líneas siguientes: solo el valor
            for seg in wrapped_vals[1:]:
                pdf.set_xy(x + lbl_w, cur_y)
                pdf.set_font('Arial', '', 9)
                pdf.cell(avail_w, line_h, seg, 0, 1, 'L')
                cur_y += line_h
        return cur_y - y

    draw_table_header()

    for i, cortina in enumerate(st.session_state.cortinas_resumen):
        # Pares (label, value) de Características
        pairs = []
        if cortina['telas']['tela1']:
            t1 = cortina['telas']['tela1']
            pairs.append(("Tela 1:", f"{t1['referencia']} - {t1['color']} [{t1['modo_confeccion']}]"))
        if cortina['telas'].get('tela2') and cortina['telas']['tela2'].get('referencia'):
            t2 = cortina['telas']['tela2']
            pairs.append(("Tela 2:", f"{t2['referencia']} - {t2['color']} [{t2['modo_confeccion']}]"))
        for insumo, info in (cortina.get('insumos_seleccion', {}) or {}).items():
            pairs.append((f"{insumo}:", f"{info['ref']} - {info['color']}"))

        # Otras columnas
        num = str(i + 1)
        nombre = cortina['diseno']
        ancho_calc = cortina['ancho'] * cortina['multiplicador']
        cant_str = f"{cortina['cantidad']} und\n{ancho_calc:.2f} x {cortina['alto']:.2f} mts"
        valor_total = f"${int(cortina['total']):,}"

        # Altura de fila = max(altura Cant/Dim, altura Características) + padding
        pdf.set_font('Arial', '', 9)
        cant_lines = wrap_to_width(cant_str, column_widths[2])
        cant_h = len(cant_lines) * line_h
        car_h = measure_pairs_height(pairs, column_widths[3], line_h)
        row_h = max(cant_h, car_h, line_h) + safety_pad

        # Salto de página si no cabe
        if pdf.get_y() + row_h > pdf.page_break_trigger:
            pdf.add_page()
            draw_table_header()

        # Bordes
        x0, y0 = pdf.get_x(), pdf.get_y()
        x = x0
        for w in column_widths:
            pdf.rect(x, y0, w, row_h)
            x += w

        # Contenidos
        pdf.set_xy(x0, y0)
        pdf.multi_cell(column_widths[0], line_h, num, 0, 'C')

        pdf.set_xy(x0 + column_widths[0], y0)
        pdf.multi_cell(column_widths[1], line_h, nombre, 0, 'L')

        pdf.set_xy(x0 + sum(column_widths[:2]), y0)
        pdf.multi_cell(column_widths[2], line_h, "\n".join(cant_lines), 0, 'L')

        x_car = x0 + sum(column_widths[:3])
        draw_label_value_in_cell(x_car, y0, column_widths[3], line_h, pairs)

        pdf.set_xy(x0 + sum(column_widths[:4]), y0)
        pdf.multi_cell(column_widths[4], line_h, valor_total, 0, 'R')

        # Siguiente fila
        pdf.set_xy(x0, y0 + row_h)

    pdf.ln(10)

    # Totales
    subtotal = sum(c['subtotal'] for c in st.session_state.cortinas_resumen)
    iva = sum(c['iva'] for c in st.session_state.cortinas_resumen)
    total_final = sum(c['total'] for c in st.session_state.cortinas_resumen)

    pdf.set_font('Arial', 'B', 10)
    pdf.cell(0, 7, f"Subtotal: ${int(subtotal):,}", 0, 1, 'R')
    pdf.cell(0, 7, f"IVA ({IVA_PERCENT:.0%}): ${int(iva):,}", 0, 1, 'R')
    pdf.set_font('Arial', 'B', 12)
    pdf.cell(0, 10, f"Vr. Total: ${int(total_final):,}", 0, 1, 'R')

    return pdf.output(dest='S').encode('latin-1', 'ignore')



def sidebar():
    with st.sidebar:
        st.image("logo.png") 
        st.title("Almacén Legal Cotizador")
        
        if st.button("Gestión de Datos", use_container_width=True):
            st.session_state.pagina_actual = 'gestion_datos'; st.rerun()
            
        st.divider()

        if st.button("Crear Cortina", use_container_width=True):
            st.session_state.editando_index = None
            st.session_state.cortina_a_editar = None
            st.session_state.pagina_actual = 'cotizador'; st.rerun()
        if st.button("Datos de la Cotización", use_container_width=True):
            st.session_state.pagina_actual = 'datos'; st.rerun()
        if st.button("Ver Cotización", use_container_width=True):
            st.session_state.pagina_actual = 'resumen'; st.rerun()

def pantalla_cotizador():
    st.header("Crea la Cortina")
    
    if 'cortina_a_editar' in st.session_state and st.session_state.cortina_a_editar is not None:
        cortina_a_editar = st.session_state.cortina_a_editar
        
        st.subheader("Editando Cortina")
        st.session_state['ancho'] = cortina_a_editar['ancho']
        st.session_state['alto'] = cortina_a_editar['alto']
        st.session_state['cantidad'] = cortina_a_editar['cantidad']
        st.session_state['multiplicador'] = cortina_a_editar['multiplicador']
        st.session_state['tipo_cortina_sel'] = cortina_a_editar['tipo']
        st.session_state['diseno_sel'] = cortina_a_editar['diseno']
        
        if 'tela1' in cortina_a_editar['telas']:
            st.session_state[f"tipo_tela_sel_1"] = cortina_a_editar['telas']['tela1'].get('tipo')
            st.session_state[f"ref_tela_sel_1"] = cortina_a_editar['telas']['tela1'].get('referencia')
            st.session_state[f"color_tela_sel_1"] = cortina_a_editar['telas']['tela1'].get('color')
            st.session_state[f"pvp_tela_1"] = cortina_a_editar['telas']['tela1'].get('pvp')
        if 'tela2' in cortina_a_editar['telas'] and cortina_a_editar['telas']['tela2'] is not None:
            st.session_state[f"tipo_tela_sel_2"] = cortina_a_editar['telas']['tela2'].get('tipo')
            st.session_state[f"ref_tela_sel_2"] = cortina_a_editar['telas']['tela2'].get('referencia')
            st.session_state[f"color_tela_sel_2"] = cortina_a_editar['telas']['tela2'].get('color')
            st.session_state[f"pvp_tela_2"] = cortina_a_editar['telas']['tela2'].get('pvp')
        if 'insumos_seleccion' in cortina_a_editar:
            st.session_state.insumos_seleccion = cortina_a_editar['insumos_seleccion']

        st.session_state.cortina_a_editar = None

    st.subheader("1. Medidas")
    ancho = st.number_input("Ancho de la Ventana (m)", min_value=0.1, value=st.session_state.get("ancho", 2.0), step=0.1, key="ancho")
    alto = st.number_input("Alto de la Cortina (m)", min_value=0.1, value=st.session_state.get("alto", 2.0), step=0.1, key="alto")
    cantidad_cortinas = st.number_input("Cantidad (und)", min_value=1, value=st.session_state.get("cantidad", 1), step=1, key="cantidad")
    st.markdown("---")
    st.subheader("2. Selecciona el Diseño")

    tipo_opciones = list(TIPOS_CORTINA.keys())
    tipo_default = st.session_state.get("tipo_cortina_sel", tipo_opciones[0])
    
    if tipo_default in tipo_opciones:
        tipo_default_index = tipo_opciones.index(tipo_default)
    else:
        tipo_default_index = 0
    tipo_cortina_sel = st.selectbox("Tipo de Cortina", options=tipo_opciones, index=tipo_default_index, key="tipo_cortina_sel")

    disenos_disponibles = TIPOS_CORTINA.get(tipo_cortina_sel, [])
    if not disenos_disponibles:
        st.error("No hay diseños disponibles para el tipo seleccionado.")
        st.stop()
    
    diseno_previo = st.session_state.get("diseno_sel", disenos_disponibles[0])
    if diseno_previo not in disenos_disponibles:
        diseno_previo = disenos_disponibles[0]
    
    diseno_sel = st.selectbox("Diseño", options=disenos_disponibles, index=disenos_disponibles.index(diseno_previo), key="diseno_sel")
    
    if diseno_sel != st.session_state.get('last_diseno_sel'):
        st.session_state.insumos_seleccion = {}
        st.session_state.last_diseno_sel = diseno_sel
    
    st.session_state.last_diseno_sel = diseno_sel

    valor_multiplicador = float(TABLA_DISENOS.get(diseno_sel, 2.0))
    multiplicador = st.number_input("Multiplicador", min_value=1.0, value=valor_multiplicador, step=0.1, key="multiplicador")

    ancho_cortina = st.session_state.ancho * multiplicador
    st.number_input("Ancho Cortina (m)", value=float(ancho_cortina), step=0.1, disabled=True, key="ancho_cortina_info")

    st.markdown("---")
    st.subheader("3. Selecciona la Tela")

    def ui_tela(prefix: str):
        tipo_key = f"tipo_tela_sel_{prefix}"
        ref_key  = f"ref_tela_sel_{prefix}"
        color_key= f"color_tela_sel_{prefix}"
        pvp_key  = f"pvp_tela_{prefix}"
        modo_key = f"modo_conf_{prefix}"

        if not CATALOGO_TELAS:
            st.error("No se pudo cargar el catálogo de telas.")
            return

        tipo_options = list(CATALOGO_TELAS.keys())
        tipo_default_value = st.session_state.get(tipo_key, tipo_options[0])
        if tipo_default_value in tipo_options:
            tipo_default_index = tipo_options.index(tipo_default_value)
        else:
            tipo_default_index = 0
        tipo = st.selectbox(f"Tipo de Tela {prefix}", options=tipo_options, key=tipo_key, index=tipo_default_index)
        
        if not tipo or tipo not in CATALOGO_TELAS:
            st.warning(f"No hay tipos de tela disponibles.")
            return

        referencias = list(CATALOGO_TELAS[tipo].keys())
        ref_default_value = st.session_state.get(ref_key, referencias[0])
        if ref_default_value in referencias:
            ref_default_index = referencias.index(ref_default_value)
        else:
            ref_default_index = 0
        ref = st.selectbox(f"Referencia {prefix}", options=referencias, key=ref_key, index=ref_default_index)

        if not ref or ref not in CATALOGO_TELAS[tipo]:
            st.warning(f"No hay referencias disponibles para el tipo '{tipo}'.")
            return

        colores = [x["color"] for x in CATALOGO_TELAS[tipo][ref]]
        color_default_value = st.session_state.get(color_key, colores[0])
        if color_default_value in colores:
            color_default_index = colores.index(color_default_value)
        else:
            color_default_index = 0
        color = st.selectbox(f"Color {prefix}", options=colores, key=color_key, index=color_default_index)

        if not color:
            st.warning("No hay colores disponibles.")
            return

        info = next((x for x in CATALOGO_TELAS[tipo][ref] if x["color"] == color), None)
        if info:
            st.session_state[pvp_key] = info["pvp"]
            st.text_input(f"PVP/Metro TELA {prefix} ($)", value=f"${int(info['pvp']):,}", disabled=True)
        else:
            st.warning("Información de precio no encontrada.")
            st.session_state[pvp_key] = 0.0
            
        modo_options = ["Entera", "Partida", "Semipartida"]
        modo_default_value = st.session_state.get(modo_key, "Entera")
        if modo_default_value in modo_options:
            modo_default_index = modo_options.index(modo_default_value)
        else:
            modo_default_index = 0
        st.radio(f"Modo de confección {prefix}", options=modo_options, horizontal=True, key=modo_key, index=modo_default_index)

    items_d = BOM_DICT.get(diseno_sel, [])
    usa_tela2 = any(i["Insumo"].strip().upper() == "TELA 2" for i in items_d)

    ui_tela("1")
    if usa_tela2:
        st.markdown("—")
        ui_tela("2")

    st.markdown("---")
    st.subheader("Insumos de la Cortina")
    mostrar_insumos_bom(diseno_sel)

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


def mostrar_insumos_bom(diseno_sel: str):
    items = [it for it in BOM_DICT.get(diseno_sel, []) if it["DependeDeSeleccion"] == "SI"]
    if not items:
        st.info("Este diseño no requiere insumos adicionales para seleccionar.")
        return

    for item in items:
        nombre = item["Insumo"]
        unidad  = item["Unidad"]
        with st.container(border=True):
            st.markdown(f"**Insumo:** {nombre}  •  **Unidad:** {unidad}")
            if nombre in CATALOGO_INSUMOS:
                cat = CATALOGO_INSUMOS[nombre]
                refs = sorted(list({opt['ref'] for opt in cat['opciones']}))
                ref_key = f"ref_{nombre}"
                color_key = f"color_{nombre}"
                
                ref_default_value = st.session_state.get(ref_key, refs[0])
                if ref_default_value in refs:
                    ref_default_index = refs.index(ref_default_value)
                else:
                    ref_default_index = 0
                ref_sel = st.selectbox(f"Referencia {nombre}", options=refs, key=ref_key, index=ref_default_index)
                
                colores = sorted(list({opt['color'] for opt in cat['opciones'] if opt['ref'] == ref_sel}))
                color_default_value = st.session_state.get(color_key, colores[0])
                if color_default_value in colores:
                    color_default_index = colores.index(color_default_value)
                else:
                    color_default_index = 0
                color_sel = st.selectbox(f"Color {nombre}", options=colores, key=color_key, index=color_default_index)
                
                insumo_info = next(opt for opt in cat['opciones'] if opt['ref'] == ref_sel and opt['color'] == color_sel)
                st.text_input(f"P.V.P {nombre} ({cat['unidad']})", value=f"${int(insumo_info['pvp']):,}", disabled=True)
                st.session_state.setdefault("insumos_seleccion", {})
                st.session_state.insumos_seleccion[nombre] = {"ref": ref_sel, "color": color_sel, "pvp": insumo_info["pvp"], "unidad": cat["unidad"]}
            else:
                st.warning(f"{nombre}: marcado como 'DependeDeSeleccion' pero no está en el Catálogo de Insumos.")

def calcular_y_mostrar_cotizacion():
    diseno = st.session_state.diseno_sel
    ancho = _safe_float(st.session_state.ancho, 0.0)
    alto = _safe_float(st.session_state.alto, 0.0)
    multiplicador = _safe_float(st.session_state.multiplicador, 1.0)
    num_cortinas = int(st.session_state.cantidad)

    detalle_insumos = []
    subtotal = 0.0

    for item in BOM_DICT.get(diseno, []):
        nombre = item["Insumo"].strip().upper()
        unidad = item["Unidad"].upper()
        regla  = item["ReglaCantidad"].upper()
        param  = item["Parametro"]

        if regla == "MT_ANCHO_X_MULT":
            factor = _safe_float(param, 1.0)
            cantidad = ancho * multiplicador * factor
        elif regla == "UND_OJALES_PAR":
            paso = _safe_float(param, DISTANCIA_OJALES_DEF)
            cantidad = ceil_to_even((ancho * multiplicador) / paso)
        elif regla == "UND_BOTON_PAR":
            paso = _safe_float(param, DISTANCIA_BOTON_DEF)
            cantidad = ceil_to_even((ancho * multiplicador) / paso)
        elif regla == "FIJO":
            cantidad = _safe_float(param, 0.0)
        else:
            st.error(f"ReglaCantidad '{regla}' no soportada.")
            st.stop()

        cantidad_total = cantidad * num_cortinas

        if nombre == "TELA 1":
            pvp = _safe_float(st.session_state.get("pvp_tela_1"), 0.0)
            ref = st.session_state.get("ref_tela_sel_1", "")
            color = st.session_state.get("color_tela_sel_1", "")
            nombre_mostrado = f"TELA 1: {ref} - {color}"
            uni = "MT"
        elif nombre == "TELA 2":
            pvp = _safe_float(st.session_state.get("pvp_tela_2"), 0.0)
            ref = st.session_state.get("ref_tela_sel_2", "")
            color = st.session_state.get("color_tela_sel_2", "")
            nombre_mostrado = f"TELA 2: {ref} - {color}"
            uni = "MT"
        elif nombre.startswith("M.O"):
            continue
        else:
            sel = st.session_state.get("insumos_seleccion", {}).get(item["Insumo"], {})
            pvp = _safe_float(sel.get("pvp"), 0.0)
            uni = sel.get("unidad", unidad)
            nombre_mostrado = item["Insumo"]

        precio_total = pvp * cantidad_total
        subtotal += precio_total

        detalle_insumos.append({
            "Insumo": nombre_mostrado,
            "Unidad": uni,
            "Cantidad": round(cantidad_total, 2) if uni != "UND" else int(round(cantidad_total)),
            "P.V.P/Unit ($)": pvp,
            "Precio ($)": round(precio_total),
        })

    mo_key_candidates = [f"M.O: {diseno}", f"M.O. {diseno}"]
    mo_info = None
    mo_key = None
    for k in mo_key_candidates:
        if k in PRECIOS_MANO_DE_OBRA:
            mo_key = k; mo_info = PRECIOS_MANO_DE_OBRA[k]; break
    if mo_info and _safe_float(mo_info.get("pvp"), 0) > 0:
        cant_mo = ancho * multiplicador * num_cortinas
        pvp_mo = _safe_float(mo_info["pvp"], 0.0)
        precio_mo = round(cant_mo * pvp_mo)
        subtotal += precio_mo
        detalle_insumos.append({
            "Insumo": mo_key,
            "Unidad": mo_info.get("unidad", "MT"),
            "Cantidad": round(cant_mo, 2),
            "P.V.P/Unit ($)": pvp_mo,
            "Precio ($)": precio_mo,
        })

    iva = round(subtotal * IVA_PERCENT)
    total = round(subtotal)
    subtotal_sin_iva = total - iva

    tela_info = {
        "tela1": {
            "tipo": st.session_state.get("tipo_tela_sel_1", ""),
            "referencia": st.session_state.get("ref_tela_sel_1", ""),
            "color": st.session_state.get("color_tela_sel_1", ""),
            "pvp": _safe_float(st.session_state.get("pvp_tela_1"), 0.0),
            "modo_confeccion": st.session_state.get("modo_conf_1", ""),
        }
    }
    if st.session_state.get("pvp_tela_2") is not None:
        tela_info["tela2"] = {
            "tipo": st.session_state.get("tipo_tela_sel_2", ""),
            "referencia": st.session_state.get("ref_tela_sel_2", ""),
            "color": st.session_state.get("color_tela_sel_2", ""),
            "pvp": _safe_float(st.session_state.get("pvp_tela_2"), 0.0),
            "modo_confeccion": st.session_state.get("modo_conf_2", ""),
        }
    else:
        tela_info["tela2"] = None

    selected_insumos_info = st.session_state.get("insumos_seleccion", {}).copy()

    st.session_state.cortina_calculada = {
        "tipo": st.session_state.tipo_cortina_sel,
        "diseno": diseno, "multiplicador": multiplicador, "ancho": ancho, "alto": alto,
        "cantidad": num_cortinas,
        "telas": tela_info,
        "insumos_seleccion": selected_insumos_info,
        "detalle_insumos": detalle_insumos, "subtotal": subtotal_sin_iva, "iva": iva, "total": total
    }

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
    cliente = st.session_state.datos_cotizacion['cliente']
    vendedor = st.session_state.datos_cotizacion['vendedor']
    if any(cliente.values()) or any(vendedor.values()):
        c1, c2 = st.columns(2)
        with c1:
            st.subheader("Cliente")
            st.text(f"Nombre: {cliente.get('nombre', 'N/A')}")
            st.text(f"Teléfono: {cliente.get('telefono', 'N/A')}")
            st.text(f"Correo: {cliente.get('correo', 'N/A')}")
        with c2:
            st.subheader("Vendedor")
            st.text(f"Nombre: {vendedor.get('nombre', 'N/A')}")
            st.text(f"Teléfono: {vendedor.get('telefono', 'N/A')}")

    st.subheader("Productos Añadidos")
    if not st.session_state.cortinas_resumen:
        st.info("Aún no has añadido ninguna cortina a la cotización.")
    else:
        if 'seleccion_resumen' not in st.session_state:
            st.session_state.seleccion_resumen = -1

        for i, cortina in enumerate(st.session_state.cortinas_resumen):
            with st.container(border=True):
                col_izq, col_cen, col_der, col_gear = st.columns([2, 3, 1.5, 0.5])
                
                ancho_calc = cortina['ancho'] * cortina['multiplicador']
                col_izq.markdown(f"**Dimensiones:** {ancho_calc:.2f} × {cortina['alto']:.2f} m")
                col_izq.markdown(f"**Cantidad:** {cortina['cantidad']} und")

                col_cen.markdown(f"**{cortina['diseno']}**")
                
                if cortina['telas']['tela1']:
                    tela1_info = cortina['telas']['tela1']
                    tela1_str = f"Tela 1: {tela1_info['referencia']} - {tela1_info['color']} **[{tela1_info['modo_confeccion']}]**"
                    col_cen.markdown(f"• {tela1_str}")
                
                if cortina['telas'].get('tela2') and cortina['telas']['tela2'].get('referencia'):
                    tela2_info = cortina['telas']['tela2']
                    tela2_str = f"Tela 2: {tela2_info['referencia']} - {tela2_info['color']} **[{tela2_info['modo_confeccion']}]**"
                    col_cen.markdown(f"• {tela2_str}")
                
                insumos_sel = cortina.get('insumos_seleccion', {})
                if insumos_sel:
                    for insumo, info in insumos_sel.items():
                        col_cen.markdown(f"• {insumo}: {info['ref']} - {info['color']}")

                col_der.markdown(f"**${int(cortina['total']):,}**")

                if col_gear.button('⚙️', key=f'select_btn_{i}'):
                    if st.session_state.seleccion_resumen == i:
                        st.session_state.seleccion_resumen = -1
                    else:
                        st.session_state.seleccion_resumen = i

                if st.session_state.seleccion_resumen == i:
                    st.markdown("---")
                    acc_col1, acc_col2, acc_col3 = st.columns([1,1,1])
                    if acc_col1.button('✏️ Editar', key=f'edit_btn_{i}', use_container_width=True):
                        st.session_state.cortina_a_editar = cortina
                        st.session_state.editando_index = i
                        st.session_state.pagina_actual = 'cotizador'
                        st.rerun()

                    if acc_col2.button('🗑️ Eliminar', key=f'delete_btn_{i}', use_container_width=True):
                        del st.session_state.cortinas_resumen[i]
                        st.session_state.seleccion_resumen = -1
                        st.rerun()

                    if acc_col3.button('📋 Duplicar', key=f'dup_btn_{i}', use_container_width=True):
                        duplicar_cortina(i)
                        st.session_state.seleccion_resumen = -1
                        st.rerun()
                
    total_final = sum(c['total'] for c in st.session_state.cortinas_resumen)
    iva = total_final * IVA_PERCENT
    subtotal = total_final - iva
    st.markdown("---")
    
    if st.session_state.cortinas_resumen:
        pdf_data = generar_pdf_cotizacion()
        st.download_button(
            label="Generar PDF de Cotización",
            data=pdf_data,
            file_name="cotizacion.pdf",
            mime="application/pdf",
            use_container_width=True
        )

    c1, c2, c3 = st.columns(3)
    c1.metric("Subtotal", f"${int(subtotal):,}")
    c2.metric(f"IVA ({IVA_PERCENT:.0%})", f"${int(iva):,}")
    c3.metric("Total Cotización", f"${int(total_final):,}")

# --- PANTALLA DE GESTIÓN DE DATOS ---
def create_template_excel(column_names: list, sheet_name: str = "Plantilla"):
    """
    Crea un archivo Excel en memoria con solo los encabezados de las columnas.
    Retorna los bytes del archivo para que Streamlit pueda descargarlo.
    """
    df = pd.DataFrame(columns=column_names)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine='xlsxwriter') as writer:
        df.to_excel(writer, index=False, sheet_name=sheet_name)
    buffer.seek(0)
    return buffer

def pantalla_gestion_datos():
    st.header("Gestión de Archivos de Datos")
    st.info("Utiliza los botones de abajo para descargar las plantillas de Excel.")
    
    if st.button("Recargar Datos del Repositorio", use_container_width=True):
        st.cache_data.clear()
        st.cache_resource.clear()
        st.rerun()
    
    st.markdown("---")

    st.subheader("1. Plantilla de Diseños")
    st.markdown("Columnas requeridas: `Diseño`, `Tipo`, `Multiplicador`, `PVP M.O.`")
    template_buffer_designs = create_template_excel(REQUIRED_DESIGNS_COLS, "Diseños")
    st.download_button(
        label="Descargar Plantilla de Diseños",
        data=template_buffer_designs,
        file_name="plantilla_disenos.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_designs_btn"
    )
    st.markdown("---")
    
    st.subheader("2. Plantilla de BOM")
    st.markdown("Columnas requeridas: `Diseño`, `Insumo`, `Unidad`, `ReglaCantidad`, `Parametro`, `DependeDeSeleccion`, `Observaciones`")
    template_buffer_bom = create_template_excel(REQUIRED_BOM_COLS, "BOM")
    st.download_button(
        label="Descargar Plantilla de BOM",
        data=template_buffer_bom,
        file_name="plantilla_bom.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_bom_btn"
    )
    st.markdown("---")
    
    st.subheader("3. Plantilla de Catálogo de Insumos")
    st.markdown("Columnas requeridas: `Insumo`, `Unidad`, `Ref`, `Color`, `PVP`")
    template_buffer_insumos = create_template_excel(REQUIRED_CAT_COLS, "Catalogo_Insumos")
    st.download_button(
        label="Descargar Plantilla de Insumos",
        data=template_buffer_insumos,
        file_name="plantilla_insumos.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_insumos_btn"
    )
    st.markdown("---")
    
    st.subheader("4. Plantilla de Catálogo de Telas")
    st.markdown("Columnas requeridas: `TipoTela`, `Referencia`, `Color`, `PVP/Metro ($)`")
    template_buffer_telas = create_template_excel(REQUIRED_TELAS_COLS, "Catalogo_Telas")
    st.download_button(
        label="Descargar Plantilla de Telas",
        data=template_buffer_telas,
        file_name="plantilla_telas.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        key="download_telas_btn"
    )

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















