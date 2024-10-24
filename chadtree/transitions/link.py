from os.path import normpath, relpath
from pathlib import PurePath
from typing import MutableMapping, Optional

from pynvim_pp.nvim import Nvim
from std2 import anext
from std2.locale import pathsort_key

from ..fs.cartographer import act_like_dir
from ..fs.ops import ancestors, exists, link, resolve
from ..lsp.notify import lsp_created
from ..registry import rpc
from ..settings.localization import LANG
from ..state.next import forward
from ..state.types import State
from ..view.ops import display_path
from .shared.current import maybe_path_above
from .shared.index import indices
from .shared.refresh import refresh
from .types import Stage


@rpc(blocking=False)
async def _link(state: State, is_visual: bool) -> Optional[Stage]:
    """
    Symlink selected
    """

    node = await anext(indices(state, is_visual=is_visual), None)
    if node is None:
        return None
    else:
        parent = (
            node.path
            if act_like_dir(node, follow_links=state.follow_links)
            else node.path.parent
        )
        selection = state.selection or {node.path}
        operations: MutableMapping[PurePath, PurePath] = {}
        for selected in selection:
            display = display_path(selected, state=state)
            if child := await Nvim.input(
                question=LANG("link", src=display), default=selected.name
            ):
                try:
                    dst = await resolve(parent / child, strict=False)
                except Exception as e:
                    await Nvim.write(e, error=True)
                    return None
                else:
                    if dst in operations or await exists(dst, follow=False):
                        await Nvim.write(
                            LANG("already_exists", name=normpath(dst)), error=True
                        )
                        return None
                    else:
                        src = PurePath(relpath(selected, start=dst.parent))
                        operations[dst] = src
            else:
                return None

        try:
            await link(operations)
        except Exception as e:
            await Nvim.write(e, error=True)
            return await refresh(state)
        else:
            paths = operations.keys()
            new_state = await maybe_path_above(state, paths=paths) or state
            await lsp_created(paths)
            focus, *_ = sorted(paths, key=pathsort_key)
            invalidate_dirs = {path.parent for path in paths}
            index = state.index | ancestors(*paths)
            next_state = await forward(
                new_state,
                index=index,
                invalidate_dirs=invalidate_dirs,
            )
            return Stage(next_state, focus=focus)


@rpc(blocking=False)
async def _new_link(state: State, is_visual: bool) -> Optional[Stage]:
    """
    Symlink at cursor
    """

    node = await anext(indices(state, is_visual=is_visual), None)
    if node is None:
        return None
    else:
        parent = (
            node.path
            if act_like_dir(node, follow_links=state.follow_links)
            else node.path.parent
        )

        if not (src := await Nvim.input(question=LANG("pencil"), default="")):
            return None

        if not (dst := await Nvim.input(question=LANG("link", src=""), default="")):
            return None

        else:
            operations = {parent / src: PurePath(dst)}

        try:
            await link(operations)
        except Exception as e:
            await Nvim.write(e, error=True)
            return await refresh(state)
        else:
            paths = operations.keys()
            await lsp_created(paths)
            focus, *_ = sorted(paths, key=pathsort_key)
            index = state.index | ancestors(*paths)
            invalidate_dirs = {path.parent for path in paths}
            next_state = await forward(
                state,
                index=index,
                invalidate_dirs=invalidate_dirs,
            )
            return Stage(next_state, focus=focus)
