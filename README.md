# FlatCAM9NeoS2
Repositorio de FlatCAM 9 Neo S2, Fork de FlatCAM 8.994 Beta

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
numpy X.X.X
etc

⚠️ Nota
FlatCAM 9 Neo S2 es un fork comunitario e independiente.
Este proyecto no pretende reemplazar ni competir con otros forks, sino ofrecer una alternativa enfocada en la compatibilidad moderna y la estabilidad. Todo el crédito del trabajo original pertenece a sus respectivos autores.
