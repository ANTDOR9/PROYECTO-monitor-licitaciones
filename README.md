# Monitor de Licitaciones — Brighter Peru

Rastrea oportunidades públicas y compras del Estado peruano relacionadas al
rubro de Brighter Peru: pantallas interactivas, pizarras digitales, kioscos
y tótems, y equipamiento audiovisual. Combina **cuatro fuentes** con
metodologías distintas — SEACE (licitaciones abiertas), Perú Compras
(catálogo de Acuerdo Marco), PetroPerú (avisos tempranos de régimen propio)
y Banco de la Nación (bases de licitaciones y concursos de régimen propio) —
y las presenta en un dashboard interactivo.

🔗 Demo en vivo: https://brighter-licitaciones.streamlit.app

## Instalación
```bash
pip install -r requirements.txt
```

## Uso

1) Extraer histórico de SEACE (descarga los años definidos en `config.yaml` y llena la base):
```bash
python src/extract.py
```
O probar sin descargar, con la muestra incluida:
```bash
python src/_muestra.py            # genera data/muestra.jsonl
python src/extract.py --archivo data/muestra.jsonl
```

2) Traer oportunidades **vigentes** de SEACE (a las que se puede postular ahora):
```bash
python src/vigentes.py
```

3) Traer catálogo de **Perú Compras** (Acuerdo Marco — compras ya ejecutadas):
```bash
python src/perucompras.py
```

4) Traer avisos de **PetroPerú** (señal temprana, antes de proceso formal):
```bash
python src/petroperu.py
```

5) Traer bases de **Banco de la Nación** (licitaciones y concursos, régimen propio):
```bash
python src/bnacion.py
```

6) Ver el dashboard:
```bash
python -m streamlit run src/dashboard.py
```
(usa `python -m streamlit` si el comando `streamlit` no está en el PATH de tu terminal)

## Antes de empezar: comprobar el entorno

```bash
python src/comprobar_entorno.py
```

Dice qué paquetes y herramientas faltan, con el comando exacto para
instalarlos, y separa lo imprescindible de lo opcional. El dashboard y los
cuatro extractores funcionan sin OCR ni lector de RAR; esos dos solo hacen
falta para el pipeline de especificaciones.

## Pipeline de especificaciones (bloque nuevo)

Va de una licitación a la pregunta "¿qué marcas podían presentarse?".

7) Listar los documentos de adjudicación de los procesos del rubro, a partir
   de los paquetes OCDS ya descargados en `data/`:
```bash
python src/buscar_actas.py --anio 2025 --solo INTERACTIVA
```
Escribe un CSV con el `fileCode` de cada documento. Sin `--solo` incluye todo
el rubro audiovisual, que es mucho más amplio que los paneles interactivos.

8) Descargar y abrir un documento del expediente (PDF suelto, .rar, .zip o
   contenedor anidado: se detecta por firma de bytes, no por extensión):
```bash
python src/descargar_documento.py --filecode <uuid> --extraer tmp/exp1
python src/contenedores.py --diagnostico      # con qué se pueden abrir los .rar
```

9) Indexar los documentos del expediente, una sola vez por año. Deja la tabla
   `documentos` en la base para que el tablero responda al instante:
```bash
python src/indexar_documentos.py --anios 2025 2026
```

10) Historial de postores: qué empresas se presentan, cuántas veces y contra
    quién. Sale del OCDS, sin descargar ni leer PDF:
```bash
python src/postores.py --anios 2023 2024 2025 2026 --solo INTERACTIVA
python src/postores.py --informe --csv data/postores.csv
```

11) Reporte de inteligencia competitiva en Excel, a partir de lo que dejó el
    paso anterior. Un libro con una hoja por nivel de agregación: empresas,
    participaciones crudas, procesos, coincidencias entre empresas, indicadores
    y matriz año por año.
```bash
python src/reporte_competencia.py
python src/reporte_competencia.py --anios 2024 2025 2026
python src/reporte_competencia.py --salida "C:/ruta/archivo.xlsx"
```
    El libro **no** se escribe dentro del repositorio. Por defecto va a la
    carpeta del estudio de mercado y, si no existe, a `tmp/`, que está
    ignorada por git. Dos lecturas obligatorias antes de usar sus números:
    un año con muchas menos participaciones por proceso no tuvo menos
    competencia, publicó peor la lista de postores; y la columna «exceso sobre
    lo esperado» mide rareza estadística frente a un reparto al azar, no
    concertación, porque ese modelo ignora que las empresas se concentran por
    región, tipo de proceso y rango de monto. Sirve para priorizar qué
    expediente leer. La hoja 00 del propio libro repite ambas advertencias.

