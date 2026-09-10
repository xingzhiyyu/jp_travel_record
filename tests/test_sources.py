from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from travel_record.sources import DataSourceError, HttpCache, OSMRailSource, _route_color
from travel_record.models import Station, Leg, Place


class StationMatchingTests(TestCase):
    def test_named_osm_colours_are_safe_for_the_renderer(self):
        self.assertEqual(_route_color("green"), "#008000")
        self.assertEqual(_route_color("#007ac2"), "#007ac2")
        self.assertEqual(_route_color("not-a-colour"), "#2463A8")

    def test_known_line_missing_data_never_falls_back_to_another_line(self):
        source = OSMRailSource(Mock(), Mock())
        leg = Leg("", "Hankyu Kobe Line", Place("Kobe-Sannomiya"), Place("Rokko"), 1)
        with patch.object(source, "_relation_metadata", side_effect=DataSourceError("offline")), patch.object(source, "discover_catalog") as catalog:
            with self.assertRaises(DataSourceError):
                source.select_relation(leg)
            catalog.assert_not_called()

    def test_shin_imamiya_osm_spelling_does_not_select_imamiya(self):
        imamiya = Station("今宮", 34.654, 135.493, ["Imamiya"])
        shin = Station("新今宮", 34.650, 135.502, ["Shin-Imaimiya"])
        self.assertIs(OSMRailSource._match_station("Shin-Imamiya", [imamiya, shin]), shin)
        self.assertIsNone(OSMRailSource._match_station("Shin-Imamiya", [imamiya]))


class OfflineCacheTests(TestCase):
    def test_offline_mode_accepts_stale_data_and_fails_fast_for_missing_data(self) -> None:
        with TemporaryDirectory() as directory, patch.dict("os.environ", {"TRAVEL_RECORD_OFFLINE": "1"}):
            cache = HttpCache(Path(directory))
            url = "https://example.invalid/cached"
            cache._path("test", f"GET\n{url}\nNone").write_bytes(b"cached payload")
            with patch("travel_record.sources.urllib.request.urlopen") as network:
                self.assertEqual(cache.request(url, namespace="test", max_age=-1), b"cached payload")
                with self.assertRaises(DataSourceError):
                    cache.request("https://example.invalid/missing", namespace="test")
                network.assert_not_called()
