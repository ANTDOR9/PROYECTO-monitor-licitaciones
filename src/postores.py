"""
Historial de postores: qué empresas se presentan, cuántas veces y contra quién.

El dato NO hace falta sacarlo de los PDF. El OCDS del OECE publica, por cada
proceso, el arreglo `parties` con un registro por participante y su rol:

    tenderer          se presentó al proceso
    supplier          además ganó
    buyer             la entidad que compra
    procuringEntity   la que conduce el procedimiento

Cada participante viene con su RUC en `identifier`, que es una clave estable:
no hay que normalizar nombres ni lidiar con "S.A.C." contra "SAC". En una
muestra de 13 procesos de paneles interactivos aparecieron 149 participaciones,
unas once por proceso.

Esto importa porque el camino obvio —bajar "Documentos de Presentación de
Propuestas", descomprimir el RAR, pasar los PDF por OCR y reconocer razones
sociales— cuesta horas de proceso, falla en la mayoría de los expedientes
escaneados y produce nombres sucios. Acá el mismo dato sale completo, limpio y
con RUC, recorriendo un archivo que ya está descargado.

Lo que este módulo NO puede dar, porque el OCDS no lo trae: la marca que
ofreció cada postor y el monto de cada oferta perdedora. Solo del ganador se
conoce el monto adjudicado.

Uso:
    python src/postores.py --anios 2023 2024 2025 2026
    python src/postores.py --solo INTERACTIVA --top 25
    python src/postores.py --informe --csv data/postores.csv
"""

from __future__ import annotations

import argparse
import collections
import gzip
import json
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

from extract import departamento as E_departamento              # noqa: E402
from buscar_actas import (categoria, config, descripcion, relevante,          # noqa: E402
                          id_proceso, texto_de)

#: Base de analisis, separada de licitaciones.db a proposito: ver la nota en
#: indexar_documentos.py. `data/analisis.db` esta en .gitignore.
DB = RAIZ / "data" / "analisis.db"

ESQUEMA = """
CREATE TABLE IF NOT EXISTS postores (
    ocid         TEXT NOT NULL,
    idproceso    TEXT,
    anio         INTEGER,
    fecha        TEXT,
    categoria    TEXT,
    entidad      TEXT,
    departamento TEXT,
    nomenclatura TEXT,
    descripcion  TEXT,
    ruc          TEXT NOT NULL,
    nombre       TEXT,
    gano         INTEGER NOT NULL DEFAULT 0,
    monto        REAL,
    PRIMARY KEY (ocid, ruc)
);
CREATE INDEX IF NOT EXISTS idx_postores_ruc ON postores(ruc);
CREATE INDEX IF NOT EXISTS idx_postores_cat ON postores(categoria);
CREATE INDEX IF NOT EXISTS idx_postores_anio ON postores(anio);
"""


def participantes(release: dict) -> list[dict]:
    """
    Un registro por empresa que se presentó, marcando si ganó.

    Se prefiere `parties` sobre `tender.tenderers` porque trae el RUC y los
    roles; `tenderers` se usa como respaldo cuando `parties` viene vacío.
    """
    salida: dict[str, dict] = {}

    for p in release.get("parties") or []:
        roles = [r.lower() for r in (p.get("roles") or [])]
        if "tenderer" not in roles and "supplier" not in roles:
            continue
        ident = (p.get("identifier") or {}).get("id") or ""
        ruc = "".join(ch for ch in str(ident) if ch.isdigit())
        if not ruc:
            # Sin RUC no hay clave fiable; se cae al nombre, marcándolo.
            ruc = "SIN-RUC:" + (p.get("name") or "")[:40]
        salida[ruc] = {"ruc": ruc,
                       "nombre": p.get("name") or
                                 (p.get("identifier") or {}).get("legalName") or "",
                       "gano": int("supplier" in roles)}

    if not salida:
        for t in (release.get("tender") or {}).get("tenderers") or []:
            ruc = "".join(ch for ch in str(t.get("id") or "") if ch.isdigit())
            if ruc:
                salida[ruc] = {"ruc": ruc, "nombre": t.get("name") or "", "gano": 0}

    # El ganador también puede venir solo en awards.
    for aw in release.get("awards") or []:
        if (aw.get("status") or "").lower() in ("cancelled", "unsuccessful"):
            continue
        for s in aw.get("suppliers") or []:
            ruc = "".join(ch for ch in str(s.get("id") or "") if ch.isdigit())
            if ruc and ruc in salida:
                salida[ruc]["gano"] = 1
            elif ruc:
                salida[ruc] = {"ruc": ruc, "nombre": s.get("name") or "", "gano": 1}
    return list(salida.values())


