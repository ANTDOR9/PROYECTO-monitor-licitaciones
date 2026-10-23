"""
Panel de expediente para el tablero: la comparación contra BTOUCH con un botón.

Está organizado en dos capas, y la separación es deliberada.

La **capa 1** muestra lo que ya está en la base y aparece al instante: entidad,
fecha, monto adjudicado, proveedor ganador, categoría de producto y enlace a la
ficha del SEACE. No descarga nada.

La **capa 2** es el análisis del expediente, y es cara: descargar puede tardar
hasta un minuto, y el reconocimiento óptico unos cinco segundos por página
escaneada. Por eso va detrás de un botón, informa en qué paso está, y guarda el
resultado para no repetirlo.

La capa 2 falla seguido, y eso no es un defecto del panel. Solo cuatro de cada
veintitrés expedientes entregan un requerimiento legible: el resto son escaneos
de los que no se puede aislar la sección técnica. Cuando no se puede, el panel
dice en qué paso se detuvo y por qué, en lugar de quedarse en blanco.
"""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import streamlit as st

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

TMP = RAIZ / "tmp" / "panel"


def ruta_analisis(db_principal: Path | str) -> str:
    """
    Las tablas derivadas viven en data/analisis.db, no en licitaciones.db.

    Se acepta igual la base principal si el analisis esta ahi: las versiones
    anteriores lo guardaban junto y no tiene sentido romper una instalacion
    existente por eso.
    """
    analisis = Path(db_principal).parent / "analisis.db"
    if analisis.exists():
        return str(analisis)
    return str(db_principal)

COLORES = {"CUMPLE": "#C6E0B4", "OBSERVADO": "#FFF2CC", "NO CUMPLE": "#F8CBAD",
           "SIN DATO": "#EDEDED", "REVISIÓN MANUAL": "#FFF2CC"}

#: Orden en que conviene intentar los documentos PARA ESTE USO.
#:
#: No es el mismo orden que usa `contenedores.clasificar`. Allá el acta de
#: adjudicación pesa casi tanto como las especificaciones, porque se la buscaba
#: para identificar la marca del ganador. Acá lo que hace falta es el
#: requerimiento técnico —lo que la entidad EXIGIÓ— y eso está en las bases,
#: no en el acta. El acta, además, se verificó que no nombra ninguna marca.
PRIORIDAD_PANEL = {
    "especificaciones": 100,
    "bases": 90,
    "pliego": 70,
    "costos": 40,
    "evaluacion": 30,
    "desierto": 25,
    "documento": 25,
    "otro": 20,
    "ruido": 5,
    "ilegible": 0,
}


# --------------------------------------------------------------------------- #
# Lectura del índice
# --------------------------------------------------------------------------- #

def hay_indice(db: Path | str) -> bool:
    try:
        con = sqlite3.connect(f"file:{ruta_analisis(db)}?mode=ro", uri=True)
        n = con.execute("select count(*) from documentos").fetchone()[0]
        con.close()
        return n > 0
    except sqlite3.Error:
        return False


def documentos_de(db: Path | str, ocid: str) -> list[dict]:
    """Documentos indexados de un proceso, del más al menos relevante."""
    if not ocid:
        return []
    try:
        con = sqlite3.connect(f"file:{ruta_analisis(db)}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        filas = [dict(r) for r in con.execute(
            "select * from documentos where ocid = ? order by etapa", (ocid,))]
        con.close()
    except sqlite3.Error:
        return []

    from contenedores import clasificar                          # noqa: PLC0415
    for f in filas:
        clase, _, _ = clasificar(f"{f['titulo']} {f['tipo_doc']}")
        f["clase"] = clase
        f["prioridad"] = PRIORIDAD_PANEL.get(clase, 20)
    return sorted(filas, key=lambda f: -f["prioridad"])


# --------------------------------------------------------------------------- #
# El análisis, sin Streamlit: así se puede probar desde la consola
# --------------------------------------------------------------------------- #

