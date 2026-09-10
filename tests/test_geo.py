from unittest import TestCase

from travel_record.geo import graph_path, polyline_lengths


class RailGraphPathTests(TestCase):
    def test_parallel_platform_tracks_do_not_force_a_remote_crossover(self) -> None:
        coordinates = {
            1: (35.00000, 135.00000),
            2: (35.00000, 135.01000),
            3: (35.00000, 135.02000),
            4: (35.00005, 135.00000),
            5: (35.00005, 135.01000),
            6: (35.00005, 135.02000),
        }
        edges = [(1, 2), (2, 3), (3, 6), (6, 5), (5, 4)]

        path = graph_path(
            edges,
            coordinates,
            coordinates[1],
            coordinates[5],
        )

        self.assertLess(polyline_lengths(path)[1], 1_200)
        self.assertLess(max(point[1] for point in path), 135.011)

