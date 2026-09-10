import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import Mock, patch

from PIL import Image

from travel_record.models import Leg, Place, ResolvedLeg, Trip
from travel_record.renderer import Basemap, VideoRenderer, _leg_camera_zoom, _pause_camera_zoom, _zoom_transition


class ZoomTimingTests(TestCase):
    def test_zoom_velocity_and_acceleration_settle_at_pause_boundaries(self) -> None:
        h = 0.0001
        midpoint = _zoom_transition(15, 11, 0)
        before = _zoom_transition(15, 11, -h)
        after = _zoom_transition(15, 11, h)
        self.assertLess(abs(midpoint - before) / h, 0.00001)
        self.assertLess(abs(after - midpoint) / h, 0.00001)
        for direction in (-1, 1):
            first = _zoom_transition(15, 11, direction * h)
            second = _zoom_transition(15, 11, direction * 2 * h)
            self.assertLess(abs(second - 2 * first + midpoint) / h**2, 0.02)

    def test_transition_is_confined_to_one_second_on_each_side(self) -> None:
        self.assertEqual(_zoom_transition(15.0, 11.0, -2.0), 15.0)
        self.assertEqual(_zoom_transition(15.0, 11.0, -1.0), 15.0)
        self.assertEqual(_zoom_transition(15.0, 11.0, 0.0), 13.0)
        self.assertEqual(_zoom_transition(15.0, 11.0, 1.0), 11.0)
        self.assertEqual(_zoom_transition(15.0, 11.0, 2.0), 11.0)

    def test_leg_and_pause_join_continuously_at_switch(self) -> None:
        zooms = [15.0, 11.0]
        pause = 0.5
        before_pause = _leg_camera_zoom(0, 4.0, 4.0, zooms)
        at_switch = _pause_camera_zoom(0, zooms)
        after_switch = _leg_camera_zoom(1, 0.0, 4.0, zooms)
        self.assertEqual(before_pause, 13.0)
        self.assertEqual(at_switch, 13.0)
        self.assertEqual(after_switch, at_switch)

    def test_zoom_holds_at_midpoint_during_station_pause(self) -> None:
        zooms = [15.0, 11.0]
        self.assertEqual(_pause_camera_zoom(0, zooms), 13.0)

    def test_zoom_holds_away_from_switches(self) -> None:
        zooms = [15.0, 11.0, 14.0]
        self.assertEqual(_leg_camera_zoom(1, 1.5, 5.0, zooms), 11.0)


class BasemapStyleTests(TestCase):
    def test_checks_all_mirror_caches_before_contacting_network(self) -> None:
        http = Mock()
        http.cached.side_effect = [None, json.dumps({"elements": [{
            "tags": {"highway": "primary"},
            "geometry": [{"lat": 35.0, "lon": 139.0}, {"lat": 35.1, "lon": 139.1}],
        }]}).encode()]
        basemap = Basemap(http, 320, 180, "silhouette")
        roads = basemap._roads_for_view((35.0, 139.0), 14)
        self.assertEqual(roads, [("primary", [(35.0, 139.0), (35.1, 139.1)])])
        self.assertEqual(http.cached.call_count, 2)
        http.json.assert_not_called()
        # Moving within the same road cell must not reload its large payload.
        self.assertIs(basemap._roads_for_view((35.01, 139.01), 14), roads)
        self.assertEqual(http.cached.call_count, 2)

    def test_soft_style_reduces_saturation_and_lifts_dark_colors(self) -> None:
        source = Image.new("RGB", (1, 1), (20, 160, 50))
        styled = Basemap._soft_map_style(source).getpixel((0, 0))
        self.assertLess(max(styled) - min(styled), 140)
        self.assertGreater(sum(styled), sum(source.getpixel((0, 0))))

    def test_extracts_only_valid_road_geometries(self) -> None:
        roads = Basemap._extract_roads(
            {
                "elements": [
                    {
                        "tags": {"highway": "motorway"},
                        "geometry": [{"lat": 35.0, "lon": 139.0}, {"lat": 35.1, "lon": 139.1}],
                    },
                    {"tags": {"highway": "primary"}, "geometry": [{"lat": 35.0, "lon": 139.0}]},
                ]
            }
        )
        self.assertEqual(roads, [("motorway", [(35.0, 139.0), (35.1, 139.1)])])


class VideoOutputTests(TestCase):
    def test_transfer_frame_uses_interpolated_center_without_changing_route(self) -> None:
        leg = Leg("", "walk", Place("A"), Place("B"), 1)
        route = ResolvedLeg(leg, [(34.7, 135.49), (34.71, 135.5)], "#777777", "test")
        with TemporaryDirectory() as directory:
            renderer = VideoRenderer(Trip("test", [leg], basemap="silhouette"), Path(directory))
            renderer.basemap = Mock()
            renderer.basemap.frame.return_value = Image.new("RGB", (1280, 720))
            renderer.atlas = Mock()
            transfer_center = (34.7105, 135.501)
            renderer._frame([route], 0, 1, 14, center_override=transfer_center)
            renderer.basemap.frame.assert_called_once_with(transfer_center, 14)
            self.assertEqual(renderer.atlas.frame.call_args.args[3].center, transfer_center)
            self.assertEqual(route.path[-1], (34.71, 135.5))

    def test_interruption_preserves_previous_video_and_removes_partial_output(self) -> None:
        leg = Leg("", "JR Kyoto Line", Place("Osaka"), Place("Shin-Osaka"), 1)
        resolved = ResolvedLeg(leg, [(34.70, 135.49), (34.73, 135.50)], "#0072BC", "test")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            destination = root / "trip.mp4"
            previous = b"previous complete video"
            destination.write_bytes(previous)
            renderer = VideoRenderer(Trip("test", [leg]), root / "cache")
            with patch("imageio_ffmpeg.get_ffmpeg_exe", return_value="ffmpeg"), \
                 patch("travel_record.renderer.subprocess.Popen") as popen, \
                 patch.object(renderer, "_frame", side_effect=KeyboardInterrupt):
                with self.assertRaises(KeyboardInterrupt):
                    renderer.render([resolved], destination)
                popen.return_value.kill.assert_called_once()
                popen.return_value.wait.assert_called_once()
            self.assertEqual(destination.read_bytes(), previous)
            self.assertEqual(list(root.glob(".trip-*.mp4")), [])