def analizar(filecode: str, nombre_doc: str = "", avisar=lambda paso: None) -> dict:
    """
    Descarga, abre, lee y evalúa un documento del expediente.

    `avisar` recibe el nombre de cada paso, para que la interfaz pueda mostrar
    el progreso sin que este módulo dependa de Streamlit.
    """
    from contenedores import abrir                               # noqa: PLC0415
    from descargar_documento import descargar                    # noqa: PLC0415

    salida = {"ok": False, "paso": "", "motivo": "", "filecode": filecode}

    # --- 1. descargar -------------------------------------------------------
    avisar("Descargando el documento del SEACE")
    salida["paso"] = "descarga"
    try:
        res = descargar(filecode)
    except Exception as e:                                       # noqa: BLE001
        salida["motivo"] = f"No se pudo descargar: {type(e).__name__}: {e}"
        return salida
    if not res["ok"]:
        salida["motivo"] = (f"El servidor no devolvió un documento válido "
                            f"({res['tipo']}, {res['bytes']:,} bytes). "
                            f"El código de archivo puede haber vencido.")
        return salida
    salida["bytes"] = res["bytes"]
    salida["tipo"] = res["tipo"]
    salida["nombre"] = res["nombre"] or nombre_doc

    # --- 2. abrir -----------------------------------------------------------
    avisar(f"Abriendo el contenedor ({res['tipo']}, {res['bytes'] / 1e6:.1f} MB)")
    salida["paso"] = "apertura"
    destino = TMP / filecode[:8]
    abierto = abrir(res["datos"], destino, nombre=res["nombre"])
    if not abierto.ok:
        salida["motivo"] = abierto.mensaje
        return salida
    salida["archivos"] = [d.nombre for d in abierto.documentos]

    import texto_office                                          # noqa: PLC0415

    doc = abierto.mejor
    if doc is None:
        salida["motivo"] = ("El expediente no trae ningún documento legible. "
                            f"Contiene: {', '.join(salida['archivos'][:6]) or 'nada'}")
        return salida
    if doc.tipo != "pdf" and doc.tipo not in texto_office.TIPOS:
        nombres = {"xlsx": "una hoja de cálculo", "zip": "un comprimido",
                   "rar5": "un comprimido", "rar4": "un comprimido"}
        salida["motivo"] = (
            f"El documento principal del expediente es {nombres.get(doc.tipo, doc.tipo)} "
            f"(«{doc.nombre}»), y el lector solo entiende PDF y Word. "
            f"Contiene: {', '.join(salida['archivos'][:6]) or 'nada'}")
        return salida
    salida["documento"] = doc.nombre
    salida["clase"] = doc.clase

    # --- 3. leer ------------------------------------------------------------
    avisar(f"Leyendo «{doc.nombre[:48]}»")
    salida["paso"] = "lectura"
    if doc.tipo in texto_office.TIPOS:
        # Word no tiene paginas que recorrer como el PDF: se lee entero y se
        # deja que el extractor busque en todo el texto. Sale mas ruidoso, pero
        # es la unica forma de no descartar un expediente por su formato.
        from extractor import Bloque                              # noqa: PLC0415
        crudo = texto_office.texto(doc.ruta, doc.tipo)
        if not crudo.strip():
            salida["motivo"] = (
                f"«{doc.nombre}» es un documento de Word del que no se pudo "
                f"rescatar texto. Suele pasar cuando el contenido son imágenes "
                f"pegadas dentro del documento.")
            return salida
        bloque = Bloque(texto=crudo, origen="word")
    else:
        try:
            from extractor import localizar                      # noqa: PLC0415
            bloque = localizar(doc.ruta)
        except ImportError as e:
            salida["motivo"] = (f"Falta una librería para leer PDF ({e.name}). "
                                f"Instalá:  pip install pypdf pdfplumber")
            return salida
        except Exception as e:                                   # noqa: BLE001
            salida["motivo"] = f"No se pudo leer el PDF: {type(e).__name__}: {e}"
            return salida

    salida["origen"] = getattr(bloque, "origen", "?")
    salida["paginas"] = list(getattr(bloque, "paginas", []) or [])
    if not getattr(bloque, "texto", ""):
        salida["motivo"] = (
            "No se localizó la sección de especificaciones técnicas dentro del "
            "documento. Suele pasar con escaneos de baja calidad o cuando el "
            "requerimiento está en un archivo aparte que el expediente no incluye.")
        return salida

    # --- 4. extraer ---------------------------------------------------------
    avisar("Extrayendo el requerimiento técnico")
    salida["paso"] = "extracción"
    from dataclasses import asdict                               # noqa: PLC0415
    from extractor import extraer                                # noqa: PLC0415
    specs = extraer(bloque.texto)
    salida["specs"] = {k: v for k, v in asdict(specs).items() if v is not None}
    salida["cobertura"] = specs.cobertura()
    if not salida["specs"]:
        if salida.get("origen") == "sin_ocr":
            salida["motivo"] = (
                "El requerimiento está en páginas escaneadas y este equipo no "
                "tiene OCR instalado, así que esas páginas se leyeron vacías.\n\n"
                "Instalá las dos herramientas y volvé a intentar:\n\n"
                "```\nwinget install UB-Mannheim.TesseractOCR\n"
                "winget install oschwartz10612.Poppler\n```\n\n"
                "Hace falta además el paquete de idioma español de Tesseract "
                "(`spa`), que el instalador ofrece durante la instalación. "
                "Comprobalo después con:  `python src/comprobar_entorno.py`")
        else:
            salida["motivo"] = (
                "Se leyó el documento pero no se reconoció ningún parámetro "
                "técnico. Si el proceso es anterior a 2018, suele ser real y no "
                "un fallo: esas bases describen pizarras de proyección "
                "—superficie, área sensitiva, puerto USB— y no los parámetros de "
                "un panel moderno (Android, RAM, OPS, brillo en cd/m²), que son "
                "los que el extractor busca.")
        return salida

    # --- 5. evaluar ---------------------------------------------------------
    avisar("Comparando contra las fichas BTOUCH y de la competencia")
    salida["paso"] = "evaluación"
    from evaluar_licitacion import evaluar_todas, NO_CUMPLE      # noqa: PLC0415
    resultados = []
    for r in evaluar_todas(salida["specs"]):
        c = r.cuenta
        resultados.append({
            "marca": r.ficha.marca, "modelo": r.ficha.modelo,
            "dictamen": r.dictamen, "concluyente": r.concluyente,
            "verde": c["CUMPLE"], "amarillo": c["OBSERVADO"] + c["REVISIÓN MANUAL"],
            "rojo": c[NO_CUMPLE], "sin_dato": c["SIN DATO"],
            "comparables": r.comparables, "medido": r.medido,
            "detalle": [{"parametro": v.etiqueta, "exigido": v.exigido,
                         "ofrecido": v.ofrecido, "resultado": v.resultado,
                         "nota": v.nota} for v in r.veredictos],
        })
    salida["resultados"] = resultados
    salida["ok"] = True
    salida["paso"] = "listo"
    return salida


