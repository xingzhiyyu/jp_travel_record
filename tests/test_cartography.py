from unittest import TestCase
from unittest.mock import Mock

from PIL import Image

from travel_record.cartography import (
    AtlasCanvas,
    AtlasPresentation,
    MapContext,
    joined_rings,
)
from travel_record.models import Trip, Leg, Place, ResolvedLeg


class RouteVisibilityTests(TestCase):
    def test_only_traveled_walking_is_dashed_and_rail_has_no_casing(self):
        walk = Leg("", "walk", Place("A"), Place("B"), 1)
        rail = Leg("", "JR Kobe Line", Place("C"), Place("D"), 2)
        legs = [
            ResolvedLeg(walk, [(34.7, 135.4), (34.71, 135.41)], "#777777", "test"),
            ResolvedLeg(rail, [(34.72, 135.42), (34.73, 135.43)], "#0072BC", "test"),
        ]
        atlas = AtlasPresentation(Trip("test", [walk, rail]), Mock())
        atlas.dashed = Mock()
        camera = Mock()
        camera.zoom = 15
        camera.point.side_effect = lambda point: point
        canvas = Mock()
        atlas.routes(canvas, legs, 0, 0.5, camera)
        canvas.line.assert_not_called()
        atlas.dashed.assert_called_once()
        self.assertNotEqual(atlas.dashed.call_args.args[1][-1], legs[0].path[-1])
        atlas.routes(canvas, legs, 1, 0.5, camera)
        self.assertEqual(atlas.dashed.call_count, 2)
        self.assertEqual(canvas.line.call_count, 1)
        self.assertNotEqual(canvas.line.call_args.args[0][-1], legs[1].path[-1])
        widths = []
        for zoom in (12, 14, 15, 16):
            camera.zoom = zoom
            atlas.routes(canvas, legs, 1, 0.5, camera)
            widths.append(canvas.line.call_args.args[2])
        self.assertEqual(widths, sorted(set(widths)))


class ContextGeometryTests(TestCase):
    def test_joins_reversed_multipolygon_members_without_inventing_edges(self) -> None:
        a, b, c, d = (0, 0), (1, 0), (1, 1), (0, 1)
        rings = joined_rings([[b, a], [b, c], [d, c], [a, d]])
        self.assertEqual(len(rings), 1)
        self.assertEqual(rings[0][0], rings[0][-1])
        self.assertEqual(set(rings[0]), {a, b, c, d})
        self.assertEqual(joined_rings([[a, b], [b, c]]), [])

    def test_preserves_water_islands_and_discards_open_area_ways(self) -> None:
        def geometry(points):
            return [{"lat": lat, "lon": lon} for lat, lon in points]

        features = MapContext.extract(
            {
                "elements": [
                    {
                        "type": "relation",
                        "tags": {"natural": "water"},
                        "members": [
                            {
                                "role": "outer",
                                "geometry": geometry(
                                    [
                                        (35, 139),
                                        (35, 140),
                                        (36, 140),
                                        (36, 139),
                                        (35, 139),
                                    ]
                                ),
                            },
                            {
                                "role": "inner",
                                "geometry": geometry(
                                    [
                                        (35.2, 139.2),
                                        (35.2, 139.4),
                                        (35.4, 139.4),
                                        (35.4, 139.2),
                                        (35.2, 139.2),
                                    ]
                                ),
                            },
                        ],
                    },
                    {
                        "type": "way",
                        "tags": {"leisure": "park"},
                        "geometry": geometry([(35, 139), (35.1, 139.1)]),
                    },
                ]
            }
        )
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0][0], "water")
        self.assertEqual(len(features[0][3]), 1)

    def test_reclaimed_industrial_areas_are_rendered_as_land(self) -> None:
        features = MapContext.extract(
            {
                "elements": [
                    {
                        "type": "way",
                        "tags": {"landuse": "industrial"},
                        "geometry": [
                            {"lat": 35.0, "lon": 139.0},
                            {"lat": 35.0, "lon": 139.1},
                            {"lat": 35.1, "lon": 139.1},
                            {"lat": 35.1, "lon": 139.0},
                            {"lat": 35.0, "lon": 139.0},
                        ],
                    }
                ]
            }
        )
        self.assertEqual(len(features), 1)
        self.assertEqual(features[0][0], "land")


class CenterMarkerTests(TestCase):
    def test_small_marker_has_no_wide_halo(self):
        presentation = AtlasPresentation(Trip("test", []), None)
        canvas = Mock()
        presentation.marker(canvas)
        canvas.circle.assert_called_once()
        self.assertEqual(canvas.circle.call_args.args[:3], (640, 360, 5))

    def test_position_marker_remains_blue_at_exact_screen_center(self) -> None:
        for width, height in [(1280, 720), (640, 360)]:
            presentation = AtlasPresentation(
                Trip("test", [], width=width, height=height), None
            )
            canvas = AtlasCanvas((width, height))
            presentation.marker(canvas)
            result = canvas.composite(Image.new("RGB", (width, height), "white"))
            # At half resolution the thin white rim contributes a little to
            # the center through antialiasing; it must still read as blue.
            center = result.getpixel((width // 2, height // 2))
            self.assertTrue(all(abs(a - b) <= 15 for a, b in zip(center, (22, 119, 255))))
