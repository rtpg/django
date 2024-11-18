import os
from test_sqlite import *  # NOQA

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "USER": "user",
        "NAME": "django",
        "PASSWORD": "postgres",
        "HOST": "localhost",
        "PORT": 5432,
        "OPTIONS": {
            "server_side_binding": os.getenv("SERVER_SIDE_BINDING") == "1",
        },
    },
    "other": {
        "ENGINE": "django.db.backends.postgresql",
        "USER": "user",
        "NAME": "django2",
        "PASSWORD": "postgres",
        "HOST": "localhost",
        "PORT": 5432,
    },
}

# XXX REMOVE LATER
import asyncio
import signal

# from rdrawer.output import SIO

from io import TextIOBase


class SIO(TextIOBase):
    buf: str

    def __init__(self, parent: "SIO | None" = None, label=None):
        self.buf = ""
        self.parent = parent
        self.label = None
        super().__init__()

    def write(self, s, /) -> int:
        """
        Write input to the item, and then write back the number of characters
        written
        """
        self.buf += s
        return len(s)

    def flush(self):
        if self.parent is not None:
            for line in self.buf.splitlines(keepends=True):
                # write at at extra indentation
                self.parent.write(f"  {line}")
            self.buf = ""

    def close(self):
        self.flush()
        if self.label is not None:
            self.write("-" * 10)
        super().close()

    # XXX change interface to just use the same object all the time
    def group(self, label=None):
        if label is not None:
            self.write("|" + label)
            self.write("-" * (len(label) + 1) + "\n")
        return SIO(parent=self)

    def print(self, f):
        self.write(f + "\n")


def output_pending_tasks(signum, frame):
    print("PENDING HOOK TASK TRIGGERED")
    import traceback

    try:
        # Some code that raises an exception
        1 / 0
    except Exception as e:
        # Print the traceback
        traceback.print_exc()
    tasks = asyncio.all_tasks(loop=asyncio.get_event_loop())
    sio = SIO()

    sio.print(f"{len(tasks)} pending tasks")
    sio.print("Tasks are...")
    for task in tasks:
        from rdrawer.asyncio import describe_awaitable

        with sio.group(label="Task") as group:
            describe_awaitable(task, group)
    print(sio.buf)


def pending_task_hook():
    signal.signal(signal.SIGUSR2, output_pending_tasks)


pending_task_hook()
import asyncio
import inspect
from asyncio import Future, Task
from inspect import _Traceback, FrameInfo
from typing import Any


def is_asyncio_shield(stack: list[FrameInfo]):
    return stack[0].frame.f_code == asyncio.shield.__code__


def described_stack(stack: list[FrameInfo]):
    result = ""
    if is_asyncio_shield(stack):
        result += "! Asyncio.shield found\n"
    for frame in stack:
        ctx = (
            frame.code_context[frame.index or 0] or "(Unknown)"
            if frame.code_context
            else "(Unknown)"
        )
        if ctx[-1] != "\n":
            ctx += "\n"
        result += f"At {frame.filename}:{frame.lineno}\n"
        result += f"-> {ctx}"
    result += "\n"
    return result


class TracedFuture(asyncio.Future):
    trace: list[FrameInfo]

    def __init__(self, *, loop) -> None:
        super().__init__(loop=loop)
        self.trace = inspect.stack(context=3)[2:]

    @property
    def is_asyncio_shield_call(self):
        return is_asyncio_shield(self.trace)

    def get_shielded_future(self):
        # Only valid if working on an asyncio.shield call
        return self.trace[0].frame.f_locals["inner"]

    def describe_context(self, sio: SIO):
        out = described_stack(self.trace)
        sio.print(out)
        if self.is_asyncio_shield_call:
            with sio.group("Shielded Future") as fut_sio:
                describe_awaitable(self.get_shielded_future(), fut_sio)

    def described_context(self):
        return described_stack(self.trace)


def describe_awaitable(awaitable, sio: SIO):
    if isinstance(awaitable, Task):
        task = awaitable
        task.print_stack(file=sio)
        if task._fut_waiter is not None:
            with sio.group("Waiting on") as wait_on_grp:
                describe_awaitable(task._fut_waiter, wait_on_grp)

                # awaiting_fut = task._fut_waiter
                # if hasattr(awaiting_fut, "describe_context"):
                #     awaiting_fut.describe_context(wait_on_grp)
                # else:
                #     wait_on_grp.print(f"Waiting on future of type {awaiting_fut}")
        else:
            sio.print("Not waiting?")
    elif isinstance(awaitable, TracedFuture):
        fut = awaitable
        sio.print(str(fut))
        fut.describe_context(sio)
    else:
        sio.print("Unknown awaitable...")
        sio.print(str(awaitable))


class TracingEventLoop(asyncio.SelectorEventLoop):
    """
    An event loop that should keep track of where futures
    are created
    """

    def create_future(self) -> Future[Any]:
        print("CREATED FUTURE")
        return TracedFuture(loop=self)


def tracing_event_loop_factory() -> type[asyncio.AbstractEventLoop]:
    print("GOT POLICY")
    return TracingEventLoop


asyncio.set_event_loop(TracingEventLoop())
