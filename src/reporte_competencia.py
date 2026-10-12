"""
Reporte de inteligencia competitiva en Excel, a partir del historial de postores.

Lee lo que `src/postores.py` dejó en `data/analisis.db` (tabla `postores`) y, de
forma opcional, el padrón de Perú Compras que vive en `data/licitaciones.db`.
Produce un único libro con una hoja por nivel de agregación, pensado para que
otra persona pueda auditar cada número hasta la participación individual.

El libro NO se escribe dentro del repositorio: los resultados son material
interno del estudio de mercado y el repositorio es público. Por defecto va a la
carpeta del estudio; si no existe, cae en `tmp/` (que está ignorado por git).

Uso:
    python src/reporte_competencia.py
    python src/reporte_competencia.py --anios 2024 2025 2026
    python src/reporte_competencia.py --salida "C:/ruta/archivo.xlsx"

Advertencias de lectura que el propio libro repite en la hoja 00:

  * Un año con muchas menos participaciones por proceso no es un año de menos
    competencia: es un año que publica peor los postores. 2023 está en ese
    caso y se marca como tal.
  * La columna "exceso sobre lo esperado" de la hoja 04 es un cociente entre lo
    observado y lo que saldría si las empresas se repartieran al azar. Mide
    rareza estadística, no concertación: un número alto es motivo para mirar el
    expediente, nunca una conclusión sobre la conducta de una empresa.
"""

from __future__ import annotations

import argparse
import math
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

try:
    import openpyxl
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
except ImportError:
    print("Falta openpyxl.  pip install openpyxl", file=sys.stderr)
    raise SystemExit(2)

RAIZ = Path(__file__).resolve().parent.parent
DB = RAIZ / "data" / "analisis.db"
DB_LIC = RAIZ / "data" / "licitaciones.db"

# Carpeta del estudio. Si no está, el libro cae en tmp/ y se avisa.
SALIDA_ESTUDIO = (Path.home() / "Downloads" / "ESTUDIO MERCADO"
                  / "CERRO COLORADO 9 - OCTUBRE -(ESTUDIO MERCADO)"
                  / "D - ESTUDIO DE MERCADO")

ENTIDAD_OBJETIVO = "CERRO COLORADO"
PROPIA = "BRIGHTER"

MINIMO_PAREJA = 3        # parejas con menos coincidencias no se listan
MINIMO_PAREJA_EXCESO = 5  # por debajo de esto, el exceso no se interpreta
TOPE_INDICADORES = 100
TOPE_MATRIZ = 40

AZUL = "1F3864"
GRIS = "F2F2F2"
AMBAR = "FFF2CC"

TIPOS = {
    "LP": "Licitación Pública",
    "CP": "Concurso Público",
    "AS": "Adjudicación Simplificada",
    "ADS": "Adjudicación Directa Selectiva",
    "ADP": "Adjudicación Directa Pública",
    "SIE": "Subasta Inversa Electrónica",
    "CE": "Contratación Especial",
    "COMPRE": "Compre Perú",
    "CD": "Contratación Directa",
    "AMC": "Adjudicación de Menor Cuantía",
}


# --------------------------------------------------------------------------- #
# Lectura
# --------------------------------------------------------------------------- #

def conectar(db: Path, db_lic: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db))
    con.row_factory = sqlite3.Row
    if db_lic.exists():
        con.execute("ATTACH ? AS lic", (str(db_lic),))
    return con


def tiene_tabla(con: sqlite3.Connection, nombre: str) -> bool:
    sql = ("select count(*) from sqlite_master where type='table' and name=?",)
    try:
        if con.execute(sql[0], (nombre,)).fetchone()[0]:
            return True
        return bool(con.execute(
            "select count(*) from lic.sqlite_master where type='table' and name=?",
            (nombre,)).fetchone()[0])
    except sqlite3.Error:
        return False


def participaciones(con: sqlite3.Connection, categoria: str,
                    anios: list[int]) -> list[sqlite3.Row]:
    donde = ["nombre <> ''"]
    param: list = []
    if categoria:
        donde.append("categoria = ?")
        param.append(categoria)
    if anios:
        donde.append("anio in (%s)" % ",".join("?" * len(anios)))
        param.extend(anios)
    return list(con.execute(
        "select * from postores where " + " and ".join(donde)
        + " order by fecha desc, nombre", param))


