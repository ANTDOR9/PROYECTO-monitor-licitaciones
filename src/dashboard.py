"""
Dashboard de Licitaciones - Brighter Peru
Ejecutar:  streamlit run src/dashboard.py

Tres "paginas" separadas por un selector de fuente (no por tabs, para que
sean fuentes claramente distintas con su propia metodologia):
  - SEACE / OECE  -> Vigentes + Historico
  - Peru Compras  -> Ordenes por Acuerdo Marco (Catalogos Electronicos)
  - PetroPeru     -> Avisos de Contratacion Futura (senal temprana)
"""
import os, sqlite3
import pandas as pd
import streamlit as st
import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def cfg():
    return yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))

def ruta_db():
    return os.path.join(BASE, cfg()["salida"]["base_datos"])

def leer(tabla):
    db = ruta_db()
    con = sqlite3.connect(db)
    try:
        df = pd.read_sql(f"SELECT * FROM {tabla}", con)
    except Exception:
        df = pd.DataFrame()
    con.close()
    for col in ("valor_referencial", "monto_referencial", "monto_adjudicado",
                "precio_unitario", "cantidad", "monto_total"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df

def selector_todos(key, etiqueta_singular="registros"):
    """Botones vistosos tipo 'segmented control' para alternar entre
    'Solo relevantes' y 'Ver todos'. Reemplaza el checkbox simple."""
    if key not in st.session_state:
        st.session_state[key] = False
    c1, c2 = st.columns(2)
    if c1.button("🏷️ Solo relevantes", key=f"{key}_rel",
                 type="secondary" if st.session_state[key] else "primary",
                 width="stretch"):
        st.session_state[key] = False
        st.rerun()
    if c2.button(f"👁️ Ver TODOS los {etiqueta_singular}", key=f"{key}_todos_btn",
                 type="primary" if st.session_state[key] else "secondary",
                 width="stretch"):
        st.session_state[key] = True
        st.rerun()
    return st.session_state[key]


def altura_tabla(n_filas, minimo=140, maximo=560, alto_fila=35, cabecera=38):
    """Altura dinamica: evita filas fantasma vacias cuando hay pocos
    registros, y limita el alto maximo cuando hay muchos (ahi sí aparece
    scroll vertical, ademas del horizontal)."""
    return int(min(maximo, max(minimo, cabecera + alto_fila * max(n_filas, 1))))

def export_button(df, nombre):
    if df.empty:
        return
    ruta = os.path.join(BASE, "data", nombre)
    if st.button(f"Exportar a Excel ({nombre})", key=nombre):
        df.to_excel(ruta, index=False)
        with open(ruta, "rb") as fh:
            st.download_button("Descargar Excel", fh, file_name=nombre, key="dl_"+nombre)

# ---- estilo "vigentes" tipo streamlit_app.py (colores por estado) ----
import re, unicodedata
from datetime import datetime

COLOR_ESTADO = {"Abierta": "#E1F5EE", "Cierra pronto": "#FAEEDA",
                "Vencida": "#FCEBEB", "Sin fecha": "#F1EFE8"}

def _norm(t):
    if not t: return ""
    t = unicodedata.normalize("NFKD", str(t))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()

def _separar(txt):
    return [x.strip() for x in re.split(r"[,\n]", txt) if x.strip()]

def estado_por_fecha(fecha_str):
    if not fecha_str:
        return "Sin fecha", None
    for fmt in ("%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y"):
        try:
            f = datetime.strptime(str(fecha_str).strip(), fmt)
            dias = (f.date() - datetime.now().date()).days
            if dias < 0: estado = "Vencida"
            elif dias <= 7: estado = "Cierra pronto"
            else: estado = "Abierta"
            return estado, dias
        except ValueError:
            continue
    return "Sin fecha", None

def _colorea(fila):
    c = COLOR_ESTADO.get(fila.get("Estado"), "")
    return [f"background-color: {c}" for _ in fila]

def etiqueta_de(texto, etiquetas, excluir):
    # palabra completa (\b), no subcadena -- mismo fix que extract.py/vigentes.py
    if any(x in texto for x in excluir): return None
    for et in etiquetas:
        toks = [t for t in _norm(et).split() if len(t) >= 4]
        if toks and all(re.search(rf"\b{re.escape(t)}(?:es|s)?\b", texto) for t in toks):
            return et
    return None

# ---- Busqueda avanzada (Etapa 1 -- SEACE Historico) ----
TAMANOS_PULGADAS = ["65", "75", "86", "98"]
RE_PULGADAS = re.compile(r'(\d{2,3})\s?(?:"|pulgadas)', re.I)

def extraer_pulgadas(texto):
    m = RE_PULGADAS.search(texto or "")
    return m.group(1) if m else None

# Los patrones aceptan plural. Sin la "s" opcional, "ADQUISICION DE PANTALLAS
# INTERACTIVAS" no coincidía con "pantalla\s+interactiv" y caía en "Otro":
# 40 de las 436 licitaciones del histórico estaban mal clasificadas por eso,
# que es más de la mitad de las interactivas reales.
CATEGORIAS_PRODUCTO = {
    "Pantalla interactiva": [r"pantallas?\s+(?:\w+\s+){0,2}interactiv",
                             r"pantallas?\s+tactil"],
    "Pizarra digital": [r"pizarras?\s+(?:digital|interactiv|electronic)"],
    "Panel interactivo": [r"(?:panel|paneles)\s+(?:\w+\s+){0,2}interactiv"],
    "Monitor interactivo": [r"monitor(?:es)?\s+(?:\w+\s+){0,2}interactiv"],
    "Pantalla LED (excluir)": [r"pantallas?\s+led", r"videos?\s*walls?", r"videowalls?"],
    "Kiosco/Totem (excluir)": [r"k?ioscos?", r"quioscos?", r"t[oó]tems?", r"totems?"],
}

def categoria_producto(texto):
    texto = texto or ""
    for cat, patrones in CATEGORIAS_PRODUCTO.items():
        if any(re.search(p, texto) for p in patrones):
            return cat
    return "Otro"

# PetroPeru guarda fechas tipo "17-Ago-2026" (mes en texto, español) --
# pd.to_datetime no las reconoce solo -- se parsean a mano.
MESES_ES = {"ene": 1, "feb": 2, "mar": 3, "abr": 4, "may": 5, "jun": 6,
            "jul": 7, "ago": 8, "set": 9, "sep": 9, "oct": 10, "nov": 11, "dic": 12}

def parsear_fecha_es(txt):
    m = re.match(r"(\d{1,2})[-\s]([A-Za-zÁÉÍÓÚáéíóú]{3,})[-\s](\d{4})", str(txt or ""))
    if not m:
        return pd.to_datetime(txt, errors="coerce", dayfirst=True)
    dia, mes_txt, anio = m.groups()
    mes = MESES_ES.get(_norm(mes_txt)[:3])
    if not mes:
        return pd.NaT
    try:
        return pd.Timestamp(int(anio), mes, int(dia))
    except ValueError:
        return pd.NaT

# ---- colores para la tabla de Peru Compras, segun Estado de Entrega ----
def _color_estado_entrega(estado):
    e = _norm(estado)
    if not e or "sin dato" in e:
        return "#F1EFE8"  # gris, sin info
    if any(x in e for x in ("vencida", "rechazada", "anulada", "no entregada")):
        return "#FCEBEB"  # rojo
    if "pendiente" in e:
        return "#FAEEDA"  # amarillo
    if "aceptada" in e or "conforme" in e or "entregada" in e:
        return "#E1F5EE"  # verde
    return "#F1EFE8"

def _colorea_perucompras(fila):
    c = _color_estado_entrega(fila.get("estado_entrega", ""))
    return [f"background-color: {c}" for _ in fila]

def _colorea_perucompras_render(fila):
    c = _color_estado_entrega(fila.get("Estado de entrega", ""))
    return [f"background-color: {c}" for _ in fila]

st.set_page_config(page_title="Monitor de Licitaciones - Brighter", layout="wide")

# Barra de scroll horizontal de las tablas mas gruesa y facil de agarrar
# (por defecto Streamlit/glide-data-grid la deja muy delgada).
st.markdown("""
<style>
[data-testid="stDataFrame"] div::-webkit-scrollbar {
    height: 16px !important;
    width: 16px !important;
}
[data-testid="stDataFrame"] div::-webkit-scrollbar-thumb {
    background: #8a8a8a !important;
    border-radius: 8px !important;
}
[data-testid="stDataFrame"] div::-webkit-scrollbar-track {
    background: #e6e6e6 !important;
}
/* Quinto botón en verde: el comparador no es otra fuente de datos, es la
   función propia del proyecto. El selector por clave existe desde Streamlit
   1.39; en versiones anteriores el botón funciona igual, solo que gris, y el
   círculo verde de la etiqueta lo sigue distinguiendo. */
.st-key-sel_comparador button {
    background-color: #1E8449 !important;
    border-color: #1E8449 !important;
    color: #FFFFFF !important;
}
.st-key-sel_comparador button:hover {
    background-color: #166B3A !important;
    border-color: #166B3A !important;
}
</style>
""", unsafe_allow_html=True)

if "vista" not in st.session_state:
    st.session_state.vista = "seace"

# ================================================================
# VISTA: PERU COMPRAS
# ================================================================
PERUCOMPRAS_PORTAL = "https://catalogos.perucompras.gob.pe/ConsultaOrdenesPub/"

def vista_perucompras():
    st.title("🛒 Perú Compras — Compras ya ejecutadas por Catálogo")
    st.caption(
        "⚠️ Esto NO son oportunidades abiertas para postular (a diferencia de SEACE). "
        "Son compras DIRECTAS que el Estado ya realizó por Acuerdo Marco: los proveedores "
        "ya fueron seleccionados antes, así que aquí no hay plazos ni competencia por orden. "
        "Úsalo como inteligencia de mercado: qué compra el Estado, a quién, y por cuánto. "
        "Fuente: catalogos.perucompras.gob.pe"
    )

    df = leer("perucompras")
    if df.empty:
        st.warning("Sin datos de Perú Compras. Corre:  python src/perucompras.py")
        return

    st.sidebar.header("Filtros - Perú Compras")
    categorias = sorted(x for x in df["categoria"].dropna().unique() if x)
    sc = st.sidebar.multiselect("Categoría (Acuerdo Marco)", categorias, key="pc_cat")
    vmin = st.sidebar.number_input("Monto total mínimo (S/)", 0, step=1000, value=0, key="pc_min")
    txt = st.sidebar.text_input("Buscar en proveedor/entidad", key="pc_txt")

    f = df.copy()
    if sc: f = f[f["categoria"].isin(sc)]
    if vmin: f = f[f["monto_total"].fillna(0) >= vmin]
    if txt:
        t = txt.lower()
        f = f[f["proveedor"].str.lower().str.contains(t, na=False) |
              f["entidad"].str.lower().str.contains(t, na=False)]

    hcol1, hcol2 = st.columns([3, 1])
    hcol1.write("")
    if "pc_avanzado" not in st.session_state:
        st.session_state.pc_avanzado = False
    if hcol2.button("🔍 Búsqueda avanzada", key="btn_pc_avanzado", width="stretch"):
        st.session_state.pc_avanzado = not st.session_state.pc_avanzado

    total_antes_avanzado = len(f)
    if st.session_state.pc_avanzado:
        with st.expander("Filtros avanzados - Perú Compras", expanded=True):
            opciones_prov = sorted(set(f["proveedor"].dropna()))
            sel_prov = st.multiselect("Proveedor", opciones_prov, key="pc_prov_av")

            pc1, pc2 = st.columns(2)
            p_max = pc1.number_input("Monto total máximo (S/, 0 = sin tope)", min_value=0, value=0, step=1000, key="pc_pmax")

            fc1, fc2 = st.columns(2)
            fechas_validas = pd.to_datetime(f["fecha_aceptacion"], errors="coerce").dropna()
            f_min_def = fechas_validas.min().date() if not fechas_validas.empty else None
            f_max_def = fechas_validas.max().date() if not fechas_validas.empty else None
            f_ini = fc1.date_input("Desde", value=f_min_def, key="pc_fini") if f_min_def else None
            f_fin = fc2.date_input("Hasta", value=f_max_def, key="pc_ffin") if f_max_def else None

            if sel_prov:
                f = f[f["proveedor"].isin(sel_prov)]
            if p_max:
                f = f[f["monto_total"].fillna(0) <= p_max]
            if f_ini and f_fin:
                fechas_f = pd.to_datetime(f["fecha_aceptacion"], errors="coerce")
                f = f[(fechas_f.dt.date >= f_ini) & (fechas_f.dt.date <= f_fin)]

    st.caption(f"Mostrando **{len(f)} de {total_antes_avanzado}** órdenes (de {len(df)} totales).")

    c1, c2, c3 = st.columns(3)
    c1.metric("Órdenes encontradas", len(f))
    c2.metric("Monto total", f"S/ {f['monto_total'].fillna(0).sum():,.0f}")
    c3.metric("Entidades compradoras", f["entidad"].nunique())

    tabla_pc = f.rename(columns={
        "categoria": "Categoría (Acuerdo Marco)", "tipo_contratacion": "Tipo de contratación",
        "orden_compra": "N° Orden de Compra",
        "proveedor": "Proveedor", "entidad": "Entidad compradora",
        "monto_total": "Monto total (S/)", "estado_entrega": "Estado de entrega",
        "fecha_aceptacion": "Fecha aceptación", "lugar_entrega": "Lugar de entrega",
    })
    cols_pc = ["Categoría (Acuerdo Marco)", "Tipo de contratación", "N° Orden de Compra", "Proveedor",
               "Entidad compradora", "Monto total (S/)", "Estado de entrega", "Fecha aceptación",
               "Lugar de entrega"]
    cols_pc = [c for c in cols_pc if c in tabla_pc.columns]
    tabla_pc = tabla_pc[cols_pc].sort_values("Fecha aceptación", ascending=False)
    st.dataframe(
        tabla_pc.style.apply(_colorea_perucompras_render, axis=1),
        width="stretch", hide_index=True, height=altura_tabla(len(tabla_pc)),
    )
    st.caption("🟢 Aceptada/Entregada  🟡 Pendiente  🔴 Vencida/Rechazada/Anulada  ⬜ Sin dato")
    st.info(f"🔎 Para ver el detalle de una orden: copia su **N° Orden de Compra** y búscalo en el "
            f"[buscador público de Perú Compras]({PERUCOMPRAS_PORTAL}) "
            f"(el sitio no permite enlazar directo a una orden especifica).")
    export_button(f, "perucompras_export.xlsx")

# ================================================================
# VISTA: SEACE / OECE (la de siempre)
# ================================================================
def vista_seace():
    st.title("📡 Tracker de Licitaciones — Brighter Perú")
    st.caption("Pantallas interactivas, pizarras digitales, kioscos y equipamiento audiovisual. Fuente: OECE / SEACE.")

    tab1, tab2 = st.tabs(["Oportunidades VIGENTES (postular ahora)", "Historico de adjudicaciones"])

    with tab1:
        df = leer("vigentes")
        if df.empty:
            st.warning("Sin datos vigentes. Corre:  python src/vigentes.py")
        else:
            claves_cfg = cfg().get("palabras_clave", [])
            if isinstance(claves_cfg, dict):
                claves_cfg = [t for terms in claves_cfg.values() for t in terms]
            excluir_cfg = cfg().get("palabras_excluir", [])

            st.sidebar.header("🏷️ Etiquetas de búsqueda")
            st.sidebar.caption("Separa por coma o por línea. Edita para investigar otros productos.")
            txt_etiquetas = st.sidebar.text_area("Etiquetas de interés",
                value="\n".join(claves_cfg), height=180, key="v_etq")
            txt_excluir = st.sidebar.text_area("Excluir (una por línea)",
                value="\n".join(excluir_cfg), height=90, key="v_exc")
            etiquetas = _separar(txt_etiquetas)
            excluir = [_norm(e) for e in _separar(txt_excluir)]

            df = df.copy()
            df["_texto"] = (df["objeto"].fillna("") + " " + df["descripcion"].fillna("") + " " +
                             df["nomenclatura"].fillna("") + " " + df["entidad"].fillna("")).apply(_norm)
            df["Etiqueta"] = df["_texto"].apply(lambda t: etiqueta_de(t, etiquetas, excluir))
            rel = df[df["Etiqueta"].notna()].drop(columns=["_texto"])

            est = rel["fecha_fin_inscripcion"].apply(estado_por_fecha)
            rel = rel.assign(Estado=[e[0] for e in est], **{"Dias restantes": [e[1] for e in est]})

            st.sidebar.divider()
            st.sidebar.header("Filtros")
            vmin = st.sidebar.number_input("Valor referencial minimo (S/)", 0, step=1000, value=0, key="v_min")
            txt = st.sidebar.text_input("Buscar en descripcion/entidad", key="v_txt")

            f = rel.copy()
            if vmin: f = f[f["valor_referencial"].fillna(0) >= vmin]
            if txt:
                t = txt.lower()
                f = f[f["descripcion"].str.lower().str.contains(t, na=False) |
                      f["entidad"].str.lower().str.contains(t, na=False)]

            c1, c2, c3 = st.columns(3)
            c1.metric("Oportunidades encontradas", len(f))
            c2.metric("Etiquetas activas", len(etiquetas))
            c3.metric("Procesos revisados", f"{len(df):,}")

            if f.empty:
                st.warning("Ninguna oportunidad vigente coincide con las etiquetas actuales. "
                           "Prueba agregando o cambiando términos en el panel de la izquierda.")
            else:
                st.divider()
                cols = ["Estado", "Dias restantes", "Etiqueta", "tipo_proceso", "nomenclatura", "entidad", "objeto",
                        "descripcion", "valor_referencial", "fecha_fin_inscripcion",
                        "fecha_presentacion", "enlace"]
                cols = [c for c in cols if c in f.columns]
                tabla = f[cols].rename(columns={
                    "tipo_proceso": "Tipo de licitación",
                    "nomenclatura": "Nomenclatura", "entidad": "Entidad", "objeto": "Objeto",
                    "descripcion": "Descripcion", "valor_referencial": "Valor referencial",
                    "fecha_fin_inscripcion": "Fin inscripcion", "fecha_presentacion": "Presentacion propuestas",
                    "enlace": "Ver en SEACE",
                }).sort_values("Dias restantes", na_position="last")
                st.dataframe(
                    tabla.style.apply(_colorea, axis=1),
                    width="stretch", hide_index=True, height=altura_tabla(len(tabla)),
                    column_config={"Ver en SEACE": st.column_config.LinkColumn("Ver en SEACE", display_text="Abrir portal")},
                )
                with st.expander("Resumen por etiqueta"):
                    st.dataframe(f.groupby("Etiqueta").size().reset_index(name="Oportunidades"),
                                 width="stretch", hide_index=True)
                export_button(f, "vigentes_export.xlsx")

    with tab2:
        df = leer("licitaciones")
        if df.empty:
            st.info("Sin historico. Corre:  python src/extract.py")
        else:
            df = df.copy()
            df["_texto_busq"] = (df["objeto"].fillna("") + " " + df["nomenclatura"].fillna("")).apply(_norm)
            df["_pulgadas"] = df["_texto_busq"].apply(extraer_pulgadas)
            df["_categoria_prod"] = df["_texto_busq"].apply(categoria_producto)

            hcol1, hcol2 = st.columns([3, 1])
            hcol1.subheader("Historico de adjudicaciones")
            if "hist_avanzado" not in st.session_state:
                st.session_state.hist_avanzado = False
            if hcol2.button("🔍 Búsqueda histórico avanzado", key="btn_hist_avanzado", width="stretch"):
                st.session_state.hist_avanzado = not st.session_state.hist_avanzado

            f = df

            if st.session_state.hist_avanzado:
                with st.expander("Filtros avanzados de histórico", expanded=True):
                    opciones_pulg = TAMANOS_PULGADAS + ["Otro/no especificado"]
                    sel_pulg = st.multiselect("Tamaño de pantalla (pulgadas)", opciones_pulg, key="h_pulg")

                    sel_cat = st.multiselect("Categoría de producto", list(CATEGORIAS_PRODUCTO.keys()) + ["Otro"], key="h_cat")

                    pc1, pc2 = st.columns(2)
                    p_min = pc1.number_input("Precio unitario mínimo (S/)", min_value=0, value=0, step=500, key="h_pmin")
                    p_max = pc2.number_input("Precio unitario máximo (S/, 0 = sin tope)", min_value=0, value=0, step=500, key="h_pmax")

                    opciones_prov = sorted(set(f["proveedor_ganador"].dropna()) | set(f["marca_detectada"].dropna()))
                    sel_prov = st.multiselect("Proveedor / marca ganadora", opciones_prov, key="h_prov")

                    fc1, fc2 = st.columns(2)
                    fechas_validas = pd.to_datetime(f["fecha"], errors="coerce").dropna()
                    f_min_def = fechas_validas.min().date() if not fechas_validas.empty else None
                    f_max_def = fechas_validas.max().date() if not fechas_validas.empty else None
                    f_ini = fc1.date_input("Desde", value=f_min_def, key="h_fini") if f_min_def else None
                    f_fin = fc2.date_input("Hasta", value=f_max_def, key="h_ffin") if f_max_def else None

                    sel_depto = st.multiselect("Departamento / región",
                        sorted(x for x in f["departamento"].dropna().unique() if x), key="h_depto")

                    # -- aplicar todo en AND, sobre el resultado que ya trae "f" --
                    if sel_pulg:
                        con_otro = "Otro/no especificado" in sel_pulg
                        nums = [p for p in sel_pulg if p != "Otro/no especificado"]
                        mask = f["_pulgadas"].isin(nums)
                        if con_otro:
                            mask = mask | f["_pulgadas"].isna()
                        f = f[mask]
                    if sel_cat:
                        f = f[f["_categoria_prod"].isin(sel_cat)]
                    if p_min:
                        f = f[f["precio_unitario"].fillna(0) >= p_min]
                    if p_max:
                        f = f[f["precio_unitario"].fillna(0) <= p_max]
                    if sel_prov:
                        f = f[f["proveedor_ganador"].isin(sel_prov) | f["marca_detectada"].isin(sel_prov)]
                    if f_ini and f_fin:
                        fechas_f = pd.to_datetime(f["fecha"], errors="coerce")
                        f = f[(fechas_f.dt.date >= f_ini) & (fechas_f.dt.date <= f_fin)]
                    if sel_depto:
                        f = f[f["departamento"].isin(sel_depto)]

            st.caption(f"Mostrando **{len(f)} de {len(df)}** procesos.")

            cols_mostrar = [c for c in df.columns if not c.startswith("_")]
            st.dataframe(
                f[cols_mostrar].sort_values("fecha", ascending=False),
                width="stretch", hide_index=True, height=altura_tabla(len(f)),
                column_config={"enlace": st.column_config.LinkColumn(
                    "Ficha SEACE", display_text="Ver proceso")},
            )
            c1, c2 = st.columns(2)
            c1.metric("Licitaciones", len(f))
            c2.metric("Monto adjudicado total", f"S/ {f['monto_adjudicado'].fillna(0).sum():,.0f}")
            export_button(f[cols_mostrar], "historico_export.xlsx")

            st.caption("La comparación contra BTOUCH está ahora en el botón "
                       "**Comparador BTOUCH**, arriba.")

# ================================================================
# VISTA: PETROPERU (avisos de contratacion futura -- señal temprana)
# ================================================================
PETROPERU_PORTAL = "https://convocatorias.petroperu.com.pe/avisos-de-contratacion-futura"

def vista_petroperu():
    st.title("🛢️ PetroPerú — Avisos de Contratación Futura")
    st.caption(
        "⚠️ Estos son avisos TEMPRANOS, antes de que exista un proceso formal "
        "(no pasan por SEACE, PetroPerú tiene régimen propio de contratación). "
        "Sirven para anticiparse: cuando un aviso madura, PetroPerú suele abrir "
        "el proceso en su propio portal de proveedores (acceso restringido). "
        f"Fuente pública: {PETROPERU_PORTAL}"
    )

    df = leer("petroperu")
    if df.empty:
        st.warning("Sin datos de PetroPerú. Corre:  python src/petroperu.py")
        return

    ver_todos = selector_todos("pp_todos", "avisos")
    st.sidebar.header("Filtros - PetroPerú")
    txt = st.sidebar.text_input("Buscar en descripción", key="pp_txt")

    f = df if ver_todos else df[df["categoria"] != ""]
    f = f.copy()
    if txt:
        f = f[f["descripcion"].str.lower().str.contains(txt.lower(), na=False)]
    if ver_todos:
        st.caption(f"👁️ Mostrando los {len(df)} avisos totales (sin filtrar por etiqueta).")

    f["_texto_busq"] = f["descripcion"].fillna("").apply(_norm)
    f["_pulgadas"] = f["_texto_busq"].apply(extraer_pulgadas)
    f["_categoria_prod"] = f["_texto_busq"].apply(categoria_producto)

    hcol1, hcol2 = st.columns([3, 1])
    if "pp_avanzado" not in st.session_state:
        st.session_state.pp_avanzado = False
    if hcol2.button("🔍 Búsqueda avanzada", key="btn_pp_avanzado", width="stretch"):
        st.session_state.pp_avanzado = not st.session_state.pp_avanzado

    total_antes_avanzado = len(f)
    if st.session_state.pp_avanzado:
        with st.expander("Filtros avanzados - PetroPerú", expanded=True):
            opciones_pulg = TAMANOS_PULGADAS + ["Otro/no especificado"]
            sel_pulg = st.multiselect("Tamaño de pantalla (pulgadas)", opciones_pulg, key="pp_pulg")

            sel_cat = st.multiselect("Categoría de producto", list(CATEGORIAS_PRODUCTO.keys()) + ["Otro"], key="pp_cat_av")

            fc1, fc2 = st.columns(2)
            fechas_validas = pd.to_datetime(f["fecha"].apply(parsear_fecha_es), errors="coerce").dropna()
            f_min_def = fechas_validas.min().date() if not fechas_validas.empty else None
            f_max_def = fechas_validas.max().date() if not fechas_validas.empty else None
            f_ini = fc1.date_input("Desde", value=f_min_def, key="pp_fini") if f_min_def else None
            f_fin = fc2.date_input("Hasta", value=f_max_def, key="pp_ffin") if f_max_def else None

            if sel_pulg:
                con_otro = "Otro/no especificado" in sel_pulg
                nums = [p for p in sel_pulg if p != "Otro/no especificado"]
                mask = f["_pulgadas"].isin(nums)
                if con_otro:
                    mask = mask | f["_pulgadas"].isna()
                f = f[mask]
            if sel_cat:
                f = f[f["_categoria_prod"].isin(sel_cat)]
            if f_ini and f_fin:
                fechas_f = pd.to_datetime(f["fecha"].apply(parsear_fecha_es), errors="coerce")
                f = f[(fechas_f.dt.date >= f_ini) & (fechas_f.dt.date <= f_fin)]

    st.caption(f"Mostrando **{len(f)} de {total_antes_avanzado}** avisos.")

    c1, c2 = st.columns(2)
    c1.metric("Avisos relevantes", len(f))
    c2.metric("Categorías distintas", f["categoria"].nunique())

    f = f[[c for c in f.columns if not c.startswith("_")]]
    tabla_pp = f.rename(columns={
        "codigo": "Código de proceso", "numero": "N° Aviso", "fecha": "Fecha publicación",
        "descripcion": "Descripción", "categoria": "Etiqueta", "pdf": "Documento",
    })
    cols_pp = ["Código de proceso", "N° Aviso", "Fecha publicación", "Descripción", "Etiqueta", "Documento"]
    cols_pp = [c for c in cols_pp if c in tabla_pp.columns]
    st.dataframe(
        tabla_pp[cols_pp].sort_values("Fecha publicación", ascending=False),
        width="stretch", hide_index=True, height=altura_tabla(len(tabla_pp)),
        column_config={"Documento": st.column_config.LinkColumn("Documento", display_text="Ver PDF")},
    )
    export_button(f, "petroperu_export.xlsx")

# ================================================================
# VISTA: BANCO DE LA NACION (bases de licitaciones/concursos)
# ================================================================
BNACION_PORTAL = "https://www.bn.com.pe/transparenciabn/publicacion-bases.asp"

def vista_bnacion():
    st.title("🏦 Banco de la Nación — Bases de Licitaciones y Concursos")
    st.caption(
        "⚠️ Igual que PetroPerú: régimen propio, no pasa por SEACE. Estas son "
        "bases YA publicadas (procesos formales, no solo señal temprana). "
        f"Fuente pública: {BNACION_PORTAL}"
    )

    df = leer("bnacion")
    if df.empty:
        st.warning("Sin datos del Banco de la Nación. Corre:  python src/bnacion.py")
        return

    ver_todos = selector_todos("bn_todos", "procesos")
    st.sidebar.header("Filtros - Banco de la Nación")
    txt = st.sidebar.text_input("Buscar en objeto", key="bn_txt")

    f = df if ver_todos else df[df["categoria"] != ""]
    f = f.copy()
    if txt:
        f = f[f["objeto"].str.lower().str.contains(txt.lower(), na=False)]
    if ver_todos:
        st.caption(f"👁️ Mostrando los {len(df)} procesos totales (sin filtrar por etiqueta).")

    f["_texto_busq"] = f["objeto"].fillna("").apply(_norm)
    f["_pulgadas"] = f["_texto_busq"].apply(extraer_pulgadas)
    f["_categoria_prod"] = f["_texto_busq"].apply(categoria_producto)

    hcol1, hcol2 = st.columns([3, 1])
    if "bn_avanzado" not in st.session_state:
        st.session_state.bn_avanzado = False
    if hcol2.button("🔍 Búsqueda avanzada", key="btn_bn_avanzado", width="stretch"):
        st.session_state.bn_avanzado = not st.session_state.bn_avanzado

    total_antes_avanzado = len(f)
    if st.session_state.bn_avanzado:
        with st.expander("Filtros avanzados - Banco de la Nación", expanded=True):
            opciones_pulg = TAMANOS_PULGADAS + ["Otro/no especificado"]
            sel_pulg = st.multiselect("Tamaño de pantalla (pulgadas)", opciones_pulg, key="bn_pulg")

            sel_cat = st.multiselect("Categoría de producto", list(CATEGORIAS_PRODUCTO.keys()) + ["Otro"], key="bn_cat_av")

            sel_tipo = st.multiselect("Tipo de proceso",
                sorted(x for x in f["tipo"].dropna().unique() if x), key="bn_tipo_av")

            fc1, fc2 = st.columns(2)
            fechas_validas = pd.to_datetime(f["fecha"], errors="coerce", dayfirst=True).dropna()
            f_min_def = fechas_validas.min().date() if not fechas_validas.empty else None
            f_max_def = fechas_validas.max().date() if not fechas_validas.empty else None
            f_ini = fc1.date_input("Desde", value=f_min_def, key="bn_fini") if f_min_def else None
            f_fin = fc2.date_input("Hasta", value=f_max_def, key="bn_ffin") if f_max_def else None

            if sel_pulg:
                con_otro = "Otro/no especificado" in sel_pulg
                nums = [p for p in sel_pulg if p != "Otro/no especificado"]
                mask = f["_pulgadas"].isin(nums)
                if con_otro:
                    mask = mask | f["_pulgadas"].isna()
                f = f[mask]
            if sel_cat:
                f = f[f["_categoria_prod"].isin(sel_cat)]
            if sel_tipo:
                f = f[f["tipo"].isin(sel_tipo)]
            if f_ini and f_fin:
                fechas_f = pd.to_datetime(f["fecha"], errors="coerce", dayfirst=True)
                f = f[(fechas_f.dt.date >= f_ini) & (fechas_f.dt.date <= f_fin)]

    st.caption(f"Mostrando **{len(f)} de {total_antes_avanzado}** procesos.")

    c1, c2 = st.columns(2)
    c1.metric("Procesos relevantes", len(f))
    c2.metric("Tipos distintos", f["tipo"].nunique())

    f = f[[c for c in f.columns if not c.startswith("_")]]
    tabla_bn = f.rename(columns={
        "tipo": "Tipo de proceso", "numero": "N°", "anio": "Año", "fecha": "Fecha bases",
        "objeto": "Objeto", "categoria": "Etiqueta", "enlace": "Documento",
    })
    cols_bn = ["Tipo de proceso", "N°", "Año", "Fecha bases", "Objeto", "Etiqueta", "Documento"]
    cols_bn = [c for c in cols_bn if c in tabla_bn.columns]
    st.dataframe(
        tabla_bn[cols_bn].sort_values("Fecha bases", ascending=False),
        width="stretch", hide_index=True, height=altura_tabla(len(tabla_bn)),
        column_config={"Documento": st.column_config.LinkColumn("Documento", display_text="Ver bases (PDF)")},
    )
    export_button(f, "bnacion_export.xlsx")

# ================================================================
# ROUTER -- selector de fuente (se repite arriba en cada vista)
# ================================================================
# ================================================================
# VISTA: COMPARADOR BTOUCH
# ================================================================
# Novedades del dia.
#
# El comparador trabaja sobre `licitaciones`, que sale de los paquetes anuales
# del OCDS y llega con alrededor de dos semanas de retraso: un filtro de "hoy"
# sobre esa tabla devuelve cero todos los dias. Lo que si esta al dia es
# `vigentes`, que viene de la API en vivo del SEACE y la Action refresca a
# diario. Por eso se muestran las dos cosas juntas y separadas: la novedad
# avisa que el proceso existe, el historico es el que se puede analizar.
CATS_INTERACTIVAS = ("Pantalla interactiva", "Pizarra digital",
                     "Panel interactivo", "Monitor interactivo")

VENTANAS = {"Hoy": 1, "Ultimos 3 dias": 3, "Ultimos 7 dias": 7,
            "Ultimos 15 dias": 15, "Ultimos 30 dias": 30, "Todo": None}


def _idprocesos_indexados(db, pe):
    """idProceso que ya tienen documentos descargables indexados."""
    try:
        con = sqlite3.connect(f"file:{pe.ruta_analisis(db)}?mode=ro", uri=True)
        ids = {str(r[0]) for r in con.execute(
            "select distinct idproceso from documentos where idproceso is not null")}
        con.close()
        return ids
    except sqlite3.Error:
        return set()


def _aviso_retraso(db):
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        ultima = con.execute("select max(fecha) from licitaciones").fetchone()[0]
        con.close()
    except sqlite3.Error:
        ultima = None
    if not ultima:
        return ("Las convocatorias salen de la API en vivo del SEACE; los documentos "
                "descargables, de los paquetes del OCDS, que llegan con retraso.")
    dias = (pd.Timestamp.now().normalize() - pd.Timestamp(ultima)).days
    return (f"Las convocatorias de arriba salen de la API en vivo del SEACE y estan al "
            f"dia. Los documentos descargables vienen de los paquetes del OCDS, cuyo "
            f"proceso mas reciente es del {ultima} ({dias} dias de retraso): por eso una "
            f"convocatoria nueva aparece aca antes de poder analizarse. Para acortar la "
            f"espera: python src/extract.py --refrescar y despues "
            f"python src/indexar_documentos.py")


def novedades_del_dia(db, pe):
    """Lo convocado recien, con el estado de si ya se puede comparar o no."""
    st.subheader("Novedades — lo que se convoco recien")
    df = leer("vigentes")
    if df.empty:
        st.warning("Sin datos de oportunidades vigentes. Corre:  python src/vigentes.py")
        return

    df = df.copy()
    df["_texto"] = (df["objeto"].fillna("") + " " + df["descripcion"].fillna("") + " "
                    + df["nomenclatura"].fillna("")).apply(_norm)
    df["Producto"] = df["_texto"].apply(categoria_producto)

    # Por defecto solo pantalla o pizarra interactiva. La casilla amplia a lo
    # que el proyecto etiqueta como del rubro (videowall, televisores,
    # equipamiento audiovisual): ahi aparecen convocatorias genericas de
    # "equipamiento audiovisual" que pueden incluir paneles en su lista de
    # items sin decirlo en el titulo. Son mas ruidosas, pero esconderlas
    # siempre es peor que mostrarlas aparte.
    ancho = st.checkbox("Incluir audiovisual relacionado (videowall, televisores, "
                        "equipamiento)", key="cmp_ancho")
    if ancho:
        claves = cfg().get("palabras_clave", [])
        if isinstance(claves, dict):
            claves = [t for v in claves.values() for t in v]
        excluir = [_norm(x) for x in cfg().get("palabras_excluir", [])]
        df = df[df["_texto"].apply(
            lambda t: etiqueta_de(t, claves, excluir) is not None)].copy()
    else:
        df = df[df["Producto"].isin(CATS_INTERACTIVAS)].copy()
    df["_conv"] = pd.to_datetime(df["fecha_convocatoria"], errors="coerce", dayfirst=True)

    etiqueta = st.radio("Convocadas en", list(VENTANAS), index=4, horizontal=True,
                        key="cmp_ventana")
    dias = VENTANAS[etiqueta]
    hoy = pd.Timestamp.now().normalize()
    f = df if dias is None else df[df["_conv"] >= hoy - pd.Timedelta(days=dias - 1)]
    f = f.copy()

    # La API trae el codigo de las bases en `documento_base`, asi que una
    # convocatoria de hoy ya se puede leer hoy. Solo queda sin analizar la que
    # no trae codigo. La columna puede faltar si todavia no se volvio a correr
    # vigentes.py despues de la actualizacion.
    ids = _idprocesos_indexados(db, pe)
    if "documento_base" not in f.columns:
        f["documento_base"] = ""
    f["documento_base"] = f["documento_base"].fillna("")

    def _estado(r):
        if str(r["id"]) in ids:
            return "expediente indexado"
        return "bases disponibles" if r["documento_base"] else "sin documento"

    f["Estado"] = f.apply(_estado, axis=1) if len(f) else []

    k1, k2, k3 = st.columns(3)
    k1.metric("Convocadas hoy", int((df["_conv"] >= hoy).sum()))
    k2.metric(f"En la ventana ({etiqueta.lower()})", len(f))
    k3.metric("Analizables ahora",
              int((f["Estado"] != "sin documento").sum()) if len(f) else 0)

    if not len(f):
        st.info("Ninguna convocatoria del rubro en esa ventana. Es lo esperable: el "
                "historico promedia dos procesos de pantalla interactiva al mes en "
                "todo el pais. Ampliá la ventana o marcá la casilla de audiovisual "
                "relacionado.")
    else:
        vista = (f.sort_values("_conv", ascending=False)
                  [["_conv", "Estado", "Producto", "nomenclatura", "entidad",
                    "descripcion", "fecha_presentacion", "enlace"]]
                  .rename(columns={"_conv": "Convocatoria", "nomenclatura": "Nomenclatura",
                                   "entidad": "Entidad", "descripcion": "Objeto",
                                   "fecha_presentacion": "Presentacion de ofertas",
                                   "enlace": "Ficha SEACE"}))
        st.dataframe(vista, width="stretch", hide_index=True,
                     height=altura_tabla(len(vista), maximo=380),
                     column_config={
                         "Convocatoria": st.column_config.DatetimeColumn(format="DD/MM/YYYY"),
                         "Ficha SEACE": st.column_config.LinkColumn(display_text="abrir"),
                     })

        analizables = f[f["Estado"] != "sin documento"].sort_values(
            "_conv", ascending=False).reset_index(drop=True)
        if len(analizables):
            st.markdown("**Analizar una convocatoria**")
            st.caption("Descarga las bases por su codigo y corre la comparacion "
                       "contra las fichas, sin esperar al OCDS.")

            def _etq(i):
                r = analizables.loc[i]
                return (f"{str(r['_conv'])[:10]} - {str(r['nomenclatura'])[:26]} - "
                        f"{str(r['entidad'])[:40]}")

            j = st.selectbox("Convocatoria", range(len(analizables)),
                             format_func=_etq, key="cmp_vig_sel")
            pe.render_convocatoria(analizables.loc[j].to_dict())
    st.caption(_aviso_retraso(db))


def vista_comparador():
    import panel_expediente as pe

    st.title("🟢 Comparador BTOUCH")
    st.caption(
        "Toma lo que una entidad EXIGIO en sus bases y lo compara, campo por campo, "
        "contra las fichas de BTOUCH y de la competencia. Responde si BTOUCH podia "
        "presentarse a ese proceso y con que modelo, y que marcas quedaban fuera por "
        "las especificaciones pedidas."
    )

    db = ruta_db()
    novedades_del_dia(db, pe)
    st.divider()

    st.subheader("Historico comparable")
    st.caption("Procesos cuyos documentos ya estan indexados: aca se corre la "
               "comparacion campo por campo contra las fichas.")
    if not pe.hay_indice(db):
        st.warning("Todavia no hay documentos indexados.")
        st.code("python src/extract.py --refrescar\npython src/indexar_documentos.py",
                language="bash")
        return

    # Las dos tablas viven en bases distintas: licitaciones.db se versiona y
    # analisis.db no. Se adjunta la de analisis para poder cruzarlas.
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    analisis = pe.ruta_analisis(db)
    if analisis != str(db):
        con.execute(f"ATTACH DATABASE 'file:{analisis}?mode=ro' AS an")
        fuente = "an.documentos"
    else:
        fuente = "documentos"
    procesos = pd.read_sql(
        f"SELECT l.*, d.categoria AS cat_doc, COUNT(*) AS n_docs "
        f"FROM {fuente} d JOIN licitaciones l ON l.ocid = d.ocid "
        f"GROUP BY l.ocid ORDER BY l.fecha DESC", con)
    con.close()

    if procesos.empty:
        st.info("Ningun proceso del historico tiene documentos indexados todavia.")
        return

    cats = sorted(x for x in procesos["cat_doc"].dropna().unique() if x)
    por_defecto = ["INTERACTIVA"] if "INTERACTIVA" in cats else []
    c1, c2 = st.columns([2, 3])
    sel_cat = c1.multiselect("Categoria de producto", cats, default=por_defecto,
                             key="cmp_cat",
                             help="Por defecto solo paneles interactivos, que es "
                                  "donde compite BTOUCH.")
    txt = c2.text_input("Buscar en entidad, nomenclatura u objeto", key="cmp_txt")

    f = procesos
    if sel_cat:
        f = f[f["cat_doc"].isin(sel_cat)]
    if txt:
        campos = (f["entidad"].fillna("") + " " + f["nomenclatura"].fillna("")
                  + " " + f["objeto"].fillna("")).str.lower()
        f = f[campos.str.contains(txt.lower(), regex=False)]

    k1, k2 = st.columns(2)
    k1.metric("Procesos analizables", len(f))
    k2.metric("Documentos disponibles", int(f["n_docs"].sum()) if len(f) else 0)

    if not len(f):
        st.info("Ningun proceso coincide con el filtro.")
        return

    f = f.reset_index(drop=True)

    def _etiqueta(i):
        r = f.loc[i]
        return (f"{str(r['fecha'])[:10]} - {str(r.get('nomenclatura') or '')[:26]} - "
                f"{str(r.get('entidad') or '')[:40]} - {int(r['n_docs'])} doc(s)")

    j = st.selectbox("Proceso a analizar", range(len(f)), format_func=_etiqueta,
                     key="cmp_proceso")
    st.divider()
    pe.render(f.loc[j].to_dict(), db)


FUENTES = {
    "seace": "📡 SEACE / OECE",
    "perucompras": "🛒 Perú Compras",
    "petroperu": "🛢️ PetroPerú",
    "bnacion": "🏦 Banco de la Nación",
    "comparador": "🟢 Comparador BTOUCH",
}

def selector_fuente():
    cols = st.columns(len(FUENTES))
    for col, (clave, etiqueta) in zip(cols, FUENTES.items()):
        activo = st.session_state.vista == clave
        if col.button(etiqueta, key=f"sel_{clave}", type="primary" if activo else "secondary",
                       width="stretch"):
            st.session_state.vista = clave
            st.rerun()
    st.divider()

selector_fuente()
if st.session_state.vista == "perucompras":
    vista_perucompras()
elif st.session_state.vista == "petroperu":
    vista_petroperu()
elif st.session_state.vista == "bnacion":
    vista_bnacion()
elif st.session_state.vista == "comparador":
    vista_comparador()
else:
    vista_seace()
