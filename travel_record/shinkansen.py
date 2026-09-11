"""Name-based camera classification, NOT a route/geometry alias resolver.

Explicit Shinkansen names cover every corridor, including mini-Shinkansen.
Service aliases refer to their Shinkansen use; conventional/relay/sleeper
qualifiers take precedence. Historical unqualified names remain ambiguous.
"""

import re
import unicodedata


SERVICE_ALIASES = (
    ("nozomi", "のぞみ", "希望号", "希望號", "希望"),
    ("hikari", "ひかり", "光号", "光號", "Hikari Rail Star", "ひかりレールスター"),
    ("kodama", "こだま", "回声", "回聲", "木灵", "木靈"),
    ("mizuho", "みずほ", "瑞穗", "瑞穂"),
    ("sakura", "さくら", "樱", "櫻", "桜", "樱号", "櫻號", "桜号"),
    ("tsubame", "つばめ", "燕", "燕号", "燕號"),
    ("kamome", "かもめ", "海鸥", "海鷗"),
    ("hayabusa", "はやぶさ", "隼", "隼号", "隼號"),
    ("hayate", "はやて", "疾风", "疾風"),
    ("yamabiko", "やまびこ", "山彦", "山彥"),
    ("nasuno", "なすの", "那须野", "那須野"),
    ("komachi", "こまち", "小町"),
    ("tsubasa", "つばさ", "翼", "翼号", "翼號", "とれいゆつばさ", "Toreiyu Tsubasa"),
    ("toki", "とき", "朱鹭", "朱鷺"),
    ("tanigawa", "たにがわ", "谷川"),
    ("kagayaki", "かがやき", "辉", "輝", "辉号", "輝號", "光辉", "光輝"),
    ("hakutaka", "はくたか", "白鹰", "白鷹"),
    ("asama", "あさま", "浅间", "淺間", "浅間"),
    ("tsurugi", "つるぎ", "剑", "劍", "剣", "剑号", "劍號", "剣号"),
    ("aoba", "あおば", "青叶", "青葉"),
    ("asahi", "あさひ", "朝日"),
)


def _normalized(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold()
    value = "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in value)
    value = "".join(c for c in unicodedata.normalize("NFKD", value)
                    if not unicodedata.combining(c) or c in '\u3099\u309a')
    return unicodedata.normalize("NFC", value)


def _compact(value: str) -> str:
    return re.sub(r"[\s\-_'’・·「」『』\[\]（）()]+", "", _normalized(value))


_EXPLICIT = re.compile(r"新[幹干][線线]|しんかんせん|(?<![a-z])shin[\s-]*kan[\s-]*sen(?![a-z])|\bbullet[\s-]+train\b")
_CONVENTIONAL = re.compile(
    r"リレー|relay|接力|接駁|接驳|寝台|臥鋪|卧铺|sleeper|在来線|在来线|在來線|conventional|"
    r"新幹線連絡|新干线联络|新幹線接続|shinkansen\s+(?:connector|connection)"
)
_ALIASES = {_compact(alias) for family in SERVICE_ALIASES for alias in family}
_PREFIX = re.compile(r"^(?:jr(?:east|west|central|kyushu|hokkaido|東日本|西日本|東海|九州|北海道)?|"
                     r"特急|列車|列车|train|max|super|すーぱー)")
_BASES = _ALIASES | {alias[:-1] for alias in _ALIASES if alias.endswith(('号', '號'))}
_SERVICE = re.compile('(?:' + '|'.join(re.escape(a) for a in sorted(_BASES, key=len, reverse=True))
                      + r')(?:(?:no\.?|第)?\d+)?(?:号|號|ごう)?')


def is_shinkansen_name(name: str, *, services: bool = True) -> bool:
    # Check original NFKC text too: Katakana relay becomes Hiragana below.
    raw = unicodedata.normalize("NFKC", name).casefold()
    if _CONVENTIONAL.search(raw) or "りれー" in raw:
        return False
    if _EXPLICIT.search(_normalized(name)):
        return True
    if not services:
        return False
    parts = re.split(r"\s*[+/&・]\s*", name)
    if len(parts) > 1:
        return all(is_shinkansen_name(part) for part in parts)
    key = _compact(name)
    while match := _PREFIX.match(key):
        key = key[match.end():]
    return _SERVICE.fullmatch(key) is not None


def is_shinkansen(leg) -> bool:
    """Use resolved identity as well as user spelling, without network calls."""
    if leg.leg.mode != "rail":
        return False
    names = [leg.details.get(key) for key in ("osm_name", "osm_name_en", "canonical_line")]
    names = [name for name in names if isinstance(name, str)]
    # An explicit resolved Shinkansen identity beats a vague input alias.
    if any(is_shinkansen_name(name, services=False) for name in names):
        return True
    if any(_CONVENTIONAL.search(unicodedata.normalize("NFKC", name).casefold()) for name in names):
        return False
    return any(is_shinkansen_name(name) for name in [leg.leg.line, *names])