def zona(fila: sqlite3.Row) -> str:
    dep = (fila["departamento"] or "").strip().upper()
    if not dep:
        return "(sin zona)"
    return "AREQUIPA (región)" if dep.startswith("AREQUIPA") else dep


def tipo_proceso(nomenclatura: str) -> str:
    pref = (nomenclatura or "").split("-", 1)[0].upper()
    return TIPOS.get(pref, pref or "(sin dato)")


# --------------------------------------------------------------------------- #
# Agregación
# --------------------------------------------------------------------------- #

class Analisis:
    def __init__(self, filas: list[sqlite3.Row]):
        self.filas = filas
        self.procesos: dict[str, list[sqlite3.Row]] = defaultdict(list)
        for f in filas:
            self.procesos[f["ocid"]].append(f)

        self.part: Counter = Counter()          # RUC -> participaciones
        self.gano: Counter = Counter()
        self.nombre: dict[str, str] = {}
        self.anios: dict[str, set] = defaultdict(set)
        self.entidades: dict[str, set] = defaultdict(set)
        self.zonas: dict[str, set] = defaultdict(set)
        self.fechas: dict[str, list[str]] = defaultdict(list)
        self.procesos_de: dict[str, set] = defaultdict(set)
        self.por_anio: dict[str, Counter] = defaultdict(Counter)

        for f in filas:
            ruc = f["ruc"] or f["nombre"]
            self.part[ruc] += 1
            self.gano[ruc] += 1 if f["gano"] else 0
            self.nombre.setdefault(ruc, f["nombre"])
            self.anios[ruc].add(f["anio"])
            self.entidades[ruc].add(f["entidad"])
            self.zonas[ruc].add(zona(f))
            if f["fecha"]:
                self.fechas[ruc].append(f["fecha"])
            self.procesos_de[ruc].add(f["ocid"])
            self.por_anio[ruc][f["anio"]] += 1

        # Coincidencias: una vez por par y por proceso.
        self.juntas: Counter = Counter()
        for rows in self.procesos.values():
            rucs = sorted({r["ruc"] or r["nombre"] for r in rows})
            for i, a in enumerate(rucs):
                for b in rucs[i + 1:]:
                    self.juntas[(a, b)] += 1

    @property
    def n_procesos(self) -> int:
        return len(self.procesos)

    def esperadas(self, a: str, b: str) -> float:
        """Coincidencias que saldrían si cada empresa eligiera procesos al azar."""
        n = self.n_procesos
        if not n:
            return 0.0
        return len(self.procesos_de[a]) * len(self.procesos_de[b]) / n

    def socio_principal(self, ruc: str) -> tuple[str, int]:
        mejor, veces = "", 0
        for (a, b), n in self.juntas.items():
            if ruc not in (a, b):
                continue
            if n > veces:
                mejor, veces = (b if a == ruc else a), n
        return mejor, veces

    def socios_recurrentes(self, ruc: str, minimo: int = 3) -> int:
        return sum(1 for (a, b), n in self.juntas.items()
                   if ruc in (a, b) and n >= minimo)

    def cobertura(self) -> list[tuple]:
        out = []
        for anio in sorted({f["anio"] for f in self.filas}):
            fil = [f for f in self.filas if f["anio"] == anio]
            proc = {f["ocid"] for f in fil}
            emp = {f["ruc"] or f["nombre"] for f in fil}
            media = len(fil) / len(proc) if proc else 0
            out.append((anio, len(proc), len(emp), len(fil), media))
        return out


# --------------------------------------------------------------------------- #
# Escritura
# --------------------------------------------------------------------------- #

BORDE = Border(bottom=Side(style="thin", color="BFBFBF"))
ANIO_ACTUAL = date.today().year


def referencia_cobertura(cobertura: list[tuple]) -> float:
    """Mejor cobertura entre los años ya cerrados.

    El año en curso siempre sale bajo —sus procesos todavía no cierran y la
    lista de postores se completa después—, así que tomarlo como referencia
    haría parecer completos a los años que no lo están.
    """
    cerrados = [x[4] for x in cobertura if x[0] < ANIO_ACTUAL]
    return max(cerrados) if cerrados else max((x[4] for x in cobertura), default=0)


def aviso_anio(anio: int, media: float, referencia: float) -> str:
    if anio >= ANIO_ACTUAL:
        return ("año en curso: los procesos abiertos todavía no publican su "
                "lista completa de postores")
    if referencia and media < 0.6 * referencia:
        return "cobertura parcial: no comparable con los demás años"
    return ""


