from dataclasses import dataclass
from json import loads
from pathlib import Path
from typing import Mapping, Tuple

from std2.coloursys import hex_inverse
from std2.graphlib import merge
from std2.pickle.decoder import new_decoder
from yaml import safe_load

from chad_types import ASSETS, Hex, IconGlyphs, IconGlyphSet, TextColours, TextColourSet

from ..run import docker_run


@dataclass(frozen=True)
class _TCAliases:
    ext_exact: Mapping[str, str]
    name_exact: Mapping[str, str]
    name_glob: Mapping[str, str]


@dataclass(frozen=True)
class _Aliases:
    icon_colours: Mapping[str, str]
    text_colours: _TCAliases


_DOCKERFILE = Path(__file__).resolve(strict=True).with_name("Dockerfile")
_ICON_BASE = ASSETS / "icon_base.yml"
_ALIASES = ASSETS / "aliases.yml"


def _process_exts(exts: Mapping[str, str]) -> Mapping[str, str]:
    return {f".{k}": v for k, v in exts.items()}


def _process_glob(glob: Mapping[str, str]) -> Mapping[str, str]:
    return {k.rstrip("$").replace(r"\.", "."): v for k, v in glob.items()}


def _process_hexcode(colours: Mapping[str, str]) -> Mapping[str, Hex]:
    return {k: f"#{v}" for k, v in colours.items()}


def _process_inverse(colours: Mapping[str, str]) -> Mapping[str, str]:
    return {k: hex_inverse(v) for k, v in colours.items()}


def _process_icons(icons: IconGlyphs) -> IconGlyphs:
    return IconGlyphs(
        default_icon=icons.default_icon,
        folder=icons.folder,
        link=icons.link,
        status=icons.status,
        ext_exact=_process_exts(icons.ext_exact),
        name_exact=icons.name_exact,
        name_glob=_process_glob(icons.name_glob),
    )


def _process_colours(colours: TextColours) -> TextColours:
    return TextColours(
        ext_exact=_process_hexcode(_process_exts(colours.ext_exact)),
        name_exact=_process_hexcode(colours.name_exact),
        name_glob=_process_hexcode(_process_glob(colours.name_glob)),
    )


def _make_lightmode(colours: TextColours) -> TextColours:
    return TextColours(
        ext_exact=_process_inverse(colours.ext_exact),
        name_exact=_process_inverse(colours.name_exact),
        name_glob=_process_inverse(colours.name_glob),
    )


def load_text_decors() -> Tuple[IconGlyphSet, TextColourSet]:
    i_decode = new_decoder[IconGlyphSet](IconGlyphSet, strict=False)
    c_decode = new_decoder[TextColourSet](TextColourSet, strict=False)
    a_decode = new_decoder[_Aliases](_Aliases)

    icon_base = safe_load(_ICON_BASE.read_text("UTF-8"))
    aliases = safe_load(_ALIASES.read_text("UTF-8"))

    json = loads(docker_run(_DOCKERFILE))
    data = merge(json, icon_base)
    icon_spec = i_decode(data)
    ali = a_decode(aliases)

    icon_set = IconGlyphSet(
        ascii=_process_icons(icon_spec.ascii),
        ascii_hollow=_process_icons(icon_spec.ascii_hollow),
        devicons=_process_icons(icon_spec.devicons),
        emoji=_process_icons(icon_spec.emoji),
    )
    colour_spec = c_decode(data)
    colour_set = TextColourSet(
        nerdtree_syntax_light=_make_lightmode(
            _process_colours(colour_spec.nerdtree_syntax_light)
        ),
        nerdtree_syntax_dark=_process_colours(colour_spec.nerdtree_syntax_dark),
    )
    return icon_set, colour_set
