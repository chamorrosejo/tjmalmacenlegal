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
    El nombre del archivo será: TipoDeTela - Referencia - Color.jpg
    """
    diseno = st.session_state.get("diseno_sel")
    tipo_tela = st.session_state.get(f"tipo_tela_sel_{tela_num}")
    ref = st.session_state.get(f"ref_tela_sel_{tela_num}")
    color = st.session_state.get(f"color_tela_sel_{tela_num}")

    if not all([diseno, tipo_tela, ref, color]):
        return os.path.join(SCRIPT_DIR, "imagenes", "placeholder.jpg")

    diseno_cleaned = diseno.replace(" ", "_").upper()
    tipo_tela_cleaned = tipo_tela.replace(" ", "_")
    ref_cleaned = ref.replace(" ", "_").replace(".", "")
    color_cleaned = color.replace(" ", "_")
    
    image_filename = f"{tipo_tela_cleaned} - {ref_cleaned} - {color_cleaned}.jpg"

    specific_image_path = os.path.join(
        SCRIPT_DIR, 
        "imagenes", 
        "cortinas",
        diseno_cleaned,
        tipo_tela,
        image_filename
    )

    if os.path.exists(specific_image_path):
        return specific_image_path
    else:
        return os.path.join(SCRIPT_DIR, "imagenes", "placeholder.jpg")

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
            "Insumo": str(row["Insumo"]).strip(), "Unidad": str(row["Unidad"]).strip().upper(),
            "ReglaCantidad": str(row["ReglaCantidad"]).strip().upper(), "Parametro": param_norm,
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
        if not catalog[insumo].get("unidad"): catalog[insumo]["unidad"] = unidad
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
        telas.setdefault(tipo, {}); telas[tipo].setdefault(ref, [])
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

# =======================
# App State & UI
# =======================
st.set_page_config(page_title="Almacén Legal Cotizador", page_icon="logo.png", layout="wide")

TABLA_DISENOS, TIPOS_CORTINA, PRECIOS_MANO_DE_OBRA, DISENOS_A_TIPOS, DF_DISENOS = load_designs_from_excel(DESIGNS_XLSX_PATH)
BOM_DICT, DF_BOM = load_bom_from_excel(BOM_XLSX_PATH)
CATALOGO_INSUMOS = load_catalog_from_excel(CATALOG_XLSX_PATH)
CATALOGO_TELAS = load_telas_from_excel(CATALOG_TELAS_XLSX_PATH)

# (Aquí van todas tus funciones como init_state, anadir_a_resumen, generar_pdf_cotizacion, etc., que no necesitan cambios)
# Para mantener este bloque legible, las omito, pero debes asegurarte de que estén en tu archivo.
# A continuación, pego la única función de UI que cambia: pantalla_cotizador.
# El resto de funciones (sidebar, pantalla_datos, etc.) no cambian.

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
    
# (Tu función `generar_pdf_cotizacion` y todas las de ayuda para el PDF van aquí)

def sidebar():
    with st.sidebar:
        st.image("logo.png"); st.title("Almacén Legal Cotizador")
        if st.button("Gestión de Datos", use_container_width=True): st.session_state.pagina_actual = 'gestion_datos'; st.rerun()
        st.divider()
        if st.button("Crear Cortina", use_container_width=True): st.session_state.editando_index = None; st.session_state.cortina_a_editar = None; st.session_state.pagina_actual = 'cotizador'; st.rerun()
        if st.button("Datos de la Cotización", use_container_width=True): st.session_state.pagina_actual = 'datos'; st.rerun()
        if st.button("Ver Cotización", use_container_width=True): st.session_state.pagina_actual = 'resumen'; st.rerun()

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
        if "placeholder.jpg" in caption: caption = "Vista previa no disponible"
        st.image(image_path, caption=caption, use_column_width=True)
    else:
        st.warning("No se encontró la imagen. Asegúrate que 'placeholder.jpg' exista en la carpeta 'imagenes'.")

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

# (Aquí deben ir el resto de tus funciones que no han cambiado: ui_tela, mostrar_insumos_bom, calcular_y_mostrar_cotizacion, pantalla_datos, pantalla_resumen, pantalla_gestion_datos)
# Asegúrate de copiarlas de tu archivo original.

def main():
    init_state()
    with st.sidebar:
        sidebar()
    page = st.session_state.pagina_actual
    if page == 'datos':
        # Asegúrate de tener la función pantalla_datos(
        pass
    elif page == 'resumen':
        # Asegúrate de tener la función pantalla_resumen()
        pass
    elif page == 'gestion_datos':
        # Asegúrate de tener la función pantalla_gestion_datos()
        pass
    else:
        pantalla_cotizador()

if __name__ == "__main__":
    main()
