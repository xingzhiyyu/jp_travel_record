from pathlib import Path
from unittest import TestCase

from travel_record.parser import TripFormatError, parse_trip


class ParserTests(TestCase):
    def test_parses_settings_and_expands_via_points(self) -> None:
        trip = parse_trip(
            """
            title: Test
            size: 960x540
            route:
            08:00 | JR Kobe Line | Osaka -> Sannomiya -> Kobe
            10:00 | 公交 | Kobe -> Harborland
            """
        )
        self.assertEqual(trip.title, "Test")
        self.assertEqual((trip.width, trip.height), (960, 540))
        self.assertEqual(len(trip.legs), 3)
        self.assertEqual(trip.legs[-1].display_line, "bus")

    def test_supports_explicit_coordinates(self) -> None:
        trip = parse_trip("route:\n08:00 | walk | A@35.0,135.0 -> B@35.1,135.1")
        self.assertEqual(trip.legs[0].origin.coordinate, (35.0, 135.0))

    def test_taxi_is_a_separate_simplified_mode(self) -> None:
        leg = parse_trip("{taxi,A,B}").legs[0]
        self.assertEqual(leg.mode, "taxi")
        self.assertEqual(leg.display_line, "taxi")

    def test_rejects_missing_destination(self) -> None:
        with self.assertRaises(TripFormatError):
            parse_trip("route:\n08:00 | walk | A")

    def test_parses_mult_style_timing(self) -> None:
        trip = parse_trip(
            "ending_overview: false\nwaypoint_pause_seconds: 0.8\nroute:\n08:00 | walk | A -> B"
        )
        self.assertFalse(trip.ending_overview)
        self.assertEqual(trip.waypoint_pause_seconds, 0.8)

    def test_parses_compact_connected_routes(self) -> None:
        trip = parse_trip(
            "{kansai-airport-line,kansai airport,hineno;"
            "hanwa-line,hineno,tennoji;"
            "osaka-loop-line,tennoji,shin-imamiya}"
        )
        self.assertEqual(len(trip.legs), 3)
        self.assertEqual(trip.legs[0].line, "Kansai Airport Line")
        self.assertEqual(trip.legs[-1].destination.name, "Shin-Imamiya")
        self.assertEqual(
            [leg.origin.name for leg in trip.legs[1:]],
            [leg.destination.name for leg in trip.legs[:-1]],
        )

    def test_parses_compact_single_route(self) -> None:
        trip = parse_trip("{kansai-airport-line,kansai airport,shin-imamiya}")
        self.assertEqual(len(trip.legs), 1)
        self.assertEqual(trip.legs[0].origin.name, "Kansai Airport")
        self.assertEqual(trip.legs[0].destination.name, "Shin-Imamiya")

    def test_preserves_order_when_formats_are_mixed(self) -> None:
        trip = parse_trip(
            "route:\n08:00 | walk | A -> B\n{bus,B,C}\n09:00 | walk | C -> D"
        )
        self.assertEqual([leg.origin.name for leg in trip.legs], ["A", "B", "C"])
