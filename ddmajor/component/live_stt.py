import asyncio
import logging
import os

from datetime import datetime

import bilibili_api as biliapi

from .DDMajorInterface import DDMajorInterface


class DDMajorSTT(DDMajorInterface):

    async def _check_online(self) -> None:
        logger = logging.getLogger(f"({self.dd_name})check_online")

        info = await self.live_room.get_room_play_info()
        is_online = (info.get("live_status", -1) == 1)

        if not self._stt_is_online and is_online:
            live_time = info.get("live_time")
            live_time = datetime.now() if not live_time else datetime.fromtimestamp(live_time)
            logger.info(f"于{live_time.strftime('%H:%M:%S')}开始直播了")

        if self._stt_is_online and not is_online:
            logger.info("下播了")

        self._stt_is_online = is_online


    async def transcribe(self) -> None:

        logger = logging.getLogger(f"({self.dd_name})transcribe")

        if not self._stt_is_online: return

        if not self._stt_fp:
            self._stt_fp = open(
                os.path.join(
                    self._stt_output_dir,
                    f"{self.live_room.room_display_id}_{int(self._stt_live_time.timestamp())}.txt"
                ),
                "w", encoding="utf-8"
            )

        try:
            logger.info(f"start to transcribe into {self._stt_fp.name}")
        except Exception as e:
            logger.warning(e)
            await asyncio.sleep(1)
            await self.transcribe()



    async def _init_async(self, **kwargs) -> None:
        logger = logging.getLogger(f"({self.dd_name})STT.init_async")

        task = self.config.get("task")
        cmpt = task.get("components", [])

        flag_enable_stt = False

        for k in range(len(cmpt)):
            component = cmpt[k]
            if component.get("type", "") == "live_stt":
                flag_enable_stt = True
                break

        if flag_enable_stt:
            logger.info("enable speech to text component")

            self._stt_output_dir = component["output_dir"]

            self.live_room = biliapi.live.LiveRoom(
                room_display_id=task.get("room_id"),
                credential=self.bili_cred,
            )

            # self.live_danmaku = biliapi.live.LiveDanmaku(
            #     room_display_id=task.get("room_id"),
            #     credential=self.bili_cred,
            # )
            # self._danmaku_task = asyncio.create_task(self.live_danmaku.connect())

            self._stt_fp = None
            self._stt_is_online = False
            self._stt_live_time = datetime.now()

            self.scheduler.add_job(
                self._check_online,
                "interval",
                seconds=int(task.get("interval", 60)),
                id=f"check_online({self.dd_name})",
                replace_existing=True,
            )

            await self._check_online()

            transcribe_task = self._event_loop.create_task(self.transcribe())
            self._background_tasks.append(transcribe_task)
            transcribe_task.add_done_callback(self._background_tasks.remove)


    def stop(self) -> None:
        if self._stt_fp: self._stt_fp.close()