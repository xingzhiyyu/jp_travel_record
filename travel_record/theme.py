"""A hue-preserving night palette and continuous, explicit theme transitions."""

import colorsys
from functools import lru_cache

from PIL import Image, ImageFilter, ImageColor


def night_rgb(r, g, b, *, ui=False):
    hue, lightness, saturation = colorsys.rgb_to_hls(r, g, b)
    if not ui:
        # Keep the map's light/dark ordering. Inverting it then dissolving
        # makes land, water and roads converge to the same grey halfway.
        if saturation < 0.4 or lightness > 0.8:
            hue, saturation = 0.59, 0.28
        else:
            saturation *= 0.65
        return colorsys.hls_to_rgb(hue, 0.035 + 0.19 * lightness**1.8, saturation)
    if ui and saturation > 0.4 and lightness < 0.8:
        return colorsys.hls_to_rgb(hue, max(0.42, lightness), saturation)
    if saturation < 0.4 or lightness > 0.8:
        hue, saturation = 0.59, 0.23
    else:
        saturation *= 0.60
    lightness = 0.075 + (0.80 if ui else 0.50) * (1 - lightness)
    return colorsys.hls_to_rgb(hue, lightness, saturation)


def theme_color(color, amount):
    if color is None or amount <= 0:
        return color
    values = ImageColor.getrgb(color) if isinstance(color, str) else color
    dark = night_rgb(*(v / 255 for v in values[:3]), ui=True)
    return tuple(round(a * (1 - amount) + b * 255 * amount) for a, b in zip(values[:3], dark)) + tuple(values[3:])


def luminance(color):
    values = ImageColor.getrgb(color) if isinstance(color, str) else color
    channels = [v / 255 for v in values[:3]]
    channels = [v / 12.92 if v <= 0.04045 else ((v + 0.055) / 1.055)**2.4 for v in channels]
    return sum(v * w for v, w in zip(channels, (0.2126, 0.7152, 0.0722)))


def contrast_ratio(first, second):
    a, b = luminance(first), luminance(second)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def readable_color(color, amount, background):
    """Switch ink polarity at the contrast crossover, never through grey ink."""
    values = ImageColor.getrgb(color) if isinstance(color, str) else color
    white = contrast_ratio((255,255,255), background) > contrast_ratio((0,0,0), background)
    preferred = theme_color(values, 1 if white else 0)
    if contrast_ratio(preferred, background) >= 4.5:
        return preferred
    endpoint = 255 if white else 0
    # Minimal adjustment toward readable ink; preserve alpha and hue as far
    # as possible. Black/white always offers >= 4.58:1 on an opaque backdrop.
    for step in range(1, 101):
        candidate = tuple(round(v + (endpoint - v) * step / 100) for v in preferred[:3]) + tuple(values[3:])
        if contrast_ratio(candidate, background) >= 4.5:
            return candidate
    return (endpoint, endpoint, endpoint) + tuple(values[3:])


class ThemeDraw:
    """Apply UI colors before rasterization, avoiding inverted text fringes."""
    def __init__(self, draw, amount):
        self.draw, self.amount = draw, amount

    def __getattr__(self, name):
        method = getattr(self.draw, name)
        def themed(*args, **kwargs):
            original_fill = kwargs.get("fill")
            backdrop = kwargs.get("stroke_fill") or (255,255,252)
            for key in ("fill", "outline", "stroke_fill"):
                if key in kwargs:
                    kwargs[key] = theme_color(kwargs[key], self.amount)
            if name == "text" and original_fill is not None:
                kwargs["fill"] = readable_color(original_fill, self.amount, theme_color(backdrop, self.amount))
            if name == "line" and isinstance(original_fill, tuple) and original_fill[:3] == (109,128,132):
                paper = (247,247,242)
                night = night_rgb(*(v/255 for v in paper))
                background = tuple(round(v*(1-self.amount)+n*255*self.amount) for v,n in zip(paper,night))
                kwargs["fill"] = readable_color(original_fill, self.amount, background)
            return method(*args, **kwargs)
        return themed


@lru_cache(maxsize=1)
def night_palette() -> ImageFilter.Color3DLUT:
    def recolor(r, g, b):
        return night_rgb(r, g, b)

    return ImageFilter.Color3DLUT.generate(33, recolor)


def apply_theme(frame: Image.Image, night: float) -> Image.Image:
    if night <= 0:
        return frame
    dark = frame.filter(night_palette())
    return dark if night >= 1 else Image.blend(frame, dark, night)


def night_amount(previous: str, current: str, elapsed: float, duration: float) -> float:
    start, end = float(previous == "night"), float(current == "night")
    if start == end or duration <= 0:
        return end
    t = max(0.0, min(1.0, elapsed / duration))
    t = t * t * t * (t * (6 * t - 15) + 10)
    return start + (end - start) * t