def hoja(libro, titulo: str, cabeceras: list[str], anchos: list[int]):
    h = libro.create_sheet(titulo)
    h.append(cabeceras)
    for c in h[1]:
        c.font = Font(bold=True, color="FFFFFF", size=10)
        c.fill = PatternFill("solid", fgColor=AZUL)
        c.alignment = Alignment(vertical="center", wrap_text=True,
                                horizontal="center")
    h.row_dimensions[1].height = 30
    for i, ancho in enumerate(anchos, start=1):
        h.column_dimensions[get_column_letter(i)].width = ancho
    h.freeze_panes = "A2"
    return h


def cerrar(h, columnas: int):
    if h.max_row > 1:
        h.auto_filter.ref = f"A1:{get_column_letter(columnas)}{h.max_row}"


def formato(h, columna: str, fmt: str, desde: int = 2):
    for fila in range(desde, h.max_row + 1):
        h[f"{columna}{fila}"].number_format = fmt


def leeme(libro, a: Analisis, categoria: str, anios: list[int], perucompras: bool):
    h = libro.create_sheet("00 LEEME")
    h.column_dimensions["A"].width = 112
    negrita = Font(bold=True, size=11, color=AZUL)

    def t(texto="", bold=False, salto=False):
        h.append([texto])
        c = h.cell(row=h.max_row, column=1)
        c.alignment = Alignment(wrap_text=True, vertical="top")
        if bold:
            c.font = negrita
        if salto:
            h.append([""])

    rango = f"{min(x[0] for x in a.cobertura())}–{max(x[0] for x in a.cobertura())}"
    t("INTELIGENCIA COMPETITIVA — HISTORIAL DE POSTORES", True)
    t(f"Insumo para el estudio de mercado. Generado el {date.today():%d/%m/%Y}.", salto=True)

    t("Alcance", True)
    t(f"Categoría de producto: {categoria or 'todas'}. Periodo: {rango}. "
      f"{a.n_procesos} procesos, {len(a.part)} empresas distintas, "
      f"{len(a.filas)} participaciones "
      f"({len(a.filas)/a.n_procesos:.1f} por proceso).", salto=True)

    t("Cobertura por año — leer antes de comparar años entre sí", True)
    t("Las participaciones por proceso son el indicador de calidad del año. Un año "
      "muy por debajo de los demás no tuvo menos competencia: publicó peor la lista "
      "de postores, y promediarlo con los otros subestima a las empresas frecuentes.")
    h.append(["año", "procesos", "empresas", "participaciones", "por proceso"])
    for c in h[h.max_row]:
        c.font = Font(bold=True, size=10)
        c.fill = PatternFill("solid", fgColor=GRIS)
    referencia = referencia_cobertura(a.cobertura())
    for anio, p, e, n, media in a.cobertura():
        h.append([anio, p, e, n, round(media, 1)])
        aviso = aviso_anio(anio, media, referencia)
        if aviso:
            for c in h[h.max_row]:
                c.fill = PatternFill("solid", fgColor=AMBAR)
            h.cell(row=h.max_row, column=6, value=aviso)
    h.append([""])

    t("Qué hay en cada hoja", True)
    for linea in [
        "01 EMPRESAS — una fila por empresa: cuántas veces postuló, cuántas ganó, "
        "en cuántos años apareció, primera y última aparición.",
        "02 PARTICIPACIONES — el dato crudo: una fila por empresa y proceso. "
        "Todo lo demás se puede recalcular desde aquí.",
        "03 PROCESOS — una fila por proceso: entidad, zona, número de postores y ganador.",
        "04 COINCIDENCIAS — parejas de empresas que se presentan al mismo proceso, "
        f"con {MINIMO_PAREJA} coincidencias o más.",
        "05 INDICADORES — por empresa: socio recurrente principal y patrones visibles.",
        "06 AREQUIPA — la actividad de cada empresa en la región, y qué parte de su "
        "actividad nacional representa.",
        "07 PERU COMPRAS — proveedores del catálogo electrónico. Es otro mercado: "
        "compra directa, sin postores." if perucompras else
        "07 PERU COMPRAS — no se generó (falta data/licitaciones.db).",
        f"08 {ENTIDAD_OBJETIVO} — lo que el histórico dice de la entidad objetivo.",
        "09 POR AÑO — las empresas más presentes, año por año: sirve para distinguir "
        "una empresa estable de una que apareció una sola temporada.",
    ]:
        t("  " + linea)
    h.append([""])

    t("Límites de la fuente", True)
    for linea in [
        "El OCDS no publica la marca ofertada. Quién ganó se sabe; con qué equipo, no.",
        "De los montos solo se conoce el del proceso y el del adjudicatario. Las ofertas "
        "perdedoras no se publican, así que no se puede reconstruir el nivel de precios.",
        "La columna «exceso sobre lo esperado» de la hoja 04 compara las coincidencias "
        "observadas con las que saldrían si cada empresa eligiera procesos al azar. Ese "
        "modelo al azar es deliberadamente ingenuo: ignora que las empresas se concentran "
        "por región, por tipo de proceso y por rango de monto, de modo que valores altos "
        "aparecen también entre empresas que simplemente trabajan el mismo nicho. Es un "
        f"criterio para priorizar qué expediente leer —y solo con {MINIMO_PAREJA_EXCESO} "
        "coincidencias o más—, no una medición de concertación. Calificar una conducta como "
        "concertada es competencia del INDECOPI.",
        "Las observaciones de la hoja 05 describen lo que se ve en los datos. No afirman "
        "intención ni acuerdo entre empresas.",
    ]:
        t("  • " + linea)


