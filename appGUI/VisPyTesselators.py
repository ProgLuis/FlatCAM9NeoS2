# ##########################################################
# FlatCAM: 2D Post-processing for Manufacturing            #
# http://flatcam.org                                       #
# File Author: Dennis Hayrullin                            #
# Date: 2/5/2016                                           #
# MIT Licence                                              #
# ##########################################################

# ########################################################## ##
# FlatCAM 9 Neo S2                                            #
# Shapely 2.x Friendly Edition                                #
# Community modernized fork                                   #
# Maintained by Luis Enrique Yacupoma Aguirre                 #
# Date: 01/06/2026                                            #
# https://github.com/ProgLuis/FlatCAM9NeoS2                   #
# ########################################################## ##

from OpenGL import GLU
from shapely.geometry import Polygon, MultiPolygon, GeometryCollection

class GLUTess:
    def __init__(self):
        """
        OpenGL GLU triangulation class
        """
        self.tris = []
        self.pts = []
        self.vertex_index = 0

    def _on_begin_primitive(self, type):
        pass

    def _on_new_vertex(self, vertex):
        self.tris.append(vertex)

    # Force GLU to return separate triangles (GLU_TRIANGLES)
    def _on_edge_flag(self, flag):
        pass

    def _on_combine(self, coords, data, weight):
        return coords[0], coords[1], coords[2]

    @staticmethod
    def _on_error(errno):
        print("GLUTess error:", errno)

    def _on_end_primitive(self):
        pass


    def _triangulate_polygon(self, polygon):
        """
        Triangulates a single Shapely Polygon.
        """

        # Create tessellation object
        tess = GLU.gluNewTess()

        # Setup callbacks
        GLU.gluTessCallback(tess, GLU.GLU_TESS_BEGIN, self._on_begin_primitive)
        GLU.gluTessCallback(tess, GLU.GLU_TESS_VERTEX, self._on_new_vertex)
        GLU.gluTessCallback(tess, GLU.GLU_TESS_EDGE_FLAG, self._on_edge_flag)
        GLU.gluTessCallback(tess, GLU.GLU_TESS_COMBINE, self._on_combine)
        GLU.gluTessCallback(tess, GLU.GLU_TESS_ERROR, self._on_error)
        GLU.gluTessCallback(tess, GLU.GLU_TESS_END, self._on_end_primitive)

        GLU.gluTessBeginPolygon(tess, None)

        def define_contour(contour):
            vertices = list(contour.coords)

            if len(vertices) < 4:
                return

            if vertices[0] == vertices[-1]:
                vertices = vertices[:-1]

            if len(vertices) < 3:
                return

            self.pts += vertices

            GLU.gluTessBeginContour(tess)

            for vertex in vertices:
                point = (vertex[0], vertex[1], 0)
                GLU.gluTessVertex(tess, point, self.vertex_index)
                self.vertex_index += 1

            GLU.gluTessEndContour(tess)

        define_contour(polygon.exterior)

        for interior in polygon.interiors:
            define_contour(interior)

        GLU.gluTessEndPolygon(tess)
        GLU.gluDeleteTess(tess)


    def triangulate(self, polygon):
        """
        Triangulates Shapely polygonal geometry.
        Supports Polygon, MultiPolygon and GeometryCollection.
        :param polygon: shapely geometry
        :return: list, list
            Triangle vertex indices and polygon points.
        """

        # Reset global output
        del self.tris[:]
        del self.pts[:]
        self.vertex_index = 0

        if polygon is None:
            return self.tris, self.pts

        try:
            if polygon.is_empty:
                return self.tris, self.pts
        except AttributeError:
            return self.tris, self.pts

        def iter_polygons(geo):
            if geo is None:
                return

            try:
                if geo.is_empty:
                    return
            except AttributeError:
                return

            if isinstance(geo, Polygon):
                yield geo
            elif isinstance(geo, (MultiPolygon, GeometryCollection)):
                for sub_geo in geo.geoms:
                    for poly in iter_polygons(sub_geo):
                        yield poly

        for poly in iter_polygons(polygon):
            self._triangulate_polygon(poly)

        return self.tris, self.pts
