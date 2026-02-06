import asyncio
import json
import logging
import threading

import bilibili_api as biliapi

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from .component.live_stt import DDMajorSTT


class DDMajor(DDMajorSTT):

    def __init__(self, config: dict, **kwargs) -> None:
        self._thread = None
        self._kwargs = kwargs
        self._event_loop = None
        self._background_tasks = []

        self.config: dict = config
        self.dd_name: str = config.get("task", {}).get("name", "unknown")

        self.bili_cred = None
        self.scheduler = None


    async def ensure_cred(self) -> None:
        if await self.bili_cred.check_refresh():
            await self.bili_cred.refresh()


    async def _init_async(self, **kwargs) -> None:

        biliapi.select_client("aiohttp") # httpx does not support websocket

        if not self._event_loop:
            self._event_loop = asyncio.new_event_loop()

        if not self.scheduler:
            self.scheduler = AsyncIOScheduler(event_loop=self._event_loop)
            self.scheduler.start()
            logging.getLogger("apscheduler").setLevel(logging.WARN)

        if not self.bili_cred and self.config.get("bili_credential"):
            self.bili_cred = biliapi.Credential(**self.config.get("bili_credential"))

            if not await self.bili_cred.check_valid():
                raise ValueError("bili_credential invalid")

        if self.bili_cred:
            if not self.scheduler.get_job("ensure_cred"):
                self.scheduler.add_job(
                    self.ensure_cred,
                    "interval",
                    minutes=60,
                    id="ensure_cred",
                    replace_existing=True,
                )

        await super()._init_async(**kwargs)


    async def run_async(self) -> None:

        await self._init_async(**self._kwargs)

        while True:
            await asyncio.sleep(60) # keep running


    def run(self, block: bool=True) -> None:

        logger = logging.getLogger("DDMajor.run")

        def _run() -> None:
            if not self._event_loop:
                self._event_loop = asyncio.new_event_loop()

            asyncio.set_event_loop(self._event_loop)

            main_task = self._event_loop.create_task(self.run_async())
            self._background_tasks.append(main_task)

            try:
                self._event_loop.run_until_complete(main_task)
            except asyncio.CancelledError:
                pass
            finally:
                self._event_loop.close()
                self._event_loop = None

        if not self._thread:
            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()

            logger.info(f"开始单推任务：{self.dd_name}")

            if block:
                try:
                    while self._thread.is_alive():
                        self._thread.join(timeout=0.1)
                except KeyboardInterrupt:
                    self.stop()
                    raise


    def stop(self) -> None:
        if self.scheduler and self.scheduler.running:
            self.scheduler.shutdown(wait=False)

        if self._event_loop and self._background_tasks:
            for bg_task in self._background_tasks:
                self._event_loop.call_soon_threadsafe(bg_task.cancel)
            if self._thread: self._thread.join(timeout=1)