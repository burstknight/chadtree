from asyncio import gather
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from pynvim_pp.nvim import Nvim

from ..consts import SESSION_DIR
from ..fs.cartographer import new
from ..nvim.markers import markers
from ..settings.types import Settings
from ..version_ctl.types import VCStatus
from .executor import AsyncExecutor
from .ops import load_session
from .types import Selection, Session, State


async def initial(settings: Settings, th: ThreadPoolExecutor) -> State:
    executor = AsyncExecutor(threadpool=th)
    cwd, marks = await gather(Nvim.getcwd(), markers())
    storage = (
        Path(await Nvim.fn.stdpath(str, "cache")) / "chad_sessions"
        if settings.xdg
        else SESSION_DIR
    )

    session = Session(workdir=cwd, storage=storage)
    stored = await load_session(session) if settings.session else None
    index = {cwd} | (stored.index if stored else frozenset())

    show_hidden = (
        stored.show_hidden
        if stored and stored.show_hidden is not None
        else settings.show_hidden
    )
    enable_vc = (
        stored.enable_vc
        if stored and stored.enable_vc is not None
        else settings.version_ctl.enable
    )

    selection: Selection = frozenset()
    node = await new(
        executor, follow_links=settings.follow_links, root=cwd, index=index
    )
    vc = VCStatus()

    current = None
    filter_pattern = None

    state = State(
        id=uuid4(),
        executor=executor,
        settings=settings,
        session=session,
        vim_focus=True,
        index=index,
        selection=selection,
        filter_pattern=filter_pattern,
        show_hidden=show_hidden,
        follow=settings.follow,
        follow_links=settings.follow_links,
        follow_ignore=settings.follow_ignore,
        enable_vc=enable_vc,
        width=settings.width,
        root=node,
        markers=marks,
        diagnostics={},
        vc=vc,
        current=current,
        window_order={},
        node_row_lookup=(),
    )
    return state