def h01(libro, a: Analisis):
    h = hoja(libro, "01 EMPRESAS",
             ["N.º", "RUC", "Empresa", "Participaciones", "Adjudicadas",
              "Tasa de adjudicación", "Años activos", "Entidades distintas",
              "Zonas distintas", "Primera aparición", "Última aparición"],
             [6, 14, 46, 13, 11, 13, 9, 11, 10, 13, 13])
    orden = sorted(a.part, key=lambda r: (-a.part[r], a.nombre[r]))
    for i, ruc in enumerate(orden, start=1):
        f = sorted(a.fechas[ruc])
        fila = h.max_row + 1
        h.append([i, ruc, a.nombre[ruc], a.part[ruc], a.gano[ruc],
                  f"=IFERROR(E{fila}/D{fila},0)", len(a.anios[ruc]),
                  len(a.entidades[ruc]), len(a.zonas[ruc]),
                  f[0] if f else "", f[-1] if f else ""])
    formato(h, "F", "0%")
    cerrar(h, 11)


def h02(libro, a: Analisis):
    h = hoja(libro, "02 PARTICIPACIONES",
             ["Fecha", "Año", "Zona", "Entidad", "Nomenclatura", "Objeto", "RUC",
              "Empresa", "¿Ganó?", "Monto adjudicado del proceso (S/)", "idProceso"],
             [11, 7, 20, 44, 26, 52, 14, 44, 9, 18, 11])
    for f in a.filas:
        monto = f["monto"]
        if monto is not None and isinstance(monto, float) and math.isnan(monto):
            monto = None
        h.append([f["fecha"], f["anio"], zona(f), f["entidad"], f["nomenclatura"],
                  (f["descripcion"] or "")[:200], f["ruc"], f["nombre"],
                  "SI" if f["gano"] else "", monto, f["idproceso"]])
    formato(h, "J", '#,##0')
    cerrar(h, 11)


def h03(libro, a: Analisis, referencial: dict[str, float]):
    h = hoja(libro, "03 PROCESOS",
             ["Fecha", "Zona", "Entidad", "Nomenclatura", "Tipo", "Objeto",
              "N.º de postores", "Ganador", "Monto adjudicado (S/)",
              "Valor referencial (S/)"],
             [11, 20, 44, 26, 22, 52, 11, 40, 18, 18])
    bloques = sorted(a.procesos.items(),
                     key=lambda kv: len(kv[1]), reverse=True)
    for ocid, rows in bloques:
        r0 = rows[0]
        ganadores = [x["nombre"] for x in rows if x["gano"]]
        monto = next((x["monto"] for x in rows if x["gano"] and x["monto"]), None)
        if monto is not None and isinstance(monto, float) and math.isnan(monto):
            monto = None
        h.append([r0["fecha"], zona(r0), r0["entidad"], r0["nomenclatura"],
                  tipo_proceso(r0["nomenclatura"]), (r0["descripcion"] or "")[:200],
                  len(rows), " | ".join(ganadores) or "(sin ganador registrado)",
                  monto, referencial.get(ocid)])
    formato(h, "I", '#,##0')
    formato(h, "J", '#,##0')
    cerrar(h, 10)


