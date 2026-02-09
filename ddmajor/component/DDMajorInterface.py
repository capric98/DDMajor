import asyncio
import logging
import threading
import typing

import bilibili_api as biliapi

from apscheduler.schedulers.asyncio import AsyncIOScheduler


class DDMajorInterface(typing.Protocol):

    config: dict
    dd_name: str
    logger: logging.Logger

    bili_cred: biliapi.Credential
    scheduler: AsyncIOScheduler

    _thread: threading.Thread
    _background_tasks: list[asyncio.Task]
    _event_loop: asyncio.AbstractEventLoop

    async def _init_async(self, **kwargs) -> None:
        pass