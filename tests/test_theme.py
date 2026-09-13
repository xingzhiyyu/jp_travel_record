from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PIL import Image, ImageChops

from travel_record.models import Leg, Place, ResolvedLeg, Trip
from travel_record.parser import TripFormatError, parse_trip
from travel_record.renderer import VideoRenderer
from travel_record.sources import resolved_manifest
from travel_record.theme import apply_theme, night_amount, theme_color, readable_color, contrast_ratio


class ThemeInputTests(TestCase):
    def test_legacy_input_remains_day(self):
        self.assertEqual(parse_trip("{walk,A,B}").legs[0].theme, "day")

    def test_compact_theme_inherits_and_can_return_to_day(self):
        trip = parse_trip("{walk,A,B;bus,B,C,night;walk,C,D;walk,D,E,day}")
        self.assertEqual([leg.theme for leg in trip.legs], ["day", "night", "night", "day"])

    def test_mixed_input_and_via_points_inherit(self):
        trip = parse_trip("theme: night\n{walk,A,B}\n08:00 | walk | B -> C -> D | day\n{bus,D,E}")
        self.assertEqual([leg.theme for leg in trip.legs], ["night", "day", "day", "day"])

    def test_rejects_bad_theme_or_transition(self):
        for text in ("{walk,A,B,nigth}", "theme: auto\n{walk,A,B}",
                     "08:00 | walk | A -> B | dusk", "theme_transition_seconds: nan\n{walk,A,B}",
                     "theme_transition_seconds: 11\n{walk,A,B}"):
            with self.subTest(text=text), self.assertRaises(TripFormatError):
                parse_trip(text)

    def test_manifest_records_effective_theme(self):
        trip = parse_trip("{walk,A,B,night}")
        leg = ResolvedLeg(trip.legs[0], [(35, 135), (35.01, 135.01)], "#777777", "test")
        manifest = resolved_manifest(trip, [leg])
        self.assertEqual(manifest["legs"][0]["theme"], "night")


class ThemeRenderTests(TestCase):
    def test_day_is_unchanged_and_night_preserves_map_contrast_order(self):
        day = Image.new("RGB", (3, 1))
        day.putdata([(247, 247, 242), (32, 58, 66), (22, 119, 255)])
        self.assertIs(apply_theme(day, 0), day)
        night = apply_theme(day, 1)
        self.assertGreater(sum(night.getpixel((0, 0))), sum(night.getpixel((1, 0))))
        self.assertEqual(theme_color((22,119,255), 1), (22,119,255))
        self.assertLess(max(theme_color((255,255,252,245), 1)[:3]), 40)
        self.assertEqual(theme_color((255,255,252,245), 1)[3], 245)
        panel = theme_color((255,255,252,245), 1)
        self.assertGreater(panel[2], panel[0])

    def test_muted_official_transit_colours_keep_their_hue_at_night(self):
        self.assertEqual(theme_color("#C1A470", 1), (193, 164, 112))
        # Dark purple is lifted for contrast but remains purple rather than blue-grey.
        self.assertEqual(theme_color("#7B3C8D", 1), (131, 64, 150))

    def test_text_stays_readable_through_every_transition_sample(self):
        for i in range(101):
            amount = i / 100
            for backdrop in ((255,255,252), (247,247,242)):
                background = theme_color(backdrop, amount)
                for ink in ((32,58,66), (109,128,132)):
                    foreground = readable_color(ink, amount, background)
                    self.assertGreaterEqual(contrast_ratio(foreground, background), 4.5)

    def test_map_details_do_not_cancel_at_mid_transition(self):
        day = Image.new("RGB", (2,1))
        day.putdata([(247,247,242), (200,222,224)])
        for i in range(101):
            frame = apply_theme(day, i/100)
            first, second = frame.getpixel((0,0)), frame.getpixel((1,0))
            self.assertGreater(sum(first), sum(second))
            self.assertGreater(sum(abs(a-b) for a,b in zip(first,second)), 10)

    def test_theme_transition_is_bounded_reversible_and_starts_on_new_leg(self):
        self.assertEqual(night_amount("day", "night", 0, 1.5), 0)
        self.assertEqual(night_amount("day", "night", 1.5, 1.5), 1)
        self.assertEqual(night_amount("day", "night", 0, 0), 1)
        samples = [night_amount("day", "night", i / 100, 1.5) for i in range(201)]
        self.assertEqual(samples, sorted(samples))
        for i, amount in enumerate(samples):
            self.assertAlmostEqual(night_amount("night", "day", i / 100, 1.5), 1 - amount)

    def test_renderer_keeps_arrival_and_overview_theme_identical(self):
        legs = [ResolvedLeg(Leg("", "walk", Place("A"), Place("B"), 1, theme=theme),
                            [(35,135),(35.01,135.01)], "#777777", "test") for theme in ("day", "night")]
        trip = Trip("test", [r.leg for r in legs], width=640, height=360, basemap="silhouette")
        with TemporaryDirectory() as directory:
            renderer = VideoRenderer(trip, Path(directory))
            canvas = Image.new("RGB", (640,360), "#F7F7F2")
            with patch.object(renderer.basemap, "frame", return_value=canvas), patch.object(renderer.atlas, "locator"):
                first = renderer._frame(legs, 1, 0, 15)
                self.assertEqual(renderer._night, 0)
                last = renderer._frame(legs, 1, 1, 15)
                self.assertEqual(renderer._night, 1)
                overview = renderer._overview_frame(legs, 0, (35.01,135.01), (35.01,135.01), 15, 15)
                self.assertIsNone(ImageChops.difference(last, overview).getbbox())
                self.assertIsNotNone(ImageChops.difference(first, last).getbbox())
