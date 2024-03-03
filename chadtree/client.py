from asyncio import (
    FIRST_COMPLETED,
    AbstractEventLoop,
    Event,
    Lock,
    Task,
    create_task,
    gather,
    get_running_loop,
    wait,
)
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractAsyncContextManager, suppress
from functools import wraps
from logging import DEBUG as DEBUG_LVL
from logging import INFO
from multiprocessing import cpu_count
from pathlib import Path
from platform import uname
from string import Template
from sys import executable, exit
from textwrap import dedent
from time import monotonic
from typing import Any, Optional, Sequence, cast

from pynvim_pp.highlight import highlight
from pynvim_pp.logging import log, suppress_and_log
from pynvim_pp.nvim import Nvim, conn
from pynvim_pp.rpc_types import (
    Method,
    MsgType,
    NvimError,
    RPCallable,
    RPClient,
    ServerAddr,
)
from pynvim_pp.types import NoneType
from std2.asyncio import cancel
from std2.cell import RefCell
from std2.contextlib import nullacontext
from std2.pickle.types import DecodeError
from std2.platform import OS, os
from std2.sched import aticker
from std2.sys import autodie

from ._registry import ____
from .consts import DEBUG, RENDER_RETRIES
from .registry import autocmd, dequeue_event, enqueue_event, rpc
from .settings.load import initial as initial_settings
from .settings.localization import init as init_locale
from .state.load import initial as initial_state
from .state.types import State
from .timeit import timeit
from .transitions.autocmds import setup
from .transitions.redraw import redraw
from .transitions.schedule_update import scheduled_update
from .transitions.types import Stage

assert ____ or True

_CB = RPCallable[Optional[Stage]]


def _autodie(ppid: int) -> AbstractAsyncContextManager:
    if os is OS.windows:
        return nullacontext(None)
    else:
        return autodie(ppid)


async def _profile(t1: float) -> None:
    t2 = monotonic()
    info = uname()
    msg = f"""
    First msg  {int((t2 - t1) * 1000)}ms
    Arch       {info.machine}
    Processor  {info.processor}
    Cores      {cpu_count()}
    System     {info.system}
    Version    {info.version}
    Python     {Path(executable).resolve(strict=True)}
    """
    await Nvim.write(dedent(msg))


async def _sched(ref: RefCell[State]) -> None:
    await enqueue_event(False, method=scheduled_update.method, params=(True,))

    async for _ in aticker(ref.val.settings.polling_rate, immediately=False):
        if ref.val.vim_focus:
            await enqueue_event(False, method=scheduled_update.method)


def _trans(handler: _CB) -> _CB:
    @wraps(handler)
    async def f(*params: Any) -> None:
        await enqueue_event(True, method=handler.method, params=params)

    return cast(_CB, f)


async def _default(_: MsgType, method: Method, params: Sequence[Any]) -> None:
    await enqueue_event(True, method=method, params=params)


async def _go(loop: AbstractEventLoop, client: RPClient) -> None:
    th = ThreadPoolExecutor()
    atomic, handlers = rpc.drain()
    try:
        settings = await initial_settings(handlers.values())
    except DecodeError as e:
        tpl = """
                Some options may have changed.
                See help doc on Github under [docs/CONFIGURATION.md]


                ${e}
                """
        ms = Template(dedent(tpl)).substitute(e=e)
        await Nvim.write(ms, error=True)
        exit(1)
    else:
        hl = highlight(*settings.view.hl_context.groups)
        await (atomic + autocmd.drain() + hl).commit(NoneType)
        state = RefCell(await initial_state(settings, th=th))

        init_locale(settings.lang)
        with suppress_and_log():
            await setup(settings)

        for f in handlers.values():
            ff = _trans(f)
            client.register(ff)

        staged = RefCell[Optional[Stage]](None)
        event = Event()
        lock = Lock()

        async def step(method: Method, params: Sequence[Any]) -> None:
            if handler := cast(Optional[_CB], handlers.get(method)):
                with suppress_and_log():
                    async with lock:
                        if stage := await handler(state.val, *params):
                            state.val = stage.state
                            staged.val = stage
                            event.set()
            else:
                assert False, (method, params)

        async def c1() -> None:
            transcient: Optional[Task] = None
            get: Optional[Task] = None
            try:
                while True:
                    with suppress_and_log():
                        get = create_task(dequeue_event())
                        if transcient:
                            await wait((transcient, get), return_when=FIRST_COMPLETED)
                            if not transcient.done():
                                with timeit("transcient"):
                                    await cancel(transcient)
                            transcient = None

                        sync, method, params = await get
                        task = step(method, params=params)
                        if sync:
                            with timeit(method):
                                await task
                        else:
                            transcient = create_task(task)
            finally:
                await cancel(
                    *(get or loop.create_future(), transcient or loop.create_future())
                )

        async def c2() -> None:
            t1, has_drawn = monotonic(), False

            while True:
                await event.wait()
                with suppress_and_log():
                    try:
                        if stage := staged.val:
                            state = stage.state

                            for _ in range(RENDER_RETRIES - 1):
                                with suppress(NvimError):
                                    await redraw(state, focus=stage.focus)
                                    break
                            else:
                                try:
                                    await redraw(state, focus=stage.focus)
                                except NvimError as e:
                                    log.warn("%s", e)

                            if settings.profiling and not has_drawn:
                                has_drawn = True
                                await _profile(t1=t1)
                    finally:
                        event.clear()

        await gather(c1(), c2(), _sched(state))


async def init(socket: ServerAddr, ppid: int, th: ThreadPoolExecutor) -> None:
    loop = get_running_loop()
    loop.set_default_executor(th)
    log.setLevel(DEBUG_LVL if DEBUG else INFO)
    async with _autodie(ppid):
        async with conn(socket, default=_default) as client:
            await _go(loop, client=client)
