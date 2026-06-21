# FlatCAM9NeoS2
Repositorio de FlatCAM 9 Neo S2, Fork basado en FlatCAM 8.994 Beta
Autor: Dr. Luis Enrique Yacupoma Aguirre
Lanzamiento: 01/06/2026 

FlatCAM 9 Neo S2 (Shapely 2.x Friendly Edition)
“De la necesidad, nació la evolución”

Todo comenzó cuando necesitaba un software para diseñar PCBs y fabricarlos en mi CNC 3018. Descubrí FlatCAM, una herramienta increíble que me ayudó muchísimo… pero también me dio muchos dolores de cabeza al intentar instalarlo. Las dependencias no eran compatibles entre sí, los errores eran confusos y, tras invertir tiempo en entender su ecosistema, me dije:
“Si yo tuve tantos problemas para hacerlo funcionar la primera vez, seguramente no soy el único.”
Así que, aprovechando el tiempo libre en la universidad, me propuse un reto: arreglar el código para que la instalación y ejecución fueran más limpias, libres de warnings molestos y compatibles con entornos modernos.

Punto de partida: FlatCAM 8.994 Beta

Mi viaje comenzó con la versión 8.994 Beta, desarrollada por Marius Stanciu. Esta versión ya fue una primera modernización importante del proyecto original:

•	Migró el código base de Python 2.7 + PyQt4 a Python 3 (compatible con versiones >=3.5) + PyQt5.
•	Incorporó herramientas clave como ToolQRCode.py, ToolPaint.py y muchas otras.

Sin embargo, al trabajar con ella, detecté que su ecosistema seguía siendo delicado: dependencias conflictivas, dependencia frecuente de scripts heredados que aún asumen Python 2.7, y una versión antigua de Shapely (1.8.5.post1) que limitaba el rendimiento y la compatibilidad futura.

Un obstáculo adicional: durante el proceso, me topé con la librería rasterio, necesaria para importar archivos BMP a Gerber. Su instalación resultó ser muy problemática. Por ello, decidí dejarla como opcional (no obligatoria) para que el programa pudiera ejecutarse sin errores, aunque sacrificando momentáneamente esa funcionalidad específica.

Mi trabajo: La segunda modernización

Decidí ir un paso más allá y llevar FlatCAM a un nuevo nivel. Sobre la base de la 8.994 Beta, realicé lo siguiente:

•	Actualización a Python 3.8 — Entorno más robusto, sin romper compatibilidad.
•	Adaptación extensiva del código base para compatibilidad con Shapely 2.0.7. Esto fue un desafío mayúsculo: tuve que reescribir partes del código para adaptarme a la nueva API (por ejemplo, el manejo de geometrías multiparte con .geoms, corrección de importaciones como shapely.geometry.base, y la inmutabilidad de las geometrías). El resultado es un rendimiento notablemente mejor y una base sólida para el futuro.
•	Mantenimiento de PyQt5 — Asegurando compatibilidad amplia sin forzar el salto a PyQt6 (al menos por ahora).
•	Corrección de decenas de pequeños bugs surgidos al modernizar las dependencias.
•	Pruebas exhaustivas sobre la mayoría de las herramientas (ToolPaint, ToolQRCode, ToolNCC, ToolCopperThieving, etc.). Todo funciona establemente.

Mejora de exportación Film PCB
También se modernizó el pipeline de renderizado de la herramienta Film PCB:
•	Corrección de exportación PDF.
•	Centrado automático en hojas A4 vertical y horizontal.
•	Reemplazo de la antigua ruta:
SVG → svglib → renderPM → PNG
por una nueva arquitectura basada en:
SVG → QtSvg → QImage → PNG
Esto eliminó corrupciones, imágenes incompletas y artefactos visuales presentes en la exportación PNG. Esto no solo eliminó artefactos visuales, sino que también reemplazó la dependencia de renderPM para la generación PNG por una solución basada en Qt, haciendo la exportación más estable y compatible con entornos modernos.

Estado actual
FlatCAM 9 Neo S2 ya está funcionando con:

•	🐍 Python 3.8
•	📐 Shapely 2.0.7
•	🖼️ PyQt5

La experiencia de instalación es mucho más limpia, la mayoría de advertencias iniciales han desaparecido y el rendimiento ha mejorado significativamente. Uno de los mayores objetivos de este fork ha sido construir un ecosistema estable y reproducible.

Planes a futuro
Seguiré modernizando el código paso a paso, incorporando buenas prácticas y, si la comunidad responde, quizás integrar algunas de las características más interesantes de los forks activos (siempre respetando las licencias y los créditos).