12) Evaluar un requerimiento contra las fichas de BTOUCH y de la competencia:
```bash
python src/fichas.py                                    # inventario de fichas
python src/evaluar_licitacion.py --demo                 # ejemplo de 86"
python src/evaluar_licitacion.py --json data/resultado_extractor.json --resumen
python src/evaluar_licitacion.py --json data/resultado_extractor.json --expediente 7
```

Qué hace cada módulo nuevo:

- `src/comprobar_entorno.py` — qué falta instalar en este equipo
- `src/contenedores.py` — identifica y abre lo que devuelve SEACE, y elige qué
  documento del expediente le interesa al extractor
- `src/descargar_documento.py` — descarga por `fileCode` (GET directo, sin sesión)
- `src/buscar_actas.py` — localiza documentos de adjudicación y clasifica por
  categoría de producto
- `src/ocr.py` — texto nativo o OCR según haga falta, con caché
- `src/extractor.py` — saca el requerimiento técnico en 18 campos
- `src/fichas.py` — fichas estructuradas de BTOUCH y competidores
- `src/evaluar_licitacion.py` — compara exigido contra ficha y dictamina
- `src/indexar_documentos.py` — vuelca los documentos del OCDS a la tabla `documentos`
- `src/postores.py` — historial de participantes por empresa, con RUC
- `src/reporte_competencia.py` — libro Excel de inteligencia competitiva a
  partir de la tabla `postores`, con cobertura por año y coincidencias entre
  empresas; escribe fuera del repositorio
- `src/panel_expediente.py` — el panel del tablero: ficha del proceso y, detrás de
  un botón, descarga, lectura y comparación contra BTOUCH

Leer la sección 11 de `PROJECT_CONTEXT.md` antes de usarlo: la marca del
producto adjudicado **no** se publica en los documentos de SEACE, y el motor de
evaluación existe precisamente porque ese dato hay que inferirlo.

## Configuración
Edita `config.yaml` para cambiar palabras clave, exclusiones, años y el
Acuerdo Marco de Perú Compras a monitorear — sin tocar código.

## Estructura
- `config.yaml` — palabras clave, exclusiones y filtros (editable por no-programadores)
- `src/extract.py` — extractor OCDS histórico (SEACE/OECE) + dedup + tipo de licitación + SQLite
- `src/vigentes.py` — extractor de oportunidades vigentes (API SEACE en vivo)
- `src/perucompras.py` — extractor de órdenes por Acuerdo Marco (Perú Compras)
- `src/petroperu.py` — extractor de avisos de contratación futura (PetroPerú)
- `src/bnacion.py` — extractor de bases de licitaciones y concursos (Banco de la Nación)
- `src/dashboard.py` — tablero Streamlit (4 fuentes, búsqueda avanzada por fuente) + export Excel con formato
- `src/_muestra.py` — genera datos de prueba
- `src/reporte_competencia.py` — reporte Excel de competencia (salida fuera del repositorio)
- `data/` — base SQLite (`licitaciones.db`, sí se versiona) y descargas crudas (no se versionan)
- `.github/workflows/` — GitHub Actions que actualizan los datos automáticamente
- `PROJECT_CONTEXT.md` — memoria técnica completa del proyecto (leer primero)

## Qué se ha logrado

**Fuente SEACE (licitaciones abiertas)**
- Extractor histórico OCDS con deduplicación por `ocid` y purga de filas
  obsoletas en cada corrida (sin arrastrar datos de configuraciones viejas).
- Extractor de oportunidades vigentes contra la API pública en vivo de SEACE.
- Matcher de palabras clave con límites de palabra reales (evita falsos
  positivos como "monitor" dentro de "monitoreo") y tolerancia a plurales.
- Aislamiento de coincidencias por ítem: en procesos con múltiples ítems no
  relacionados, ya no se permite que dos ítems distintos se combinen para
  simular una coincidencia falsa.
- Detección best-effort de marca/modelo por regex sobre el texto del ítem.
- Campo "Tipo de licitación" (Licitación Pública, Adjudicación Simplificada,
  etc.), tomado del método de contratación OCDS o deducido del prefijo de
  la nomenclatura si el dato no viene en el registro.

**Fuente Perú Compras (Acuerdo Marco)**
- Investigación y documentación del endpoint real
  (`consultaOrdenes`, formato de body y respuesta propietarios — no JSON).
- Extractor funcional para el Acuerdo Marco 322-BIENES (Equipos Multimedia
  y Accesorios).
- Aclarado en el dashboard que esto es catálogo de compras ya ejecutadas
  (inteligencia de mercado), no licitaciones abiertas a las que postular.
- Columna "Tipo de contratación" visible en el dashboard.

