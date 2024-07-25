from dataclasses import dataclass
from enum import Enum, IntEnum, auto
from itertools import chain, repeat
from typing import (
    AbstractSet,
    Callable,
    Iterator,
    Mapping,
    MutableMapping,
    MutableSet,
    Optional,
    Tuple,
    Union,
)
from uuid import uuid4

from pynvim_pp.highlight import HLgroup
from std2.coloursys import rgb_to_hex

from ..consts import FM_HL_PREFIX
from ..fs.types import Mode


class _Style(IntEnum):
    bold = auto()
    dimmed = auto()
    italic = auto()
    underline = auto()
    blink = auto()
    blink_fast = auto()
    reverse = auto()
    hidden = auto()
    strikethrough = auto()


class _Ground(Enum):
    fore = auto()
    back = auto()


class _AnsiColour(IntEnum):
    black = auto()
    red = auto()
    green = auto()
    yellow = auto()
    blue = auto()
    magenta = auto()
    cyan = auto()
    white = auto()

    bright_black = auto()
    bright_red = auto()
    bright_green = auto()
    bright_yellow = auto()
    bright_blue = auto()
    bright_magenta = auto()
    bright_cyan = auto()
    bright_white = auto()


@dataclass(frozen=True)
class _Colour:
    r: int
    g: int
    b: int


@dataclass(frozen=True)
class _Styling:
    styles: AbstractSet[_Style]
    foreground: Union[_AnsiColour, _Colour, None]
    background: Union[_AnsiColour, _Colour, None]


@dataclass(frozen=True)
class LSC:
    mode_pre: Mapping[Mode, HLgroup]
    mode_post: Mapping[Optional[Mode], HLgroup]
    exts: Mapping[str, HLgroup]
    name_glob: Mapping[str, HLgroup]


_ANSI_RANGE = range(256)
_RGB_RANGE = range(256)

_STYLE_TABLE: Mapping[str, _Style] = {str(code + 0): code for code in _Style}

_GROUND_TABLE: Mapping[str, _Ground] = {
    str(code): ground
    for code, ground in chain(
        zip(chain(range(30, 39), range(90, 98)), repeat(_Ground.fore)),
        zip(chain(range(40, 49), range(100, 108)), repeat(_Ground.back)),
    )
}

_COLOUR_TABLE: Mapping[str, _AnsiColour] = {
    str(code): colour
    for code, colour in chain(
        ((c + 29 if c <= 8 else c + 31, c) for c in _AnsiColour),
        ((c + 89 if c <= 8 else c + 91, c) for c in _AnsiColour),
    )
}

_RGB_TABLE: AbstractSet[str] = {"38", "48"}

_E_BASIC_TABLE: Mapping[int, _AnsiColour] = {i: c for i, c in enumerate(_AnsiColour)}

_E_GRAY_TABLE: Mapping[int, _Colour] = {
    i: _Colour(r=s, g=s, b=s)
    for i, s in enumerate((round(step / 23 * 255) for step in range(24)), 232)
}

_LEN_LO = len(_E_BASIC_TABLE)


def _parse_8(codes: Iterator[str]) -> Union[_AnsiColour, _Colour, None]:
    try:
        ansi_code = int(next(codes, ""))
    except ValueError:
        return None
    else:
        if ansi_code in _ANSI_RANGE:
            if basic := _E_BASIC_TABLE.get(ansi_code):
                return basic
            elif grey := _E_GRAY_TABLE.get(ansi_code):
                return grey
            else:
                code = ansi_code - _LEN_LO
                r = code // 36
                g = code % 36 // 6
                b = code % 6
                xt_r = 55 + r * 40
                xt_g = 55 + g * 40
                xt_b = 55 + b * 40
                clr = _Colour(r=xt_r, g=xt_g, b=xt_b)
                return clr
        else:
            return None


def _parse_24(codes: Iterator[str]) -> Optional[_Colour]:
    try:
        r, g, b = int(next(codes, "")), int(next(codes, "")), int(next(codes, ""))
    except ValueError:
        return None
    else:
        if r in _RGB_RANGE and g in _RGB_RANGE and b in _RGB_RANGE:
            return _Colour(r=r, g=g, b=b)
        else:
            return None


_PARSE_TABLE: Mapping[
    str, Callable[[Iterator[str]], Union[_AnsiColour, _Colour, None]]
] = {
    "5": _parse_8,
    "2": _parse_24,
}


