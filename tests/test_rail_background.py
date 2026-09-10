from unittest import TestCase

from travel_record.rail_background import UrbanRailNetwork


class UrbanRailTests(TestCase):
    def test_all_operating_modes_including_tunnels_are_kept_and_deduplicated(self):
        def way(osm_id, railway, **tags):
            return {
                "type": "way",
                "id": osm_id,
                "tags": {"railway": railway, **tags},
                "geometry": [{"lat": 35, "lon": 139}, {"lat": 35.1, "lon": 139.1}],
            }

        ways = [
            way(n, kind)
            for n, kind in enumerate(
                ["rail", "subway", "light_rail", "tram", "monorail", "funicular"], 1
            )
        ]
        ways += [
            way(2, "subway", tunnel="yes"),
            way(7, "rail", service="yard"),
            way(8, "construction"),
            way(9, "rail", disused="yes"),
        ]
        extracted = UrbanRailNetwork.extract({"elements": ways})
        self.assertEqual({w.osm_id for w in extracted}, set(range(1, 8)))
        self.assertEqual(len(extracted), 7)
        self.assertTrue(next(w for w in extracted if w.osm_id == 7).service)

    def test_route_master_color_is_inherited_and_unknown_tracks_are_neutral(self):
        data = {
            "elements": [
                {
                    "type": "relation",
                    "id": 10,
                    "tags": {"type": "route_master", "colour": "#00aaff"},
                    "members": [{"type": "relation", "ref": 11}],
                },
                {
                    "type": "relation",
                    "id": 11,
                    "tags": {"type": "route", "route": "subway"},
                    "members": [{"type": "way", "ref": 1}],
                },
                *[
                    {
                        "type": "way",
                        "id": n,
                        "tags": {"railway": "subway"},
                        "geometry": [
                            {"lat": 35, "lon": 139},
                            {"lat": 35.1, "lon": 139.1},
                        ],
                    }
                    for n in (1, 2)
                ],
            ]
        }
        ways = {w.osm_id: w for w in UrbanRailNetwork.extract(data)}
        self.assertEqual(ways[1].color, (0, 170, 255))
        self.assertEqual(ways[2].color, (113, 133, 143))