**Fuente PetroPerú (señal temprana)**
- Extractor de avisos de contratación futura (antes de que exista un
  proceso formal en el portal propio de PetroPerú).
- Parser de fechas en español (formato "17-Ago-2026") propio del portal.

**Fuente Banco de la Nación (régimen propio)**
- Extractor de bases de licitaciones y concursos ya publicadas.
- Columna "Tipo de proceso" visible en el dashboard.

**Dashboard**
- Navegación por botones entre las 4 fuentes (metodologías distintas,
  separadas intencionalmente).
- Panel editable de palabras clave/exclusiones directamente desde la barra
  lateral, sin tocar `config.yaml`.
- Búsqueda avanzada por fuente: tamaño de pantalla en pulgadas, categoría
  de producto, precio/monto, proveedor o marca, rango de fechas y, según
  la fuente, departamento o tipo de proceso — todos combinables en AND,
  con contador "X de Y" resultados.
- Estado con colores (verde/amarillo/rojo) y columna de días restantes en
  SEACE Vigentes; estado de entrega coloreado en Perú Compras.
- Exportación a Excel con formato: cabecera en color y negrita, columnas
  auto-ajustadas, panel superior fijo, autofiltro y el mismo color
  condicional por fila que se ve en pantalla (donde aplica).
- Nota conocida: los filtros de tamaño/categoría solo leen el texto que
  trae el propio registro de la convocatoria (título/objeto), no el
  contenido de los PDFs adjuntos (bases, ficha técnica) — ver
  `PROJECT_CONTEXT.md` para más detalle.

**Infraestructura**
- Repositorio limpio: archivos pesados de descarga cruda (`.jsonl.gz`,
  100MB+) fuera de git vía `.gitignore`; la base filtrada (`licitaciones.db`)
  sí se versiona para que el despliegue en Streamlit Cloud tenga datos
  sin necesidad de procesar nada en la nube.
- Desplegado en Streamlit Cloud, actualizable con cada `git push`.
- Automatización con GitHub Actions: actualización diaria de oportunidades
  vigentes/catálogos y actualización semanal del histórico completo,
  con commit automático del `.db` al repositorio.

## Pendiente
- Extraer texto de los PDFs adjuntos (bases, ficha técnica) para que la
  búsqueda avanzada también encuentre especificaciones que no están en el
  título/objeto del registro.
- Alerta por correo si un extractor falla.
- Explorar si Perú Compras tiene una sección de convocatoria para nuevos
  proveedores (pausado — enlaces encontrados hasta ahora están caídos).
- Explorar SEDAPAL, ENAPU y CORPAC como fuentes adicionales (pausado —
  sin acceso público funcional al momento de revisar).

Datos: OCDS / OECE (ex OSCE), Perú Compras, PetroPerú y Banco de la Nación,
licencia CC BY 4.0.


## Despliegue en Streamlit Cloud

`packages.txt` lista los paquetes de sistema que el panel de expediente
necesita: tesseract con el idioma español, poppler y el lector de archivos
comprimidos. Esa máquina arranca limpia en cada despliegue.

**Ese archivo no admite comentarios.** Streamlit Cloud pasa cada línea a
`apt-get install`, así que una línea que empiece con `#` se interpreta como un
nombre de paquete, falla la instalación y la aplicación no levanta: aparece
"Oh no. Error running app". Solo nombres de paquete, uno por línea, sin tildes.

Si solo se quiere publicar el tablero y dejar el análisis de expedientes para
una ejecución local, `packages.txt` puede eliminarse: el dashboard y los cuatro
extractores no lo necesitan. Conviene tener en cuenta, además, que el OCR de un
documento grande puede superar la memoria del plan gratuito.

## El panel de comparación dentro del tablero

En la pestaña **Histórico de adjudicaciones**, al pie, hay un selector de
proceso y un panel con dos capas.

La primera aparece al instante y sale de la base: entidad, fecha, monto
adjudicado, proveedor ganador, categoría y enlace a la ficha del SEACE.

La segunda está detrás del botón **Analizar expediente y comparar con BTOUCH**.
Descarga el documento, lo abre sea cual sea su formato, lo lee —con
reconocimiento óptico si hace falta—, extrae el requerimiento técnico y lo
compara contra las fichas de BTOUCH y de la competencia. Puede tardar de unos
segundos a varios minutos y el resultado queda guardado en la sesión.

Requiere haber corrido `python src/indexar_documentos.py` al menos una vez; si
no, el panel lo avisa en lugar de fallar.

Conviene saber que este análisis **falla seguido, y no por un defecto del
panel**: solo cuatro de cada veintitrés expedientes entregan un requerimiento
legible. Cuando no puede, el panel dice en qué paso se detuvo y por qué.