_SPECIAL_PRE_TABLE: Mapping[str, Mode] = {
    "bd": Mode.block_device,
    "ca": Mode.file_w_capacity,
    "cd": Mode.char_device,
    "di": Mode.folder,
    "do": Mode.door,
    "ex": Mode.executable,
    "ln": Mode.link,
    "mh": Mode.multi_hardlink,
    "or": Mode.orphan_link,
    "ow": Mode.other_writable,
    "pi": Mode.pipe,
    "sg": Mode.set_gid,
    "so": Mode.socket,
    "st": Mode.sticky,
    "su": Mode.set_uid,
    "tw": Mode.sticky_other_writable,
}


_SPECIAL_POST_TABLE: Mapping[str, Optional[Mode]] = {
    "fi": Mode.file,
    "no": None,
}

_UNUSED = {
    "mi": "colour of missing symlink pointee",
    "cl": "ANSI clear",
    "ec": "ANSI end_code",
    "lc": "ANSI left_code",
    "rc": "ANSI right_code",
    "rs": "ANSI reset",
}

assert _UNUSED


_HL_STYLE_TABLE: Mapping[_Style, Optional[str]] = {
    _Style.bold: "bold",
    _Style.dimmed: None,
    _Style.italic: "italic",
    _Style.underline: "underline",
    _Style.blink: None,
    _Style.blink_fast: None,
    _Style.reverse: "reverse",
    _Style.hidden: None,
    _Style.strikethrough: "strikethrough",
}


def _parse_codes(
    codes: str,
) -> Iterator[Union[_Style, Tuple[_Ground, Union[_AnsiColour, _Colour]]]]:
    it = (code.lstrip("0") for code in codes.split(";"))
    for code in it:
        style = _STYLE_TABLE.get(code)
        if style:
            yield style
            continue
        ground = _GROUND_TABLE.get(code)
        ansi_colour = _COLOUR_TABLE.get(code)
        if ground and ansi_colour:
            yield ground, ansi_colour
        elif ground and code in _RGB_TABLE:
            code = next(it, "")
            parse = _PARSE_TABLE.get(code)
            if parse:
                colour = parse(it)
                if colour:
                    yield ground, colour


def _parse_styling(codes: str) -> _Styling:
    styles: MutableSet[_Style] = set()
    colours: MutableMapping[_Ground, Union[_AnsiColour, _Colour]] = {}
    for ret in _parse_codes(codes):
        if isinstance(ret, _Style):
            styles.add(ret)
        elif isinstance(ret, tuple):
            ground, colour = ret
            colours[ground] = colour

    styling = _Styling(
        styles=styles,
        foreground=colours.get(_Ground.fore),
        background=colours.get(_Ground.back),
    )
    return styling


def _parseHLGroup(styling: _Styling, discrete_colours: Mapping[str, str]) -> HLgroup:
    fg, bg = styling.foreground, styling.background
    name = f"{FM_HL_PREFIX}_ls_{uuid4().hex}"
    cterm = {
        style
        for style in (_HL_STYLE_TABLE.get(style) for style in styling.styles)
        if style
    }
    ctermfg = fg.value - 1 if isinstance(fg, _AnsiColour) else None
    ctermbg = bg.value - 1 if isinstance(bg, _AnsiColour) else None
    guifg = (
        rgb_to_hex(fg.r, fg.g, fg.b)
        if isinstance(fg, _Colour)
        else (discrete_colours.get(fg.name) if isinstance(fg, _AnsiColour) else None)
    )
    guibg = (
        rgb_to_hex(bg.r, bg.g, bg.b)
        if isinstance(bg, _Colour)
        else (discrete_colours.get(bg.name) if isinstance(bg, _AnsiColour) else None)
    )
    group = HLgroup(
        name=name,
        cterm=cterm,
        ctermfg=ctermfg,
        ctermbg=ctermbg,
        guifg=guifg,
        guibg=guibg,
    )
    return group


def parse_lsc(ls_colours: str, discrete_colours: Mapping[str, str]) -> LSC:
    hl_lookup = {
        key: _parseHLGroup(_parse_styling(val), discrete_colours=discrete_colours)
        for key, _, val in (
            segment.partition("=") for segment in ls_colours.strip(":").split(":")
        )
    }

    mode_pre = {
        mode: hl
        for indicator, mode in _SPECIAL_PRE_TABLE.items()
        if (hl := hl_lookup.pop(indicator, None))
    }
    mode_post = {
        mode: hl
        for indicator, mode in _SPECIAL_POST_TABLE.items()
        if (hl := hl_lookup.pop(indicator, None))
    }

    _ext_keys = tuple(
        key for key in hl_lookup if key.startswith("*.") and key.count(".") == 1
    )
    exts = {key[1:]: hl_lookup.pop(key) for key in _ext_keys}

    lsc = LSC(exts=exts, mode_pre=mode_pre, mode_post=mode_post, name_glob=hl_lookup)
    return lsc
