import logging
import asyncio
import logging
import os
import json

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

            transcribe_task = self._event_loop.create_task(self.transcribe())
            self._background_tasks.append(transcribe_task)
            transcribe_task.add_done_callback(self._background_tasks.remove)

        if self._stt_is_online and not is_online:
            logger.info("下播了")

        self._stt_is_online = is_online


    async def transcribe(self) -> None:

        logger = logging.getLogger(f"({self.dd_name})transcribe")

        while self._stt_is_online:

            info = await self.live_room.get_room_play_url()
            durl = info.get("durl", [])

            if durl:
                url = durl[0].get("url", "")
            else:
                logger.warning(f"no durl in:\n{json.dumps(info, indent=2)}")
                return

            if not url:
                logger.warning(f"no url in:\n{json.dumps(durl[0], indent=2)}")
                return
            else:
                logger.debug(f"get stream url: {url}")


            stream = await ffmpeg_to_audio_bytes(url)

            try:
                while stream.returncode is None:
                    chunk = await stream.stdout.read(4096)
                    if not chunk or stream.returncode:
                        logger.warning("ffmpeg stream closed")
                        break

                    # logger.info(f"read {len(chunk)} bytes: {chunk}")
                    # logger.info(stream.returncode)
            finally:
                if stream.returncode is None:
                    try:
                        stream.terminate()
                        await stream.wait()
                    except Exception as e:
                        logger.warning(f"Error terminating ffmpeg: {e}")

            continue

            if not self._stt_fp:
                self._stt_fp = open(
                    os.path.join(
                        self._stt_output_dir,
                        f"{self.live_room.room_display_id}_{int(self._stt_live_time.timestamp())}.txt"
                    ),
                    "w", encoding="utf-8"
                )


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


    def stop(self) -> None:
        if self._stt_fp: self._stt_fp.close()


def sort_durl(durl: list[dict]) -> list[dict]:
    durl.sort(key=lambda v: v.get("order", 999999))
    return durl


async def ffmpeg_to_audio_bytes(url: str, codec: str="pcm_s16le", sample_rate: int=16000) -> asyncio.subprocess.Process:
    command = [
        "ffmpeg",
        "-loglevel", "quiet", "-hide_banner",
        "-i", url,
        "-vn",
        "-ac", "1", # mono
        "-ar", f"{sample_rate}",
        "-acodec", codec,
        "-f", "wav", "pipe:1"
    ]

    logger = logging.getLogger("ffmpeg_to_audio_bytes")
    logger.debug(" ".join(command))

    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE)

    return process