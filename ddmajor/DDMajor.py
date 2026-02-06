import asyncio
import threading

import aiohttp


class DDMajor:

    def __init__(self, config: dict) -> None:
        self._event_loop = None
        self._main_task = None
        self._thread = None

    async def run_async(self) -> None:
        pass

    def run(self, block: bool=True) -> None:

        def _run() -> None:
            if not self._event_loop:
                self._event_loop = asyncio.new_event_loop()

            asyncio.set_event_loop(self._event_loop)
            self._main_task = self._event_loop.create_task(self.run_async())

            try:
                self._event_loop.run_until_complete(self._main_task)
            except asyncio.CancelledError:
                pass
            finally:
                self._event_loop.close()
                self._event_loop = None

        if not self._thread:
            self._thread = threading.Thread(target=_run, daemon=True)
            self._thread.start()

            if block:
                try:
                    while self._thread.is_alive():
                        self._thread.join(timeout=0.1)
                except KeyboardInterrupt:
                    self.stop()
                    raise


    def stop(self) -> None:
        if self._event_loop and self._main_task:
            self._event_loop.call_soon_threadsafe(self._main_task.cancel)
            if self._thread: self._thread.join(timeout=1)