"""
Extractor FASE 2 - Oportunidades de negocio VIGENTES (SEACE)
------------------------------------------------------------
Consume la API publica de "Oportunidades de Negocio v2.0" del OECE/SEACE
(procedimientos con registro de participantes vigente = a los que se puede
postular AHORA), filtra el rubro Brighter y los guarda en SQLite (tabla
'vigentes'). Fuente descubierta: prod4.seace.gob.pe:8086 (sin token).

Uso:
    python src/vigentes.py                 # descarga en vivo
    python src/vigentes.py --archivo x.json  # usa un JSON local (pruebas)
"""
import argparse, json, os, re, sqlite3, unicodedata
import yaml

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
API = ("https://prod4.seace.gob.pe:8086/api/oportunidades/"
       "codObjeto/codDepartamento/sintesisProceso/codTipoProceso/0/0/0/0")

def cfg():
    return yaml.safe_load(open(os.path.join(BASE, "config.yaml"), encoding="utf-8"))

def norm(t):
    if not t: return ""
    t = unicodedata.normalize("NFKD", str(t))
    return "".join(c for c in t if not unicodedata.combining(c)).lower()

def coincide(texto, claves, excluir):
    # palabra completa (\b), no subcadena -- evita que "monitor" pesque
    # "monitoreo" (ver detalle del bug en extract.py::coincide).
    if any(x in texto for x in excluir): return False
    for clave in claves:
        toks = [t for t in clave.split() if len(t) >= 4]
        if toks and all(re.search(rf"\b{re.escape(t)}(?:es|s)?\b", texto) for t in toks): return True
    return False

def descarga():
    import requests, urllib3
    urllib3.disable_warnings()  # el server usa cert propio en :8086
    r = requests.get(API, timeout=120, verify=False)
    r.raise_for_status()
    return r.json()

def fila(o):
    return {
        "id": o.get("idProcedimiento"),
        "nomenclatura": o.get("nomenclatura", ""),
        "entidad": o.get("detEntidad", ""),
        "objeto": o.get("detObjeto", ""),
        "tipo_proceso": o.get("detTipoProceso", ""),
        "descripcion": o.get("detItem", "") or o.get("sintesisProceso", ""),
        "valor_referencial": o.get("valorReferencial"),
        "moneda": o.get("monedaProceso", ""),
        "fecha_convocatoria": o.get("fechaConvocatoria", ""),
        "fecha_fin_inscripcion": o.get("fechaFin", ""),
        "fecha_presentacion": o.get("fechaPresentacionPropuestas", ""),
        "ubigeo": o.get("ubigeo"),
        "enlace": f"https://prod4.seace.gob.pe/openegocio/#/ficha/idProceso/{o.get('idProcedimiento')}",
        # Codigo de archivo de las bases. La API lo devuelve en TODOS los
        # procedimientos y se verifico que sirve con un GET directo al mismo
        # servlet que usa descargar_documento.py. Es lo que permite leer el
        # requerimiento el mismo dia de la convocatoria, sin esperar las dos
        # semanas que tarda el paquete anual del OCDS en publicar el expediente.
        "documento_base": o.get("documentoBase") or "",
    }

#: Columnas agregadas despues de la primera version. CREATE TABLE IF NOT EXISTS
#: no altera una tabla que ya existe, asi que hay que agregarlas a mano o el
#: INSERT falla contra una base vieja.
COLUMNAS_NUEVAS = [("documento_base", "TEXT")]

def tabla(con):
    con.execute("""CREATE TABLE IF NOT EXISTS vigentes(
        id INTEGER PRIMARY KEY, nomenclatura TEXT, entidad TEXT, objeto TEXT,
        tipo_proceso TEXT, descripcion TEXT, valor_referencial REAL, moneda TEXT,
        fecha_convocatoria TEXT, fecha_fin_inscripcion TEXT, fecha_presentacion TEXT,
        ubigeo INTEGER, enlace TEXT, documento_base TEXT)""")
    existentes = {r[1] for r in con.execute("PRAGMA table_info(vigentes)")}
    for col, tipo in COLUMNAS_NUEVAS:
        if col not in existentes:
            con.execute(f"ALTER TABLE vigentes ADD COLUMN {col} {tipo}")
            print(f"  (se agrego la columna '{col}' a la tabla vigentes)")
    con.commit()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--archivo")
    ap.add_argument("--crudo", metavar="RUTA",
                    help="guarda la respuesta tal cual la devuelve la API y lista "
                         "los campos de un registro. Sirve para ver si la API trae "
                         "datos que el script todavia no guarda (por ejemplo los "
                         "codigos de archivo de los documentos).")
    a = ap.parse_args()
    c = cfg()
    data = json.load(open(a.archivo, encoding="utf-8")) if a.archivo else descarga()
    if isinstance(data, dict):
        data = data.get("content") or data.get("data") or []

    if a.crudo:
        os.makedirs(os.path.dirname(os.path.abspath(a.crudo)) or ".", exist_ok=True)
        with open(a.crudo, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=1)
        print(f"Respuesta cruda guardada en {a.crudo} ({len(data)} registros).")
        if data:
            print("\nCampos que devuelve la API por registro:")
            for k in sorted(data[0]):
                print(f"   {k:<34} {str(data[0][k])[:58]}")
            print()
    # NOTA (cambio v2): ya NO se pre-filtra por palabra clave aqui. Se guarda
    # TODO lo que trae la API -- el filtrado por etiquetas ahora vive 100% en
    # el dashboard (igual que streamlit_app.py), asi el panel editable de
    # etiquetas puede mostrar mas o menos resultados sin depender de una
    # nueva extraccion.
    db = os.path.join(BASE, c["salida"]["base_datos"])
    os.makedirs(os.path.dirname(db), exist_ok=True)
    con = sqlite3.connect(db); tabla(con)
    con.execute("DELETE FROM vigentes")  # vigentes se reemplaza cada corrida

    filas, vistos = [], set()
    for o in data:
        idp = o.get("idProcedimiento")
        if idp in vistos: continue
        vistos.add(idp)
        filas.append(fila(o))
    if filas:
        # Se nombran las columnas: con VALUES a secas, agregar una columna
        # cambia el orden posicional y el INSERT empieza a guardar los datos
        # corridos de lugar sin avisar.
        con.executemany("""INSERT OR REPLACE INTO vigentes
            (id, nomenclatura, entidad, objeto, tipo_proceso, descripcion,
             valor_referencial, moneda, fecha_convocatoria, fecha_fin_inscripcion,
             fecha_presentacion, ubigeo, enlace, documento_base)
            VALUES
            (:id,:nomenclatura,:entidad,:objeto,:tipo_proceso,:descripcion,
             :valor_referencial,:moneda,:fecha_convocatoria,:fecha_fin_inscripcion,
             :fecha_presentacion,:ubigeo,:enlace,:documento_base)""", filas)
        con.commit()
    # La API devuelve un registro por ITEM, no por procedimiento: varios
    # comparten idProcedimiento y la tabla los colapsa. Decir "3121
    # guardados" cuando en la tabla quedan 2601 es informar de mas.
    print(f"Total vigentes: {len(filas)} procedimientos guardados "
          f"({len(data)} registros en la API, uno por item). "
          f"Sin pre-filtrar; el dashboard filtra por etiquetas.")
    con.close()

if __name__ == "__main__":
    main()
