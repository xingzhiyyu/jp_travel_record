from unittest import TestCase
from unittest.mock import patch
from pathlib import Path
from tempfile import TemporaryDirectory
from PIL import Image, ImageColor
from travel_record.cartography import PAPER, WATER

from shapely.geometry import Point
from shapely.ops import unary_union

from travel_record.coastline import land_polygons, acquire_region, Coastline, validate_bounds
from travel_record.sources import DataSourceError, HttpCache
from travel_record.cli import main


def data(*ways):
    return {"elements": [{"type": "way", "tags": {"natural": "coastline"},
                          "geometry": [{"lon": x, "lat": y} for x,y in way]} for way in ways]}


class CoastlineTests(TestCase):
    def test_invalid_bounds(self):
        for bounds in [(1, 2, 0, 3), (0, 179, 1, -179), (0, 0, float('nan'), 1)]:
            with self.assertRaises(DataSourceError):
                validate_bounds(bounds)

    def test_acquire_register_and_reload_without_network(self):
        with TemporaryDirectory() as folder:
            http = HttpCache(Path(folder))
            with patch('travel_record.coastline.fetch_coastline', return_value=data([(1,-1),(1,3)])):
                path, snapshot = acquire_region(http, 'test-coast', (0,0,2,2))
            self.assertTrue(path.exists())
            self.assertEqual(snapshot['land_polygon_count'], 1)
            with patch('travel_record.coastline.fetch_coastline', side_effect=AssertionError('network')):
                coast = Coastline(http)
                self.assertTrue(coast.prepare('test-coast'))
                self.assertEqual(coast.sources['test-coast']['bbox'], (0,0,2,2))
                image = Image.new('RGB', (200,200))
                coast.draw(image, (1,1), 7, ratio=1)
                self.assertEqual(image.getpixel((50,100)), ImageColor.getrgb(PAPER))
                self.assertEqual(image.getpixel((150,100)), ImageColor.getrgb(WATER))

    def test_failed_update_preserves_valid_region(self):
        with TemporaryDirectory() as folder:
            http = HttpCache(Path(folder))
            with patch('travel_record.coastline.fetch_coastline', return_value=data([(1,-1),(1,3)])):
                path, _ = acquire_region(http, 'test-coast', (0,0,2,2))
            before = path.read_bytes()
            with patch('travel_record.coastline.fetch_coastline', return_value=data([(1,-1),(1,1)])):
                with self.assertRaises(DataSourceError):
                    acquire_region(http, 'test-coast', (0,0,2,2))
            self.assertEqual(before, path.read_bytes())

    def test_reject_unsafe_name_large_or_empty_region(self):
        with TemporaryDirectory() as folder:
            http = HttpCache(Path(folder))
            for name, bounds in [('tokyo',(0,0,2,2)), ('../oops',(0,0,2,2)), ('big',(0,0,4,4))]:
                with self.assertRaises(DataSourceError):
                    acquire_region(http, name, bounds)
            with patch('travel_record.coastline.fetch_coastline', return_value=data()):
                with self.assertRaises(DataSourceError):
                    acquire_region(http, 'empty', (0,0,2,2))
            self.assertFalse((http.root / 'coastline-regions').exists())

    def test_cli_does_not_resolve_trip_or_render(self):
        with TemporaryDirectory() as folder:
            with patch('travel_record.coastline.fetch_coastline', return_value=data([(1,-1),(1,3)])), \
                 patch('travel_record.cli.TripResolver', side_effect=AssertionError('resolve')):
                self.assertEqual(main(['coastline','test-coast','--bounds','0','0','2','2',
                                       '--cache-dir',folder]), 0)

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
