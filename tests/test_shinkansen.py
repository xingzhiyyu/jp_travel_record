from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from travel_record.models import Leg, Place, ResolvedLeg, Trip
from travel_record.renderer import VideoRenderer
from travel_record.shinkansen import SERVICE_ALIASES, is_shinkansen, is_shinkansen_name


def resolved(name, **details):
    return ResolvedLeg(Leg('', name, Place('A'), Place('B'), 1),
                       [(35, 135), (35, 137)], '#0072BC', 'test', details=details)


class ShinkansenTests(TestCase):
    def test_corridors_and_scripts(self):
        for corridor in ('東海道', '山陽', '東北', '北海道', '上越', '北陸', '長野',
                         '山形', '秋田', '九州', '西九州', '東海道・山陽', '中央'):
            for suffix in ('新幹線', '新干线', '新幹线', '新干線'):
                with self.subTest(name=corridor + suffix):
                    self.assertTrue(is_shinkansen_name(corridor + suffix))
        for name in ('JR Tōkaidō Shinkansen', 'Sanyō Shin-kan-sen',
                     'Nishi-Kyushu Shinkansen', 'ＪＲ ＳＨＩＮＫＡＮＳＥＮ',
                     'しんかんせん', 'シンカンセン', 'Bullet Train',
                     'Hello Kitty Shinkansen', '新幹線のぞみ12号'):
            with self.subTest(name=name):
                self.assertTrue(is_shinkansen_name(name))

    def test_every_service_alias_and_number(self):
        for family in SERVICE_ALIASES:
            for alias in family:
                with self.subTest(alias=alias):
                    self.assertTrue(is_shinkansen_name(alias))
                    self.assertTrue(is_shinkansen_name(alias + ' 123'))
        for name in ('のぞみ１２３号', 'NOZOMI No. 12', 'JR Hikari 507',
                     'Maxとき321号', 'Max Yamabiko 123', 'スーパーこまち',
                     'ハヤブサ３号', '光123号', '希望號', 'こだま号',
                     'はやぶさ・こまち', 'Yamabiko / Tsubasa'):
            with self.subTest(name=name):
                self.assertTrue(is_shinkansen_name(name))

    def test_conventional_and_substring_false_positives(self):
        for name in ('JR東海道本線', '山陽本線', '上越線', '北陸本線', 'はるか',
                     'Sakura Tram', 'さくら通り線', 'ときわ', 'Tokiwa', 'こまちバス',
                     'リレーかもめ', 'Relay Kamome 15', '接力海鸥', '新幹線リレー号',
                     'Shinkansen connector', '寝台特急さくら', 'Sleeper Hayabusa',
                     '在来線特急かもめ', 'Limited Express Thunderbird'):
            with self.subTest(name=name):
                self.assertFalse(is_shinkansen_name(name))

    def test_resolved_identity_and_non_rail(self):
        self.assertTrue(is_shinkansen(resolved('任意简称', osm_name='東海道新幹線')))
        self.assertTrue(is_shinkansen(resolved('任意简称', canonical_line='Tokaido Shinkansen')))
        self.assertFalse(is_shinkansen(resolved('かもめ', osm_name='リレーかもめ')))
        self.assertFalse(is_shinkansen(resolved('bus', osm_name='東海道新幹線')))

    def test_zoom_policy_is_language_independent(self):
        with TemporaryDirectory() as folder:
            renderer = VideoRenderer(Trip('test', [], basemap='none'), Path(folder))
            english = renderer._target_zooms([resolved('Tokaido Shinkansen')])[0]
            self.assertLess(english, 12.5)
            for name in ('東海道新幹線', '东海道新干线', 'のぞみ', 'Nozomi 1', '西九州新幹線'):
                self.assertEqual(renderer._target_zooms([resolved(name)])[0], english)
            for name in ('JR東海道本線', 'Relay Kamome'):
                self.assertEqual(renderer._target_zooms([resolved(name)])[0], 12.5)