def h04(libro, a: Analisis):
    h = hoja(libro, "04 COINCIDENCIAS",
             ["Empresa A", "Empresa B", "Veces juntas", "Procesos de A",
              "Procesos de B", "% de los procesos de A", "% de los procesos de B",
              "Veces esperadas al azar", "Exceso sobre lo esperado", "¿Interpretable?"],
             [42, 42, 11, 11, 11, 13, 13, 13, 13, 30])
    pares = [(p, n) for p, n in a.juntas.items() if n >= MINIMO_PAREJA]
    pares.sort(key=lambda x: (-x[1], a.nombre[x[0][0]]))
    for (ra, rb), n in pares:
        fila = h.max_row + 1
        esp = a.esperadas(ra, rb)
        h.append([a.nombre[ra], a.nombre[rb], n,
                  len(a.procesos_de[ra]), len(a.procesos_de[rb]),
                  f"=IFERROR(C{fila}/D{fila},0)", f"=IFERROR(C{fila}/E{fila},0)",
                  round(esp, 2), f"=IFERROR(C{fila}/H{fila},0)",
                  "sí" if n >= MINIMO_PAREJA_EXCESO
                  else f"no: menos de {MINIMO_PAREJA_EXCESO} coincidencias"])
    formato(h, "F", "0%")
    formato(h, "G", "0%")
    formato(h, "H", "0.00")
    formato(h, "I", '0.0"×"')
    cerrar(h, 10)


def h05(libro, a: Analisis):
    h = hoja(libro, "05 INDICADORES",
             ["RUC", "Empresa", "Participaciones", "Adjudicadas",
              "Tasa de adjudicación", "Primera aparición", "Años activos",
              "Socio recurrente más frecuente", "Veces con ese socio",
              "% de sus procesos con ese socio", "Socios recurrentes (3+ veces)",
              "Zonas", "Entidades", "Observación (patrón, no conclusión)"],
             [14, 42, 13, 11, 13, 13, 9, 40, 11, 14, 13, 8, 10, 56])
    orden = sorted(a.part, key=lambda r: (-a.part[r], a.nombre[r]))[:TOPE_INDICADORES]
    for ruc in orden:
        socio, veces = a.socio_principal(ruc)
        n = a.part[ruc]
        fila = h.max_row + 1
        notas = []
        if n >= 8 and a.gano[ruc] == 0:
            notas.append("muchas participaciones, ninguna adjudicación")
        if veces and n and veces / n >= 0.55 and veces >= MINIMO_PAREJA_EXCESO:
            notas.append("coincide casi siempre con la misma empresa")
        if len(a.anios[ruc]) >= max(3, len(a.cobertura()) - 1) and a.gano[ruc] == 0:
            notas.append("presente todos los años sin ganar nunca")
        if a.gano[ruc] and n and a.gano[ruc] / n >= 0.5 and n >= 2:
            notas.append("gana la mayoría de las veces que se presenta")
        if len(a.zonas[ruc]) >= 8:
            notas.append("actividad dispersa en muchas regiones")
        fechas = sorted(a.fechas[ruc])
        h.append([ruc, a.nombre[ruc], n, a.gano[ruc],
                  f"=IFERROR(D{fila}/C{fila},0)", fechas[0] if fechas else "",
                  len(a.anios[ruc]), a.nombre.get(socio, ""), veces,
                  f"=IFERROR(I{fila}/C{fila},0)", a.socios_recurrentes(ruc),
                  len(a.zonas[ruc]), len(a.entidades[ruc]), "; ".join(notas)])
    formato(h, "E", "0%")
    formato(h, "J", "0%")
    cerrar(h, 14)