def monto_adjudicado(release: dict) -> float | None:
    for aw in release.get("awards") or []:
        v = (aw.get("value") or {}).get("amount")
        if v:
            return float(v)
    return None


def recolectar(anio: int, solo: str = "", ruta: Path | None = None) -> list[tuple]:
    archivo = ruta or RAIZ / "data" / f"{anio}.jsonl.gz"
    if not archivo.exists():
        print(f"  No está {archivo.name}; se omite. "
              f"Agregá el año a config.yaml y corré src/extract.py.", file=sys.stderr)
        return []

    claves, excluir = config()
    filas: list[tuple] = []
    procesos = 0

    with gzip.open(archivo, "rt", encoding="utf-8", errors="replace") as f:
        for linea in f:
            linea = linea.strip()
            if not linea:
                continue
            try:
                rel = json.loads(linea)
            except json.JSONDecodeError:
                continue

            if not relevante(rel):
                continue
            txt = texto_de(rel)
            cat = categoria(txt)
            if solo and cat != solo:
                continue

            gente = participantes(rel)
            if not gente:
                continue
            procesos += 1

            ocid = rel.get("ocid", "")
            t = rel.get("tender") or {}
            # La zona sale del propio release (parties[].address.region) y no de
            # un cruce con la tabla licitaciones: esa tabla solo cubre los anios
            # configurados en config.yaml, asi que un analisis de 2023-2024 se
            # quedaba sin zona en todas sus filas.
            base = (ocid, id_proceso(ocid), anio, (rel.get("date") or "")[:10], cat,
                    (rel.get("buyer") or {}).get("name", ""), E_departamento(rel),
                    t.get("title", ""), descripcion(rel)[:200])
            monto = monto_adjudicado(rel)
            for g in gente:
                filas.append((*base, g["ruc"], g["nombre"], g["gano"], monto))

    print(f"  {anio}: {procesos} proceso(s), {len(filas)} participación(es)")
    return filas


#: Columnas agregadas despues de la primera version. `CREATE TABLE IF NOT
#: EXISTS` no toca una tabla que ya existe, asi que una base creada con el
#: esquema viejo seguia sin la columna y el INSERT fallaba con
#: "table postores has no column named ...". Agregar la columna a mano es lo
#: que faltaba.
COLUMNAS_NUEVAS = [("departamento", "TEXT")]


def _migrar(con: sqlite3.Connection) -> None:
    existentes = {r[1] for r in con.execute("PRAGMA table_info(postores)")}
    for col, tipo in COLUMNAS_NUEVAS:
        if col not in existentes:
            con.execute(f"ALTER TABLE postores ADD COLUMN {col} {tipo}")
            print(f"  (se agrego la columna '{col}' a la tabla postores)")
    con.commit()


def guardar(filas: list[tuple], anios: list[int], db: Path) -> int:
    con = sqlite3.connect(db)
    con.executescript(ESQUEMA)
    _migrar(con)
    for a in anios:
        con.execute("DELETE FROM postores WHERE anio = ?", (a,))
    con.executemany(
        "INSERT OR REPLACE INTO postores (ocid, idproceso, anio, fecha, categoria, "
        "entidad, departamento, nomenclatura, descripcion, ruc, nombre, gano, monto) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", filas)
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM postores").fetchone()[0]
    con.close()
    return n


# --------------------------------------------------------------------------- #
# Informe
# --------------------------------------------------------------------------- #

