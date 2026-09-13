from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from travel_record.sources import (
    DataSourceError,
    HttpCache,
    OSMRailSource,
    PlaceResolver,
    TripResolver,
    _route_color,
)
from travel_record.models import Station, Leg, Place, ResolvedLeg, Trip


class StationMatchingTests(TestCase):
    def test_service_direction_rejects_reverse_even_when_track_connects(self):
        source = OSMRailSource(Mock(), Mock())
        nodes = {1: {'lat': 35, 'lon': 136, 'tags': {'name': '敦賀'}},
                 2: {'lat': 34.9, 'lon': 136, 'tags': {'name': '新疋田'}}}
        relation = {'id': 123, 'tags': {'type': 'route', 'route': 'train', 'public_transport:version': '2'},
                    'members': [{'type': 'node', 'role': 'stop', 'ref': i} for i in (1, 2)]}
        forward = Leg('', '湖西線', Place('敦賀'), Place('新疋田'), 1)
        check = source._validate_service_direction(forward, relation, nodes)
        self.assertEqual(check['status'], 'stop_order_matches')
        self.assertEqual(check['track_geometry'], 'unverified')
        reverse = Leg('', '湖西線', Place('新疋田'), Place('敦賀'), 1)
        with self.assertRaisesRegex(DataSourceError, '拒绝反向'):
            source._validate_service_direction(reverse, relation, nodes)
        relation['members'].append(relation['members'][0])
        self.assertEqual(source._validate_service_direction(forward, relation, nodes)['status'], 'unverified')

    def test_infrastructure_and_missing_stops_are_not_direction_verified(self):
        source = OSMRailSource(Mock(), Mock())
        leg = Leg('', '湖西線', Place('A'), Place('B'), 1)
        for tags in ({'route': 'railway'}, {'type': 'route', 'route': 'train', 'public_transport:version': '2'}):
            self.assertEqual(source._validate_service_direction(leg, {'tags': tags}, {})['status'], 'unverified')

    def test_kosei_aliases_select_full_service_without_catalog(self):
        source = OSMRailSource(Mock(), Mock())
        source.places.known = {}
        relation = {"id": 19142520, "tags": {"type": "route", "route": "train",
                    "name": "JR湖西線 普通 (敦賀 => 京都)", "from": "敦賀", "to": "京都"}}
        for name in ('JR Kosei Line', 'Kosei Line', 'JR湖西線', '湖西線', '湖西线', 'JR湖西线'):
            for origin, destination in [('比良', '近江舞子'), ('近江舞子', '敦賀'), ('山科', '近江塩津')]:
                with self.subTest(name=name, origin=origin, destination=destination):
                    leg = Leg('', name, Place(origin), Place(destination), 1)
                    with patch.object(source, '_relation_metadata', return_value=relation) as metadata, \
                         patch.object(source, 'discover_catalog') as catalog:
                        self.assertEqual(source.select_relation(leg)['id'], 19142520)
                        metadata.assert_called_once_with(19142520)
                        catalog.assert_not_called()

    def test_discovery_includes_train_service_relations(self):
        source = OSMRailSource(Mock(), Mock())
        source.seed_by_relation = {}
        with patch.object(source, '_overpass', return_value={'elements': []}) as query:
            source.discover_catalog()
        self.assertTrue(query.call_args_list)
        for call in query.call_args_list:
            self.assertIn('rel["type"="route"]["route"~"train|', call.args[0])

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

    def test_direction_suffix_still_locks_known_sanin_line(self):
        source = OSMRailSource(Mock(), Mock())
        source.places.known = {}
        relation = {
            "id": 10154357,
            "tags": {"type": "route", "route": "train", "name": "嵯峨野線"},
        }
        leg = Leg(
            "", "JR Sanin Main Line Osaka-bound", Place("Saga-Arashiyama"), Place("Nijo"), 1
        )
        with patch.object(source, "_relation_metadata", return_value=relation) as metadata, \
             patch.object(source, "discover_catalog") as catalog:
            self.assertEqual(source.select_relation(leg)["id"], 10154357)
            self.assertTrue(metadata.called)
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


class PlaceAndFallbackTests(TestCase):
    def test_station_preference_does_not_choose_same_named_attraction(self):
        http = Mock()
        http.cached.return_value = None
        http.json.return_value = [
            {"lat": "35.0", "lon": "135.0", "category": "tourism", "type": "attraction", "importance": 0.9},
            {"lat": "35.1", "lon": "135.1", "category": "railway", "type": "station", "importance": 0.4},
        ]
        resolver = PlaceResolver(http)
        resolver._last_nominatim_request = -100
        self.assertEqual(
            resolver.resolve(Place("Unique Station Candidate"), prefer_station=True),
            (35.1, 135.1),
        )

    def test_walking_fallback_records_actual_failure(self):
        with TemporaryDirectory() as directory:
            resolver = TripResolver(Path(directory))
            resolver.places.resolve = Mock(side_effect=[(35.0, 135.0), (35.01, 135.01)])
            resolver.router.walking_path = Mock(return_value=None)
            resolver.router.failures["osm-foot-routes"] = "离线缓存缺失"
            leg = resolver.resolve_leg(Leg("", "walk", Place("A"), Place("B"), 1))
        self.assertEqual(leg.details["fallback_reason"], "离线缓存缺失")
        self.assertIn("离线缓存缺失", leg.notes[0])

    def test_trip_records_connection_gap_without_inventing_route(self):
        with TemporaryDirectory() as directory:
            resolver = TripResolver(Path(directory))
            first_leg = Leg("", "walk", Place("A"), Place("B"), 1)
            second_leg = Leg("", "walk", Place("C"), Place("D"), 2)
            resolved = [
                ResolvedLeg(first_leg, [(35.0, 135.0), (35.0, 135.0)], "#777", "test"),
                ResolvedLeg(second_leg, [(35.01, 135.01), (35.02, 135.02)], "#777", "test"),
            ]
            resolver.resolve_leg = Mock(side_effect=resolved)
            result = resolver.resolve_trip(Trip("test", [first_leg, second_leg]))
        self.assertGreater(result[1].details["connection_from_previous_meters"], 300)
        self.assertIn("未自动补画", result[1].notes[-1])