def h06(libro, a: Analisis):
    h = hoja(libro, "06 AREQUIPA",
             ["RUC", "Empresa", "Participaciones en la región", "Adjudicadas",
              "Tasa", "Entidades de la región", "Participaciones en todo el país",
              "% de su actividad que es regional"],
             [14, 42, 13, 11, 10, 46, 13, 14])
    reg: Counter = Counter()
    gan: Counter = Counter()
    ent: dict[str, set] = defaultdict(set)
    for f in a.filas:
        if zona(f).startswith("AREQUIPA"):
            ruc = f["ruc"] or f["nombre"]
            reg[ruc] += 1
            gan[ruc] += 1 if f["gano"] else 0
            ent[ruc].add(f["entidad"])
    for ruc, n in sorted(reg.items(), key=lambda kv: (-kv[1], a.nombre[kv[0]])):
        fila = h.max_row + 1
        h.append([ruc, a.nombre[ruc], n, gan[ruc], f"=IFERROR(D{fila}/C{fila},0)",
                  " | ".join(sorted(ent[ruc]))[:250], a.part[ruc],
                  f"=IFERROR(C{fila}/G{fila},0)"])
    formato(h, "E", "0%")
    formato(h, "H", "0%")
    cerrar(h, 8)


def h07(libro, con: sqlite3.Connection, a: Analisis) -> bool:
    if not tiene_tabla(con, "perucompras"):
        return False
    rucs = set(a.part)
    h = hoja(libro, "07 PERU COMPRAS",
             ["RUC", "Proveedor", "Órdenes", "Monto total (S/)",
              "Monto promedio (S/)", "Entidades distintas", "¿También postula en SEACE?"],
             [14, 46, 10, 16, 16, 12, 16])
    filas = con.execute(
        "select ruc_proveedor, max(proveedor), count(*) n, "
        "       sum(coalesce(monto_total,0)), count(distinct entidad) "
        "from lic.perucompras group by 1 order by n desc")
    for ruc, nombre, n, total, entidades in filas:
        fila = h.max_row + 1
        h.append([ruc, nombre, n, round(total or 0, 2),
                  f"=IFERROR(D{fila}/C{fila},0)", entidades,
                  "sí" if ruc in rucs else "no"])
    formato(h, "D", '#,##0')
    formato(h, "E", '#,##0')
    cerrar(h, 7)
    return True


def h08(libro, con: sqlite3.Connection, a: Analisis):
    h = libro.create_sheet(f"08 {ENTIDAD_OBJETIVO}")
    h.column_dimensions["A"].width = 34
    h.column_dimensions["B"].width = 78

    def t(k, v="", bold=False):
        h.append([k, v])
        if bold:
            for c in h[h.max_row]:
                c.font = Font(bold=True, color=AZUL)

    t(f"ENTIDAD OBJETIVO: {ENTIDAD_OBJETIVO}", "", True)
    h.append([""])

    propios = [f for f in a.filas
               if ENTIDAD_OBJETIVO in (f["entidad"] or "").upper()]
    if propios:
        for f in propios:
            t("Proceso", f["nomenclatura"])
            t("Fecha", f["fecha"])
            t("Entidad", f["entidad"])
            t("Objeto", (f["descripcion"] or "")[:200])
            t("Empresa registrada", f["nombre"])
            h.append([""])
    else:
        t("Procesos de la entidad en el histórico", "ninguno con postores publicados")
        h.append([""])
    t("Lectura", "La entidad no tiene historial de licitaciones de este rubro, de modo "
                 "que no hay postores habituales suyos. Quienes se presenten vendrán del "
                 "conjunto nacional de la hoja 01.")
    h.append([""])

    t("POSICIÓN PROPIA", "", True)
    mios = [r for r in a.part if PROPIA in a.nombre[r].upper()]
    if mios:
        for ruc in mios:
            t("Empresa", a.nombre[ruc])
            t("Participaciones", a.part[ruc])
            t("Adjudicadas", a.gano[ruc])
            t("Años activos", ", ".join(str(x) for x in sorted(a.anios[ruc])))
            socio, veces = a.socio_principal(ruc)
            if socio:
                t("Empresa con la que más coincide",
                  f"{a.nombre[socio]} ({veces} de {a.part[ruc]} procesos)")
            h.append([""])
    else:
        t("Participaciones propias en el periodo", "ninguna registrada")


def h09(libro, a: Analisis):
    anios = [x[0] for x in a.cobertura()]
    h = hoja(libro, "09 POR AÑO",
             ["RUC", "Empresa"] + [str(x) for x in anios]
             + ["Total", "Adjudicadas", "Años activos", "Primera aparición"],
             [14, 46] + [8] * len(anios) + [9, 11, 9, 13])
    orden = sorted(a.part, key=lambda r: (-a.part[r], a.nombre[r]))[:TOPE_MATRIZ]
    for ruc in orden:
        fechas = sorted(a.fechas[ruc])
        h.append([ruc, a.nombre[ruc]]
                 + [a.por_anio[ruc].get(x, 0) for x in anios]
                 + [a.part[ruc], a.gano[ruc], len(a.anios[ruc]),
                    fechas[0] if fechas else ""])
    cerrar(h, 6 + len(anios))


