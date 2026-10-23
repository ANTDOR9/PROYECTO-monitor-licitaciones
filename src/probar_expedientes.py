"""
Corre el comparador sobre varios expedientes seguidos y dice cuál falla y por qué.

El tablero analiza de a un expediente y muestra el error en pantalla, así que
cuando algo falla hay que ir probando a mano. Este script hace lo mismo que el
botón «Analizar expediente», pero en lote y sin interfaz: descarga, abre, lee,
extrae y evalúa, y deja una línea por proceso con el resultado.

Sirve para dos cosas: ver de un vistazo qué proporción del histórico es
analizable, y reproducir un fallo puntual con el detalle completo.

Uso:
    python src/probar_expedientes.py                     # los 8 más recientes
    python src/probar_expedientes.py --limite 20
    python src/probar_expedientes.py --desde 2026-01-01
    python src/probar_expedientes.py --filecode <uuid>   # uno solo, con detalle
    python src/probar_expedientes.py --categoria TELEVISOR
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import panel_expediente as pe                                    # noqa: E402

DB = RAIZ / "data" / "licitaciones.db"


def procesos(db: Path, categoria: str, desde: str, limite: int) -> list[dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    analisis = pe.ruta_analisis(db)
    if analisis != str(db):
        con.execute(f"ATTACH DATABASE 'file:{analisis}?mode=ro' AS an")
        tabla = "an.documentos"
    else:
        tabla = "documentos"
    con.row_factory = sqlite3.Row
    sql = (f"SELECT l.ocid, l.fecha, l.nomenclatura, l.entidad "
           f"FROM {tabla} d JOIN licitaciones l ON l.ocid = d.ocid "
           f"WHERE d.categoria = ? AND l.fecha >= ? "
           f"GROUP BY l.ocid ORDER BY l.fecha DESC LIMIT ?")
    filas = [dict(r) for r in con.execute(sql, (categoria, desde, limite))]
    con.close()
    return filas


def una(filecode: str, titulo: str = "") -> dict:
    return pe.analizar(filecode, titulo)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limite", type=int, default=8)
    ap.add_argument("--desde", default="2000-01-01")
    ap.add_argument("--categoria", default="INTERACTIVA")
    ap.add_argument("--filecode", default="")
    ap.add_argument("--db", default=str(DB))
    args = ap.parse_args()
    db = Path(args.db)

    if args.filecode:
        r = una(args.filecode)
        print(f"\nfilecode      : {args.filecode}")
        for k in ("ok", "paso", "nombre", "tipo", "bytes", "documento", "clase",
                  "origen", "paginas", "cobertura", "motivo"):
            if k in r:
                print(f"{k:<14}: {r[k]}")
        if r.get("archivos"):
            print(f"{'archivos':<14}: {', '.join(r['archivos'][:10])}")
        if r.get("specs"):
            print("specs         :")
            for k, v in r["specs"].items():
                print(f"   {k:<26} {v}")
        return 0 if r.get("ok") else 1

    filas = procesos(db, args.categoria.upper(), args.desde, args.limite)
    if not filas:
        print("No hay procesos indexados con ese filtro.")
        return 2

    print(f"Probando {len(filas)} expediente(s) de categoría {args.categoria.upper()}.\n")
    ok = 0
    fallos: list[tuple] = []
    for i, p in enumerate(filas, 1):
        docs = pe.documentos_de(db, p["ocid"])
        if not docs:
            print(f"{i:>2}. {p['fecha'][:10]}  {p['nomenclatura'][:26]:<26} "
                  f"SIN DOCUMENTOS")
            fallos.append((p["nomenclatura"], "sin documentos indexados", ""))
            continue
        d = docs[0]
        r = una(d["filecode"], d["titulo"])
        if r.get("ok"):
            ok += 1
            print(f"{i:>2}. {p['fecha'][:10]}  {p['nomenclatura'][:26]:<26} "
                  f"OK   {r['cobertura']:.0%} de cobertura, "
                  f"{len(r['specs'])} campos, origen {r.get('origen')}")
        else:
            print(f"{i:>2}. {p['fecha'][:10]}  {p['nomenclatura'][:26]:<26} "
                  f"FALLA en «{r.get('paso')}»")
            print(f"     {r.get('motivo', '')[:160]}")
            fallos.append((p["nomenclatura"], r.get("paso"), r.get("motivo", "")))

    print("\n" + "=" * 70)
    print(f"{ok} de {len(filas)} expedientes se analizaron completos.")
    if fallos:
        print("\nFallos por paso:")
        pasos: dict[str, int] = {}
        for _, paso, _ in fallos:
            pasos[paso] = pasos.get(paso, 0) + 1
        for paso, n in sorted(pasos.items(), key=lambda x: -x[1]):
            print(f"   {paso:<22} {n}")
        print("\nPara ver uno en detalle:")
        print("   python src/probar_expedientes.py --filecode <uuid del documento>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