En cuanto a rasterio: estoy evaluando dos caminos. El primero es retomar la integración de rasterio pero buscando una forma más estable de empaquetarlo. El segundo es reemplazarlo por una librería alternativa más ligera y fácil de instalar que permita convertir archivos BMP (y otros formatos de imagen) a geometría Gerber. Si conoces alguna opción interesante, ¡te escucho!

¿Encontraste un bug?
¡Por favor, házmelo saber! Tu feedback es fundamental para seguir mejorando. Juntos podemos mantener vivo este gran proyecto.

Gracias por tu interés y por darle una oportunidad a FlatCAM 9 Neo S2.
“De dónde vienes, sabes. A dónde vas, lo decides tú.”

FlatCAM 9 Neo S2 fue validado específicamente con:
Python 3.8
PyQt5 5.15.11
Qt 5.15.2
Shapely 2.0.7
matplotlib 3.5.3
numpy 1.21.6
VisPy 0.9.0

Link de descarga: https://github.com/ProgLuis/FlatCAM9NeoS2/releases/tag/V9

Archivos principales modernizados y adaptados para compatibilidad con Shapely 2.x

Bin
- app_Main.py
- appPool.py
- camlib.py
- Common.py
- FlatCAM.py
- FlatCAMExcellon.py
- FlatCAMGerber.py
- FlatCAMGeometry.py
- FlatCAMObj.py
- PlotCanvas.py
- PlotCanvasLegacy.py

appCommon
- Common.py

appEditors
- AppExcEditor.py
- AppGeoEditor.py
- AppGerberEditor.py

appGUI
- GUIElements.py
- PlotCanvas.py
- PlotCanvasLegacy.py
- VisPyTesselators.py
- VisPyVisuals.py

appObjects
- FlatCAMCNCJob.py
- FlatCAMExcellon.py
- FlatCAMGeometry.py
- FlatCAMObj.py

appParsers
- ParseGerber.py
- ParsePDF.py
- ParseSVG.py

appTools
- ToolAlignObjects.py
- ToolCopperThieving.py
- ToolCorners.py
- ToolCutOut.py
- ToolDistance.py
- ToolDistanceMin.py
- ToolEtchCompensation.py
- ToolFiducials.py
- ToolFilm.py
- ToolInvertGerber.py
- ToolIsolation.py
- ToolNCC.py
- ToolOptimal.py
- ToolPaint.py
- ToolPanelize.py
- ToolPDF.py
- ToolProperties.py
- ToolQRCode.py
- ToolRulesCheck.py
- ToolSolderPaste.py
- ToolSub.py

locale\es\LC_MESSAGES
- strings.po

preprocessors
- Paste_1.py

⚠️ Nota
FlatCAM 9 Neo S2 es un fork comunitario e independiente.
Este proyecto no pretende reemplazar ni competir con otros forks, sino ofrecer una alternativa enfocada en la compatibilidad moderna y la estabilidad. Todo el crédito del trabajo original pertenece a sus respectivos autores.


## New Features in FlatCAM 9 Neo S2
## 18/06/2026

### SVG Proteus Compatibility MVP

FlatCAM 9 Neo S2 includes improved compatibility with SVG files exported by Proteus.

Implemented features:

* Correct SVG scaling using viewBox dimensions
* Improved SVG polyline parsing
* Support for inherited stroke-width from SVG groups
* Conversion of SVG strokes into solid geometry
* Automatic extraction of drill locations from Proteus SVG files
* Automatic creation of Excellon objects from detected Proteus SVG drill information

Workflow:

Proteus SVG
→ Geometry Object
→ Excellon Object
→ CNC Job
→ CNC3018

Validation example:

* `Prueba2.SVG`:
  * Proteus original
  * Geometry OK
  * Excellon OK
  * 6 drills
  * Drill diameter: 0.6400 mm

## 19/06/2026

### SVG Source Advisor and Adobe Illustrator SVG Compatibility MVP

FlatCAM 9 Neo S2 now includes an experimental CAM-oriented SVG import workflow.

Implemented features:

* SVG source detection:
  * Proteus Design Suite
  * Adobe Illustrator
  * Unknown SVG sources
* SVG Source Advisor messages during import.
* Physical scale reliability detection.
* Warning when SVG files do not contain reliable physical scale information.
* Physical scale recovery from Illustrator XMP `MaxPageSize` metadata.
* Detection of recommended Illustrator export settings:
  * SVG Tiny 1.2
  * Presentation Attributes
  * Include XMP Metadata
  * Coordinate decimals: minimum 3, recommended 4
  * Do not preserve Illustrator Editing Capabilities
* Coordinate decimal precision inspection for Illustrator SVG files.
* Warnings when observed coordinate precision is too low for CAM/CNC work.
* Added compatibility with Proteus SVG files reexported from Adobe Illustrator.
* Automatic extraction of drill locations from Illustrator SVG files exported with recommended settings.
* Automatic creation of Excellon objects from detected Illustrator SVG drill information.
* Preserved compatibility with the existing Proteus SVG workflow.