# --------------------------------------------------------------------------- #
# Interfaz
# --------------------------------------------------------------------------- #

def _capa1(fila: dict) -> None:
    c = st.columns([2, 1, 1, 1])
    c[0].markdown(f"**{(fila.get('entidad') or '—')[:60]}**")
    c[0].caption(fila.get("nomenclatura") or "")
    c[1].metric("Fecha", (fila.get("fecha") or "—")[:10])
    # Ojo con NaN: `if monto` da verdadero para NaN y la métrica mostraba
    # "S/ nan". 121 de las 436 licitaciones no tienen monto adjudicado.
    monto = fila.get("monto_adjudicado")
    try:
        import math
        vacio = monto is None or (isinstance(monto, float) and math.isnan(monto))
    except Exception:                                            # noqa: BLE001
        vacio = monto is None
    c[2].metric("Monto adjudicado", "—" if vacio else f"S/ {float(monto):,.0f}")
    c[3].metric("Categoría", fila.get("_categoria_prod") or "—")

    g = fila.get("proveedor_ganador")
    if g and str(g).strip() not in ("", "-"):
        st.markdown(f"Ganador: **{g}**")
    if fila.get("enlace"):
        st.markdown(f"[Ver la ficha en SEACE]({fila['enlace']})")
    st.caption(fila.get("objeto", "")[:300])