def informe(db: Path, solo: str = "", top: int = 20) -> None:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    cond = "WHERE categoria = ?" if solo else ""
    arg = (solo,) if solo else ()

    procesos = con.execute(f"SELECT COUNT(DISTINCT ocid) FROM postores {cond}", arg).fetchone()[0]
    empresas = con.execute(f"SELECT COUNT(DISTINCT ruc) FROM postores {cond}", arg).fetchone()[0]
    partic = con.execute(f"SELECT COUNT(*) FROM postores {cond}", arg).fetchone()[0]
    anios = con.execute(f"SELECT MIN(anio), MAX(anio) FROM postores {cond}", arg).fetchone()

    print("=" * 78)
    print(f"HISTORIAL DE POSTORES{'  ·  ' + solo if solo else ''}")
    print("=" * 78)
    if not procesos:
        print("Sin datos. Corré primero la recolección.")
        return
    print(f"Periodo              : {anios[0]} – {anios[1]}")
    print(f"Procesos             : {procesos}")
    print(f"Empresas distintas   : {empresas}")
    print(f"Participaciones      : {partic}  ({partic / procesos:.1f} por proceso)")

    print(f"\nLas {top} empresas más presentes\n")
    print(f"{'empresa':<46}{'particip.':>10}{'ganadas':>9}{'éxito':>8}{'años':>6}")
    print("-" * 79)
    filas = con.execute(f"""
        SELECT nombre, COUNT(*) n, SUM(gano) g, COUNT(DISTINCT anio) a
        FROM postores {cond}
        GROUP BY ruc ORDER BY n DESC, g DESC LIMIT ?""", (*arg, top)).fetchall()
    for nombre, n, g, a in filas:
        print(f"{(nombre or '(sin nombre)')[:44]:<46}{n:>10}{g or 0:>9}"
              f"{(g or 0) / n:>7.0%}{a:>6}")

    # Concentración: cuánto del mercado tocan los más presentes.
    total_part = partic
    top10 = sum(r[1] for r in filas[:10])
    print(f"\nLas 10 más presentes concentran {top10 / total_part:.0%} de las "
          f"participaciones.")

    # Coincidencias: quién compite contra quién.
    print("\nParejas que más veces coinciden en el mismo proceso\n")
    por_proceso = collections.defaultdict(list)
    for ocid, nombre in con.execute(
            f"SELECT ocid, nombre FROM postores {cond}", arg):
        por_proceso[ocid].append(nombre or "?")
    pares = collections.Counter()
    for nombres in por_proceso.values():
        unicos = sorted(set(nombres))
        for i in range(len(unicos)):
            for j in range(i + 1, len(unicos)):
                pares[(unicos[i], unicos[j])] += 1
    for (a, b), n in pares.most_common(10):
        if n < 2:
            break
        print(f"   {n:>2}×  {a[:33]:<35} vs  {b[:33]}")
    if not pares or pares.most_common(1)[0][1] < 2:
        print("   (ninguna pareja coincide más de una vez)")

    print("\nPor año\n")
    print(f"{'año':<8}{'procesos':>10}{'empresas':>10}{'particip.':>11}")
    print("-" * 39)
    for a, p, e, pa in con.execute(f"""
            SELECT anio, COUNT(DISTINCT ocid), COUNT(DISTINCT ruc), COUNT(*)
            FROM postores {cond} GROUP BY anio ORDER BY anio""", arg):
        print(f"{a:<8}{p:>10}{e:>10}{pa:>11}")

    print("\n" + "=" * 78)
    print("El OCDS no publica la marca ofertada ni el monto de las ofertas que")
    print("perdieron: de los montos solo se conoce el del ganador.")
    print("=" * 78)
    con.close()


def exportar(db: Path, destino: Path, solo: str = "") -> None:
    import csv
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    cond = "WHERE categoria = ?" if solo else ""
    filas = con.execute(f"SELECT * FROM postores {cond} ORDER BY fecha DESC",
                        (solo,) if solo else ()).fetchall()
    con.close()
    if not filas:
        print("Nada que exportar.", file=sys.stderr)
        return
    destino.parent.mkdir(parents=True, exist_ok=True)
    with open(destino, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=filas[0].keys())
        w.writeheader()
        w.writerows(dict(r) for r in filas)
    print(f"{len(filas)} fila(s) en {destino}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--anios", nargs="+", type=int, default=[2025, 2026])
    ap.add_argument("--solo", default="", metavar="CATEGORIA",
                    help="quedarse con una categoría de producto (ej. INTERACTIVA)")
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--informe", action="store_true",
                    help="solo informar sobre lo ya recolectado, sin volver a recorrer")
    ap.add_argument("--csv", default="", help="exportar la tabla a un CSV")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()
    db = Path(args.db)
    solo = args.solo.upper()

    if not args.informe:
        print("Recolectando participantes del OCDS ...")
        todas: list[tuple] = []
        for anio in args.anios:
            todas += recolectar(anio, solo)
        if todas:
            n = guardar(todas, args.anios, db)
            print(f"\nTabla 'postores': {n} participaciones almacenadas.\n")
        else:
            print("\nNo se recolectó nada.\n")

    informe(db, solo, args.top)
    if args.csv:
        exportar(db, Path(args.csv), solo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
