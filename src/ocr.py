"""
Capa de OCR para bases integradas escaneadas.

El 55 % de los documentos de la muestra son imágenes sin capa de texto.
Sin esta etapa, más de la mitad del expediente es invisible al extractor.

Se aplica OCR solo cuando hace falta: extraer texto nativo es dos órdenes de
magnitud más rápido, de modo que rasterizar siempre sería desperdicio.

DOS ERRORES QUE ESTA VERSIÓN CORRIGE, los dos medidos sobre bases reales:

1. La decisión de aplicar OCR era del documento entero y se tomaba mirando las
   primeras diez páginas. Las bases peruanas modernas son un PDF nativo de
   setenta u ochenta páginas con el requerimiento técnico insertado como
   escaneo en el medio. Como las primeras diez páginas sí tienen texto, el
   documento se declaraba "nativo" y las páginas escaneadas —justo las que
   importan— se leían vacías. Ahora la decisión es por página.

2. Se leían solo las primeras veinte páginas. En las bases de Torata
   (LP-SM-2-2026-OC/MDT-1, 79 páginas) las especificaciones técnicas están en
   las páginas 26 a 30, de modo que el recorte las dejaba fuera aunque el OCR
   hubiera estado instalado. Ahora se leen todas.
"""

from __future__ import annotations
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pdfplumber

#: Debajo de este promedio de caracteres por página se asume documento escaneado.
UMBRAL_CHARS_POR_PAGINA = 300

#: Debajo de esto, una página suelta se considera escaneada. Es más bajo que el
#: umbral del documento porque una página de encabezado y pie, sin cuerpo, ronda
#: los 140 caracteres: en las bases de Torata las cinco páginas del
#: requerimiento traían 137 cada una.
UMBRAL_PAGINA = 200

#: Tope de páginas a rasterizar en un mismo documento. Un expediente entero
#: escaneado de doscientas páginas tardaría más de lo que nadie espera frente
#: a una pantalla.
MAX_PAGINAS_OCR = 40

#: Resolución de rasterizado.
#:
#: Estaba en 100 ppp por una medición anterior que daba el mismo texto a 100,
#: 150 y 200 ppp. Esa medición se hizo sobre escaneos limpios y no se sostiene
#: en los que trae el SEACE, que suelen venir torcidos y con sello encima.
#: Medido sobre las cinco páginas del requerimiento de Torata
#: (LP-SM-2-2026-OC/MDT-1):
#:
#:    100 ppp   10 326 caracteres, 22 s,  4 de 14 términos técnicos,  6 % de cobertura
#:    150 ppp   15 820 caracteres, 34 s, 13 de 14 términos técnicos, 61 % de cobertura
#:    200 ppp   16 219 caracteres, 43 s, 13 de 14 términos técnicos, 56 % de cobertura
#:
#: A 100 ppp el OCR corría y devolvía basura: "Consrasción: MAI Y protección"
#: en vez de "Construcción: Metálico + protección térmica". Los doce segundos
#: extra de 150 ppp son la diferencia entre un expediente ilegible y uno
#: analizable; 200 ppp ya no agrega nada.
DPI = 150

#: Trabajadores para el OCR. Es un proceso ligado a CPU, uno por núcleo.
WORKERS = max(1, (os.cpu_count() or 2))

#: El OCR cuesta entre uno y dos órdenes de magnitud más que leer texto nativo,
#: de modo que su resultado se persiste y no se recalcula entre ejecuciones.
CACHE = Path(__file__).parent / ".cache_ocr"


def hay_ocr() -> bool:
    """Si están las dos herramientas externas que el OCR necesita."""
    return bool(shutil.which("tesseract") and shutil.which("pdftoppm"))


def paginas_nativas(pdf_path: str | Path,
                    max_paginas: int | None = None) -> list[str]:
    """Texto de la capa nativa, página por página."""
    with pdfplumber.open(pdf_path) as pdf:
        paginas = pdf.pages if max_paginas is None else pdf.pages[:max_paginas]
        return [(p.extract_text() or "") for p in paginas]


def necesita_ocr(pdf_path: str | Path, muestra: int = 10) -> tuple[bool, float]:
    """Indica si el documento carece de capa de texto utilizable.

    Se conserva porque `comprobar_entorno.py` y los diagnósticos la usan para
    describir un documento de un vistazo. La decisión real de qué rasterizar
    ya no pasa por acá, sino por `paginas_escaneadas`.
    """
    with pdfplumber.open(pdf_path) as pdf:
        paginas = pdf.pages[:muestra]
        if not paginas:
            return True, 0.0
        chars = sum(len(p.extract_text() or "") for p in paginas)
        cpp = chars / len(paginas)
    return cpp < UMBRAL_CHARS_POR_PAGINA, cpp


