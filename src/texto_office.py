"""
Texto de documentos de Word, para cuando el expediente no trae PDF.

El SEACE publica bases en .docx y, en los procesos viejos, en el .doc binario
de Word 97-2003. El resto del proyecto solo sabía leer PDF, así que esos
expedientes morían en "no trae ningún PDF legible" aunque el requerimiento
estuviera ahí.

No se usa ninguna librería externa a propósito: el tablero corre también en
Streamlit Cloud, donde cada dependencia nueva es una build más frágil.

  .docx   es un ZIP; el texto vive en word/document.xml y se saca quitando
          las etiquetas, respetando los saltos de párrafo.

  .doc    es un contenedor OLE2 binario. En vez de interpretar el formato se
          rescatan las rachas largas de caracteres imprimibles, en UTF-16LE y
          en cp1252, que es como Word guarda el texto según el documento. Sale
          con ruido —nombres de fuentes, formatos de celda— pero el
          requerimiento queda legible, que es lo que el extractor necesita.
          No se intenta reconstruir el orden del documento ni sus tablas.
"""

from __future__ import annotations

import html
import re
import zipfile
from pathlib import Path

#: Tipos que este módulo sabe convertir a texto.
TIPOS = ("docx", "ole2")

_PARRAFO = re.compile(r"</w:p>", re.I)
_SALTO = re.compile(r"<w:(?:br|tab)\b[^>]*/?>", re.I)
_ETIQUETA = re.compile(r"<[^>]+>")

_UTF16 = re.compile(rb"(?:[\x20-\xff]\x00){12,}")
_CP1252 = re.compile(rb"[\x20-\x7e\xa0-\xff]{12,}")


def _de_docx(ruta: Path) -> str:
    with zipfile.ZipFile(ruta) as z:
        nombres = [n for n in z.namelist()
                   if n == "word/document.xml" or n.startswith("word/header")
                   or n.startswith("word/footer")]
        if "word/document.xml" not in nombres:
            return ""
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    xml = _SALTO.sub(" ", xml)
    xml = _PARRAFO.sub("\n", xml)
    return html.unescape(_ETIQUETA.sub("", xml))


def _de_ole2(datos: bytes) -> str:
    """Rescata el texto imprimible de un .doc binario.

    Las dos codificaciones se buscan por separado y después se descartan los
    tramos cp1252 que caen dentro de un tramo UTF-16 ya tomado: sin eso el
    mismo párrafo sale dos veces y las palabras quedan con la primera letra
    repetida ("TTecnología"), que rompe los anclajes de palabra del extractor.
    """
    tomados: list[tuple[int, int, str]] = []
    for m in _UTF16.finditer(datos):
        tomados.append((m.start(), m.end(), m.group().decode("utf-16-le", "ignore")))
    ocupado = [(a, b) for a, b, _ in tomados]
    for m in _CP1252.finditer(datos):
        if any(a < m.end() and m.start() < b for a, b in ocupado):
            continue
        tomados.append((m.start(), m.end(), m.group().decode("cp1252", "ignore")))
    tomados.sort()
    texto = "\n".join(t for _, _, t in tomados)
    return re.sub(r"[ \t]{2,}", " ", texto)


def texto(ruta: str | Path, tipo: str = "") -> str:
    """Devuelve el texto de un .docx o .doc. Cadena vacía si no se pudo."""
    ruta = Path(ruta)
    tipo = tipo or ("docx" if ruta.suffix.lower() == ".docx" else "ole2")
    try:
        if tipo == "docx":
            return _de_docx(ruta)
        if tipo == "ole2":
            return _de_ole2(ruta.read_bytes())
    except Exception:                                            # noqa: BLE001
        return ""
    return ""


if __name__ == "__main__":
    import sys
    for arg in sys.argv[1:]:
        t = texto(arg)
        print(f"{Path(arg).name[:56]:<58} {len(t):>8} caracteres")