Validated workflows:

Adobe Illustrator SVG recommended profile
→ XMP physical scale recovery
→ Geometry Object
→ Excellon Object
→ CNC Job

Recommended Illustrator export profile:

* SVG Profile: SVG Tiny 1.2
* CSS Properties: Presentation Attributes
* Include XMP Metadata: Yes
* Working units: millimeters (mm)
* Coordinate decimals: minimum 3
* Coordinate decimals recommended: 4
* Preserve Illustrator Editing Capabilities: No

Validation examples:

* `Prueba2d.SVG`:
  * Illustrator without XMP
  * Geometry OK
  * Excellon detected
  * Scale warning shown
  * Physical scale not reliable
* `Prueba2g.SVG`:
  * Illustrator with XMP
  * Physical scale recovered from XMP `MaxPageSize`
  * Geometry OK
  * Excellon OK
  * 6 drills
* `Prueba2h.SVG`:
  * Illustrator recommended profile
  * SVG Tiny 1.2 OK
  * Presentation Attributes OK
  * XMP Metadata OK
  * Coordinate decimals = 4 OK
  * Geometry OK
  * Excellon OK
  * 6 drills
  * Recommended reference profile

Limitations:

* SVG import remains an experimental CAM-oriented workflow.
* Native Gerber + Excellon files remain the preferred manufacturing workflow.
* Illustrator SVG files without `width`/`height` and without XMP `MaxPageSize` cannot provide reliable physical scale.
* Low coordinate precision, such as 1 decimal, may introduce small geometry or drill diameter deviations.
* Users must verify dimensions, geometry, Excellon drills and CNC paths before manufacturing.
* Manual Illustrator-created drill conventions still require future validation.

## 21/06/2026

### Adobe Illustrator SVG Tiny 1.2 Compatibility Suite

FlatCAM 9 Neo S2 expands its experimental CAM-oriented workflow with broader support for SVG Tiny 1.2 files exported from Adobe Illustrator.

This update supports both of these workflows:

Adobe Illustrator drawing created from scratch
-> SVG Tiny 1.2
-> FlatCAM 9 Neo S2 Geometry
-> Excellon drills
-> CNC Job

Proteus SVG
-> Adobe Illustrator editing and customization
-> SVG Tiny 1.2
-> FlatCAM 9 Neo S2 Geometry
-> Excellon drills
-> CNC Job

This compatibility applies to Illustrator-exported SVG files, not native `.AI` documents.

Main improvements:

* Broader support for common Illustrator tools:
  * Lines and stroked paths
  * Rectangles and rounded rectangles
  * Circles and ellipses
  * Polygons and Pen Tool paths
  * Compound Paths with contained holes
  * Pathfinder Unite and Minus Front
  * Flattened clipping masks
  * Expanded symbols
* Multi-layer Illustrator SVG support.
* Hidden layers, hidden groups and hidden objects are ignored.
* Visible overlapping objects from separate layers are consolidated into CAM geometry.
* SVG matrix, translate, rotate, scale and skew transformations are handled more reliably.
* Physical SVG scale can be recovered from Illustrator XMP `MaxPageSize` metadata.
* Manual Illustrator drill detection is supported experimentally.
* Valid white circular drill markers create an Excellon object automatically.
* Proteus SVG and Proteus SVG reexported or customized through Illustrator remain supported.

Manual drill convention in Illustrator:

* Draw a true circle, not an ellipse.
* Use white fill.
* Use a visible dark or black stroke.
* Set the geometric circle diameter to the desired drill diameter.
* Supported drill diameter range: 0.2 mm to 6.0 mm.
* Stroke width is only a visual/detection aid and is not added to the drill diameter.

Recommended Illustrator export profile:

* SVG Tiny 1.2.
* Presentation Attributes.
* Include XMP Metadata.
* Working units: millimeters.
* Coordinate decimals: minimum 3, recommended 4.
* Do not preserve Illustrator Editing Capabilities.

Known limitation:

Complex Compound Paths made by partially overlapping shapes may not match Illustrator's visual fill-rule behavior. FlatCAM 9 Neo S2 warns the user in the Shell and reports the problematic layer when detected.

For PCB/CAM workflows, use Illustrator Pathfinder operations such as Unite, Minus Front, Exclude or Intersect instead of overlapping Compound Paths. Expanding or flattening the geometry before SVG export is also recommended.

Compound Paths with normal contained holes remain supported.

Important:

* SVG import remains experimental.
* Native Gerber + Excellon is still the preferred manufacturing workflow.
* Always verify dimensions, geometry, drill diameters, drill positions and CNC paths before manufacturing.