def paginas_escaneadas(nativo: list[str]) -> list[int]:
    """Índices (base 0) de las páginas sin texto aprovechable."""
    return [i for i, t in enumerate(nativo) if len(t.strip()) < UMBRAL_PAGINA]


def _rangos(indices: list[int]) -> list[tuple[int, int]]:
    """Agrupa índices consecutivos en rangos de páginas (base 1, inclusivos)."""
    rangos: list[tuple[int, int]] = []
    for i in sorted(indices):
        pagina = i + 1
        if rangos and pagina == rangos[-1][1] + 1:
            rangos[-1] = (rangos[-1][0], pagina)
        else:
            rangos.append((pagina, pagina))
    return rangos


def _ocr_una(args) -> tuple[int, str]:
    i, img, idioma = args
    r = subprocess.run(
        ["tesseract", str(img), "stdout", "-l", idioma, "--psm", "6"],
        capture_output=True, text=True, timeout=180,
    )
    return i, r.stdout


def texto_ocr(pdf_path: str | Path, paginas: int | None = None,
              idioma: str = "spa") -> list[str]:
    """Rasteriza desde la página 1 y aplica OCR. Una entrada por página."""
    indices = list(range(paginas or 0)) or None
    if indices is None:
        with pdfplumber.open(pdf_path) as pdf:
            indices = list(range(len(pdf.pages)))
    por_pagina = texto_ocr_paginas(pdf_path, indices, idioma)
    return [por_pagina.get(i, "") for i in indices]


def texto_ocr_paginas(pdf_path: str | Path, indices: list[int],
                      idioma: str = "spa") -> dict[int, str]:
    """Rasteriza y reconoce SOLO las páginas pedidas (índices base 0)."""
    if not indices:
        return {}
    if not hay_ocr():
        raise RuntimeError("tesseract y pdftoppm son necesarios para el OCR")

    from concurrent.futures import ProcessPoolExecutor

    pdf_path = str(pdf_path)
    salida: dict[int, str] = {}
    with tempfile.TemporaryDirectory() as tmp:
        tareas = []
        for desde, hasta in _rangos(indices):
            prefijo = f"{tmp}/r{desde}"
            subprocess.run(
                ["pdftoppm", "-r", str(DPI), "-gray", "-png",
                 "-f", str(desde), "-l", str(hasta), pdf_path, prefijo],
                check=True, capture_output=True, timeout=600)
            imgs = sorted(Path(tmp).glob(f"r{desde}-*.png"))
            for n, img in enumerate(imgs):
                tareas.append((desde - 1 + n, img, idioma))
        with ProcessPoolExecutor(max_workers=WORKERS) as ex:
            for i, txt in ex.map(_ocr_una, tareas):
                salida[i] = txt
    return salida


def _clave(pdf_path: Path, indices: list[int]) -> Path:
    st = pdf_path.stat()
    marca = f"{min(indices)}-{max(indices)}-{len(indices)}" if indices else "todo"
    return CACHE / f"{pdf_path.stem}_{st.st_size}_{int(st.st_mtime)}_{marca}.json"


def texto_documento(pdf_path: str | Path, max_paginas: int | None = None,
                    idioma: str = "spa") -> tuple[list[str], str]:
    """Devuelve (texto por página, origen), aplicando OCR solo donde falta.

    `origen` vale:
      nativo          todas las páginas traían texto
      ocr             ninguna lo traía
      mixto           texto nativo más OCR de las páginas escaneadas
      sin_ocr         hay páginas escaneadas pero faltan tesseract/pdftoppm
    """
    pdf_path = Path(pdf_path)
    nativo = paginas_nativas(pdf_path, max_paginas)
    if not nativo:
        return [], "nativo"

    faltan = paginas_escaneadas(nativo)
    if not faltan:
        return nativo, "nativo"
    if not hay_ocr():
        return nativo, "sin_ocr"

    faltan = faltan[:MAX_PAGINAS_OCR]
    CACHE.mkdir(exist_ok=True)
    ruta = _clave(pdf_path, faltan)
    if ruta.exists():
        reconocido = {int(k): v for k, v in
                      json.loads(ruta.read_text(encoding="utf-8")).items()}
    else:
        reconocido = texto_ocr_paginas(pdf_path, faltan, idioma)
        ruta.write_text(json.dumps({str(k): v for k, v in reconocido.items()},
                                   ensure_ascii=False), encoding="utf-8")

    salida = list(nativo)
    for i, txt in reconocido.items():
        if len(txt.strip()) > len(salida[i].strip()):
            salida[i] = txt
    return salida, ("ocr" if len(faltan) == len(nativo) else "mixto")