def _tabla_resultados(resultados: list[dict]) -> None:
    import pandas as pd                                          # noqa: PLC0415

    utiles = [r for r in resultados if r["concluyente"]]
    otros = [r for r in resultados if not r["concluyente"]]

    if not utiles:
        st.warning("Ninguna ficha permite verificar lo suficiente de lo exigido "
                   "como para emitir un dictamen.")
    else:
        df = pd.DataFrame([{
            "Marca": r["marca"], "Modelo": r["modelo"], "Dictamen": r["dictamen"],
            "Cumple": r["verde"], "Observado": r["amarillo"],
            "No cumple": r["rojo"], "Sin dato": r["sin_dato"],
        } for r in sorted(utiles, key=lambda r: (r["rojo"], -r["comparables"]))])
        st.dataframe(df, width="stretch", hide_index=True)

    for r in sorted(utiles, key=lambda r: (r["rojo"], -r["comparables"])):
        bloq = [d for d in r["detalle"] if d["resultado"] == "NO CUMPLE"]
        titulo = f"{r['marca']} · {r['modelo']} — {r['dictamen']}"
        with st.expander(titulo, expanded=bool(bloq) and r["marca"] == "BTOUCH"):
            for d in r["detalle"]:
                if d["resultado"] == "CUMPLE":
                    continue
                color = COLORES.get(d["resultado"], "#EDEDED")
                st.markdown(
                    f"<div style='background:{color};padding:6px 10px;"
                    f"border-radius:4px;margin-bottom:4px'>"
                    f"<b>{d['parametro']}</b> — {d['resultado']}<br>"
                    f"<small>exige: {d['exigido'][:70]} · ofrece: {d['ofrecido'][:50]}</small>"
                    + (f"<br><small><i>{d['nota'][:160]}</i></small>" if d["nota"] else "")
                    + "</div>", unsafe_allow_html=True)
            verdes = sum(1 for d in r["detalle"] if d["resultado"] == "CUMPLE")
            st.caption(f"{verdes} parámetro(s) cumplen sin observación.")

    if otros:
        st.caption("Sin medir: " + ", ".join(
            f"{r['marca']} ({r['medido']:.0%} de lo exigido verificable)" for r in otros)
            + ". Una ficha que no publica datos no es competitiva ni incompetitiva: "
              "es desconocida.")


def _pie_manual() -> None:
    from evaluar_licitacion import PENDIENTES_MANUALES           # noqa: PLC0415
    with st.expander("Puntos que las reglas de Brighter piden revisar a mano"):
        for titulo, detalle in PENDIENTES_MANUALES:
            st.markdown(f"**{titulo}** — {detalle}")


def render(fila: dict, db: Path | str) -> None:
    """Panel completo para una licitación."""
    _capa1(fila)
    st.divider()

    ocid = fila.get("ocid") or ""
    if not hay_indice(db):
        st.info("Los documentos del expediente todavía no están indexados. "
                "Corré una vez:  `python src/indexar_documentos.py`")
        return

    docs = documentos_de(db, ocid)
    if not docs:
        st.info(
            "**Este proceso no tiene documentos indexados.**\n\n"
            "La causa más común no es que no publique expediente, sino que el "
            "proceso es más nuevo que el paquete OCDS descargado: el índice solo "
            "ve lo que hay en `data/*.jsonl.gz`, y esos archivos son una foto del "
            "día en que se bajaron.\n\n"
            "Para incorporarlo:\n\n"
            "```\npython src/extract.py          # vuelve a bajar el paquete del año\n"
            "python src/indexar_documentos.py\n```\n\n"
            "Si tras eso sigue sin documentos, el proceso efectivamente no publica "
            "expediente descargable.")
        return

    etiquetas = [f"[{d['clase']}] {d['titulo'] or d['tipo_doc']} ({d['etapa']})"
                 for d in docs]
    i = st.selectbox("Documento del expediente", range(len(docs)),
                     format_func=lambda i: etiquetas[i], key=f"doc_{ocid}")
    elegido = docs[i]

    clave = f"analisis_{elegido['filecode']}"
    if st.button("Analizar expediente y comparar con BTOUCH", key=f"btn_{ocid}",
                 type="primary"):
        _correr(elegido["filecode"], elegido["titulo"], clave)

    _mostrar(clave)


