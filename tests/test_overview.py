from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from PIL import Image, ImageChops

from travel_record.models import Leg, Place, ResolvedLeg, Trip
from travel_record.renderer import (
    FollowCamera,
    VideoRenderer,
    _overview_camera,
    _overview_duration,
    _smootherstep,
)


class OverviewCameraTests(TestCase):
    endpoint = (35.710167, 139.812333)
    center = (35.185, 137.65)

    def test_endpoint_follows_bounded_screen_path_without_jump_or_flyoff(self):
        final = FollowCamera(self.center, 8, 1280, 720).point(self.endpoint)
        for index in range(101):
            p = index / 100
            center, zoom = _overview_camera(p, self.endpoint, self.center, 16, 8)
            x, y = FollowCamera(center, zoom, 1280, 720).point(self.endpoint)
            self.assertAlmostEqual(
                x, 640 + (final[0] - 640) * _smootherstep(p), places=6
            )
            self.assertAlmostEqual(
                y, 360 + (final[1] - 360) * _smootherstep(p), places=6
            )
            self.assertTrue(0 <= x <= 1280 and 0 <= y <= 720)
        self.assertEqual(
            _overview_camera(0, self.endpoint, self.center, 16, 8), (self.endpoint, 16)
        )
        self.assertEqual(
            _overview_camera(1, self.endpoint, self.center, 16, 8), (self.center, 8)
        )

    def test_peak_zoom_speed_respects_setting_not_just_average_speed(self):
        seconds = _overview_duration(16, 8, 7, 0.9)
        for i in range(1000):
            first = _overview_camera(i / 1000, self.endpoint, self.center, 16, 8)[1]
            next_zoom = _overview_camera(
                (i + 1) / 1000, self.endpoint, self.center, 16, 8
            )[1]
            self.assertLessEqual(abs(next_zoom - first) / (seconds / 1000), 0.900001)

    def test_first_overview_frame_matches_arrival_and_marker_never_fades(self):
        leg = Leg("14:00", "walk", Place("Skytree"), Place("Oshiage"), 1)
        resolved = ResolvedLeg(
            leg, [(35.710063, 139.8107), self.endpoint], "#75808D", "test"
        )
        trip = Trip("test", [leg], basemap="silhouette")
        with TemporaryDirectory() as directory:
            renderer = VideoRenderer(trip, Path(directory))
            with patch.object(
                renderer.basemap,
                "frame",
                return_value=Image.new("RGB", (1280, 720), "white"),
            ), patch.object(renderer.atlas, "locator"):
                arrival = renderer._frame([resolved], 0, 1, 16)
                first = renderer._overview_frame(
                    [resolved], 0, self.endpoint, self.center, 16, 8
                )
                self.assertIsNone(ImageChops.difference(arrival, first).getbbox())
                for progress in (0.1, 0.5, 1):
                    frame = renderer._overview_frame(
                        [resolved], progress, self.endpoint, self.center, 16, 8
                    )
                    center, zoom = _overview_camera(
                        progress, self.endpoint, self.center, 16, 8
                    )
                    x, y = FollowCamera(center, zoom, 1280, 720).point(self.endpoint)
                    self.assertEqual(
                        frame.getpixel((round(x), round(y))), (22, 119, 255)
                    )
