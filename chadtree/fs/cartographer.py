from asyncio import gather, sleep
from contextlib import suppress
from fnmatch import fnmatch
from itertools import count
from os import DirEntry, scandir, stat, stat_result
from os.path import normcase
from pathlib import Path, PurePath
from stat import (
    S_IFDOOR,
    S_ISBLK,
    S_ISCHR,
    S_ISDIR,
    S_ISFIFO,
    S_ISGID,
    S_ISLNK,
    S_ISREG,
    S_ISSOCK,
    S_ISUID,
    S_ISVTX,
    S_IWOTH,
    S_IXUSR,
)
from typing import AbstractSet, AsyncIterator, Iterator, Mapping, Optional, Tuple, Union

from std2.asyncio import pure
from std2.pathlib import is_relative_to

from ..state.executor import AsyncExecutor
from ..state.types import Index
from ..timeit import timeit
from .ops import ancestors
from .types import Ignored, Mode, Node

_COUNT = count()

_FILE_MODES: Mapping[int, Mode] = {
    S_IXUSR: Mode.executable,
    S_IFDOOR: Mode.door,
    S_ISGID: Mode.set_gid,
    S_ISUID: Mode.set_uid,
    S_ISVTX: Mode.sticky,
    S_IWOTH: Mode.other_writable,
    S_IWOTH | S_ISVTX: Mode.sticky_other_writable,
}


def _fs_modes(stat: stat_result) -> Iterator[Mode]:
    st_mode = stat.st_mode
    if S_ISDIR(st_mode):
        yield Mode.folder
    if S_ISREG(st_mode):
        yield Mode.file
    if S_ISFIFO(st_mode):
        yield Mode.pipe
    if S_ISSOCK(st_mode):
        yield Mode.socket
    if S_ISCHR(st_mode):
        yield Mode.char_device
    if S_ISBLK(st_mode):
        yield Mode.block_device
    if stat.st_nlink > 1:
        yield Mode.multi_hardlink
    for bit, mode in _FILE_MODES.items():
        if bit and st_mode & bit == bit:
            yield mode


def _fs_stat(
    dirent: Union[PurePath, DirEntry[str]]
) -> Tuple[AbstractSet[Mode], Optional[PurePath]]:
    try:
        info = (
            stat(dirent, follow_symlinks=False)
            if isinstance(dirent, PurePath)
            else dirent.stat(follow_symlinks=False)
        )
    except (FileNotFoundError, PermissionError):
        return {Mode.orphan_link}, None
    else:
        if S_ISLNK(info.st_mode):
            try:
                pointed = Path(dirent).resolve(strict=True)
                link_info = stat(pointed, follow_symlinks=False)
            except (FileNotFoundError, NotADirectoryError, RuntimeError):
                return {Mode.orphan_link}, None
            else:
                mode = {*_fs_modes(link_info)}
                return mode | {Mode.link}, pointed
        else:
            mode = {*_fs_modes(info)}
            return mode, None


async def _next(
    dirent: Union[PurePath, DirEntry[str]], follow_links: bool, index: Index
) -> Node:
    async def cont() -> AsyncIterator[Node]:
        with suppress(NotADirectoryError, FileNotFoundError, PermissionError):
            with scandir(dirent) as dirents:
                for child in dirents:
                    yield await _next(child, follow_links=follow_links, index=index)

    if next(_COUNT) % 17 == 0:
        await sleep(0)

    root = PurePath(dirent)
    mode, pointed = _fs_stat(dirent)
    if root in index and (follow_links or pointed is None):
        children = {node.path: node async for node in cont()}
    else:
        children = {}
    _ancestors = ancestors(root)
    node = Node(
        path=root,
        mode=mode,
        pointed=pointed,
        ancestors=_ancestors,
        children=children,
    )

    return node


def _cross_over(root: PurePath, invalid: PurePath) -> bool:
    return is_relative_to(root, invalid) or is_relative_to(invalid, root)


async def _update(
    root: Node, follow_links: bool, index: Index, invalidate_dirs: AbstractSet[PurePath]
) -> Node:
    if any((_cross_over(root.path, invalid=invalid) for invalid in invalidate_dirs)):
        return await _next(root.path, follow_links=follow_links, index=index)
    else:
        walked = await gather(
            *(
                gather(
                    pure(k),
                    _update(
                        v,
                        follow_links=follow_links,
                        index=index,
                        invalidate_dirs=invalidate_dirs,
                    ),
                )
                for k, v in root.children.items()
            )
        )
        children = {k: v for k, v in walked}
        return Node(
            path=root.path,
            mode=root.mode,
            pointed=root.pointed,
            ancestors=root.ancestors,
            children=children,
        )


async def new(
    exec: AsyncExecutor, root: PurePath, follow_links: bool, index: Index
) -> Node:
    with timeit("fs->new"):
        return await exec.submit(_next(root, follow_links=follow_links, index=index))


async def update(
    exec: AsyncExecutor,
    root: Node,
    *,
    follow_links: bool,
    index: Index,
    invalidate_dirs: AbstractSet[PurePath],
) -> Node:
    with timeit("fs->_update"):
        try:
            return await exec.submit(
                _update(
                    root=root,
                    follow_links=follow_links,
                    index=index,
                    invalidate_dirs=invalidate_dirs,
                )
            )
        except FileNotFoundError:
            return await new(
                exec, follow_links=follow_links, root=root.path, index=index
            )


def user_ignored(node: Node, ignores: Ignored) -> bool:
    return (
        node.path.name in ignores.name_exact
        or any(fnmatch(node.path.name, pattern) for pattern in ignores.name_glob)
        or any(fnmatch(normcase(node.path), pattern) for pattern in ignores.path_glob)
    )


def is_dir(node: Node) -> bool:
    return Mode.folder in node.mode


def act_like_dir(node: Node, follow_links: bool) -> bool:
    if node.pointed and not follow_links:
        return False
    else:
        return is_dir(node)
