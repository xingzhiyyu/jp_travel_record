from unittest import TestCase

from shapely.geometry import Point
from shapely.ops import unary_union

from travel_record.coastline import land_polygons
from travel_record.sources import DataSourceError


def data(*ways):
    return {"elements": [{"type": "way", "tags": {"natural": "coastline"},
                          "geometry": [{"lon": x, "lat": y} for x,y in way]} for way in ways]}


class CoastlineTests(TestCase):
    def test_open_mainland_and_offshore_island_are_separated_from_sea(self):
        # Northbound coast: mainland west (left); a separate CCW island east.
        source = data([(5,-1),(5,5),(5,11)], [(7,3),(9,3),(9,5),(7,5),(7,3)])
        land = unary_union(land_polygons(source, (0,0,10,10)))
        self.assertTrue(land.contains(Point(2,4)))
        self.assertTrue(land.contains(Point(8,4)))
        self.assertFalse(land.contains(Point(6,4)))
        self.assertAlmostEqual(land.area, 54)

    def test_port_inlet_is_not_closed_with_a_straight_chord(self):
        source = data([(5,-1),(5,3),(2,3),(2,6),(5,6),(5,11)])
        land = unary_union(land_polygons(source, (0,0,10,10)))
        self.assertTrue(land.contains(Point(1,4)))
        self.assertFalse(land.contains(Point(4,4)))
        self.assertTrue(land.contains(Point(4,7)))

    def test_broken_coast_fails_instead_of_guessing_land(self):
        with self.assertRaises(DataSourceError):
            land_polygons(data([(5,-1),(5,4)],[(5,6),(5,11)]), (0,0,10,10))