# --------------------------------------------------------------------------- #

def referenciales(con: sqlite3.Connection) -> dict[str, float]:
    if not tiene_tabla(con, "licitaciones"):
        return {}
    out = {}
    for ocid, valor in con.execute(
            "select ocid, monto_referencial from lic.licitaciones "
            "where monto_referencial is not null"):
        try:
            out[ocid] = float(valor)
        except (TypeError, ValueError):
            continue
    return out


def construir(salida: Path, categoria: str, anios: list[int],
              db: Path, db_lic: Path) -> Path:
    con = conectar(db, db_lic)
    filas = participaciones(con, categoria, anios)
    if not filas:
        raise SystemExit("La tabla 'postores' no tiene filas para ese filtro. "
                         "Corré src/postores.py primero.")
    a = Analisis(filas)

    libro = openpyxl.Workbook()
    libro.remove(libro.active)
    h01(libro, a)
    h02(libro, a)
    h03(libro, a, referenciales(con))
    h04(libro, a)
    h05(libro, a)
    h06(libro, a)
    hay_pc = h07(libro, con, a)
    h08(libro, con, a)
    h09(libro, a)
    leeme(libro, a, categoria, anios, hay_pc)
    # La portada va primera aunque se escriba al final (necesita los totales).
    libro.move_sheet("00 LEEME", offset=-(len(libro.sheetnames) - 1))

    salida.parent.mkdir(parents=True, exist_ok=True)
    libro.save(salida)
    con.close()

    print(f"\n{salida.name}")
    print(f"  carpeta        : {salida.parent}")
    print(f"  periodo        : {min(x[0] for x in a.cobertura())}"
          f"–{max(x[0] for x in a.cobertura())}")
    print(f"  procesos       : {a.n_procesos}")
    print(f"  empresas       : {len(a.part)}")
    print(f"  participaciones: {len(a.filas)}")
    print(f"  hojas          : {len(libro.sheetnames)}")
    print("\nCobertura por año (participaciones por proceso):")
    ref = referencia_cobertura(a.cobertura())
    for anio, p, e, n, media in a.cobertura():
        aviso = aviso_anio(anio, media, ref)
        marca = "   <-- " + ("año en curso" if anio >= ANIO_ACTUAL
                             else "cobertura parcial") if aviso else ""
        print(f"   {anio}   {p:>3} procesos  {n:>5} particip.  {media:>5.1f}/proceso{marca}")
    return salida


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anios", nargs="*", type=int, default=[],
                    help="años a incluir (por defecto, todos los de la tabla)")
    ap.add_argument("--solo", default="INTERACTIVA", metavar="CATEGORIA",
                    help="categoría de producto; vacío para todas")
    ap.add_argument("--db", default=str(DB))
    ap.add_argument("--db-licitaciones", default=str(DB_LIC))
    ap.add_argument("--salida", default="",
                    help="ruta del .xlsx; por defecto, la carpeta del estudio")
    args = ap.parse_args()

    if args.salida:
        salida = Path(args.salida)
    else:
        con = conectar(Path(args.db), Path(args.db_licitaciones))
        rango = con.execute(
            "select min(anio), max(anio) from postores"
            + (" where categoria = ?" if args.solo else ""),
            (args.solo.upper(),) if args.solo else ()).fetchone()
        con.close()
        nombre = (f"D1 Inteligencia competitiva - historial de postores "
                  f"{rango[0]}-{rango[1]}.xlsx")
        carpeta = SALIDA_ESTUDIO if SALIDA_ESTUDIO.exists() else RAIZ / "tmp"
        if carpeta != SALIDA_ESTUDIO:
            print(f"No está la carpeta del estudio ({SALIDA_ESTUDIO}).\n"
                  f"El libro se escribe en {carpeta} (ignorada por git).",
                  file=sys.stderr)
        salida = carpeta / nombre

    construir(salida, args.solo.upper(), args.anios,
              Path(args.db), Path(args.db_licitaciones))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