def _correr(filecode: str, titulo: str, clave: str) -> None:
    """Descarga, lee y compara. Deja el resultado en la sesión."""
    with st.status("Analizando…", expanded=True) as estado:
        def avisar(paso):
            estado.update(label=paso)
            st.write(paso)
        resultado = analizar(filecode, titulo, avisar)
        estado.update(
            label="Análisis completo" if resultado["ok"] else
                  f"Se detuvo en: {resultado['paso']}",
            state="complete" if resultado["ok"] else "error", expanded=False)
    st.session_state[clave] = resultado


def _mostrar(clave: str) -> None:
    """Pinta el resultado guardado, si lo hay."""
    resultado = st.session_state.get(clave)
    if not resultado:
        st.caption("El análisis descarga el expediente y lo lee. Puede tardar entre "
                   "unos segundos y varios minutos según el tamaño y si hace falta "
                   "reconocimiento óptico. El resultado queda guardado.")
        return

    if not resultado["ok"]:
        st.error(f"**No se pudo completar ({resultado['paso']}).**\n\n{resultado['motivo']}")
        if resultado.get("archivos"):
            st.caption("El expediente contenía: " + ", ".join(resultado["archivos"][:8]))
        return

    origen = resultado.get("origen", "?")
    COMO_SE_LEYO = {"nativo": "texto nativo", "ocr": "reconocimiento óptico",
                    "mixto": "texto nativo más OCR de las páginas escaneadas",
                    "sin_ocr": "solo texto nativo, sin OCR",
                    "word": "documento de Word"}
    if origen == "sin_ocr":
        st.warning(
            "El documento tiene páginas escaneadas que no se pudieron leer "
            "porque falta el OCR en este equipo. Lo que ves abajo sale solo de "
            "las páginas con texto, así que puede estar incompleto. Para "
            "instalarlo:  `winget install UB-Mannheim.TesseractOCR` y "
            "`winget install oschwartz10612.Poppler`.")
    st.success(
        f"Leído **{resultado.get('documento', '')}** "
        f"({COMO_SE_LEYO.get(origen, origen)}"
        + (f", páginas {resultado['paginas']}" if resultado.get("paginas") else "")
        + f"). Se reconocieron {len(resultado['specs'])} parámetros "
          f"({resultado['cobertura']:.0%} de cobertura).")

    with st.expander("Requerimiento extraído"):
        for k, v in resultado["specs"].items():
            st.markdown(f"- **{k}**: {str(v)[:120]}")

    _tabla_resultados(resultado["resultados"])
    _pie_manual()


def render_convocatoria(fila: dict) -> None:
    """
    Panel para una convocatoria VIGENTE, sin pasar por el índice del OCDS.

    La API en vivo del SEACE devuelve, en cada procedimiento, el campo
    `documentoBase`: el mismo código de archivo con el que se descargan las
    bases. Se verificó que funciona con un GET directo, así que un proceso
    convocado hoy se puede leer hoy, sin esperar a que el paquete anual del
    OCDS lo publique —que lleva alrededor de dos semanas—.

    Lo que no se obtiene por esta vía es el resto del expediente: acá solo
    están las bases. Alcanza para el requerimiento técnico, que es lo que
    compara el motor.
    """
    c = st.columns([2, 1, 1])
    c[0].markdown(f"**{(fila.get('entidad') or '—')[:60]}**")
    c[0].caption(fila.get("nomenclatura") or "")
    c[1].metric("Convocatoria", str(fila.get("fecha_convocatoria") or "—")[:10])
    c[2].metric("Presentación", str(fila.get("fecha_presentacion") or "—")[:10])
    if fila.get("descripcion"):
        st.caption(str(fila["descripcion"])[:300])
    if fila.get("enlace"):
        st.markdown(f"[Ver la ficha en SEACE]({fila['enlace']})")

    filecode = (fila.get("documento_base") or "").strip()
    if not filecode:
        st.info("Esta convocatoria no trae código de bases en la API. Habrá que "
                "esperar a que el paquete del OCDS publique su expediente.")
        return

    st.divider()
    clave = f"analisis_{filecode}"
    if st.button("Descargar bases y comparar con BTOUCH",
                 key=f"btn_vig_{filecode}", type="primary"):
        _correr(filecode, f"Bases de {fila.get('nomenclatura') or filecode}", clave)
    _mostrar(clave)
