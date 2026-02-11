import logging
import asyncio
import logging
import os
import json

from datetime import datetime, timedelta
from typing import Callable

import dashscope
import bilibili_api as biliapi

from dashscope.audio import asr

from .DDMajorInterface import DDMajorInterface


__SAMPLE_RATE__ = 16000


class DDMajorASR(DDMajorInterface):

    async def _check_online(self) -> None:

        info = await self.live_room.get_room_play_info()
        is_online = (info.get("live_status", -1) == 1)

        if not self._asr_is_online and is_online:
            live_time = info.get("live_time")
            live_time = datetime.now() if not live_time else datetime.fromtimestamp(live_time)
            self.logger.info(f"于{live_time.strftime('%H:%M:%S')}开始直播了")

            self._asr_sentence_id = 0
            self._asr_srt_content = ""
            self._asr_live_time   = live_time
            self._asr_time_delta  = datetime.now() - live_time

            if self._asr_fp: self._asr_fp.close()
            self._asr_fp = open(
                os.path.join(
                    self._asr_output_dir,
                    f"{self.live_room.room_display_id}_{int(self._asr_live_time.timestamp())}.txt"
                ),
                "a", encoding="utf-8"
            )

            transcribe_task = self._event_loop.create_task(self.transcribe())
            self._background_tasks.append(transcribe_task)
            transcribe_task.add_done_callback(self._background_tasks.remove)

        if self._asr_is_online and not is_online:
            self.logger.info("下播了")
            if self._asr_fp: self._asr_fp.close()
            self._asr_fp = None

        self._asr_is_online = is_online


    async def get_stream_url(self) -> str:
        url = ""

        try:
            info = await self.live_room.get_room_play_url()
            durl = info.get("durl", [])

            if durl:
                urls = [v["url"] for v in durl if v.get("url")]

                self.logger.debug(f"got:\n{'\n'.join(urls)}")

                for k in reversed(range(len(urls))):
                    if "d1--ov-gotcha05.bilivideo.com" in urls[k]: # 403 Forbidden
                        urls.pop(k)

                for url in urls:
                    # TODO: optimize url select
                    if "gotcha04.bilivideo.com" in url: break # prefer cn-gotcha04

                self.logger.debug(f"select: {url}")
            else:
                self.logger.warning(f"no durl in:\n{json.dumps(info, indent=2)}")

        except Exception as e:
            self.logger.error(f"failed: {e}")

        return url


    def get_transcribe_callback(self) -> Callable:

        async def _transcribe_callback(result: asr.RecognitionResult) -> None:
            sentence = result.get_sentence()

            if "text" in sentence:
                content = sentence.get("text").strip() # type: ignore
                if not content: return

                if asr.RecognitionResult.is_sentence_end(sentence): # type: ignore
                    self._asr_sentence_id += 1
                    srt_begin = self._asr_time_delta + timedelta(milliseconds=sentence.get("begin_time", 0)) # type: ignore
                    srt_end = self._asr_time_delta + timedelta(milliseconds=sentence.get("end_time", 1000)) # type: ignore
                    srt_record = (
                        f"{self._asr_sentence_id}\n" +
                        f"{timedelta_to_srt(srt_begin)} --> {timedelta_to_srt(srt_end)}\n" +
                        f"{content}\n"
                    )

                    self.logger.debug("write srt:\n" + srt_record)
                    self._asr_srt_content += srt_record + "\n"

                    try:
                        print(srt_record, file=self._asr_fp, flush=True)
                    except Exception as e:
                        self.logger.error(f"failed to write: {e}")

                else:
                    self.logger.debug(content)

        return _transcribe_callback


    async def transcribe(self) -> None:

        recognition = None

        while self._asr_is_online:

            try:

                url = await self.get_stream_url()
                stream = await ffmpeg_to_audio_bytes(url)


                update_time_delta_task = self._event_loop.create_task(self._update_time_delta(url))
                self._background_tasks.append(update_time_delta_task)
                update_time_delta_task.add_done_callback(self._background_tasks.remove)


                asr_config = self.config.get("dashscope", {}).get("asr", {})

                callback = ASRCallback(
                    name=self.dd_name,
                    event_loop=self._event_loop,
                    callback=self.get_transcribe_callback(),
                )

                recognition = asr.Recognition(
                    api_key=asr_config["api_key"],
                    model="fun-asr-realtime",
                    format="wav",
                    sample_rate=__SAMPLE_RATE__,
                    callback=callback,
                    base_websocket_api_url=asr_config.get(
                        "base_websocket_api_url",
                        "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
                    ),
                    heartbeat=True,
                    **self._asr_config.get("asr_params", {}),
                )

                recognition.start()

                try:
                    while stream.returncode is None:
                        chunk = await stream.stdout.read(4096) # type: ignore
                        if not chunk or stream.returncode:
                            self.logger.warning("ffmpeg stream closed")
                            break
                        else:
                            recognition.send_audio_frame(chunk)

                except Exception as e:
                    self.logger.warning(f"send audio stream: {e}")

                try:
                    recognition.stop()
                except Exception as _:
                    pass

                if stream.returncode is None:
                    stream.kill()

            except Exception as e:
                self.logger.error(f"exception during transcription: {e}")
            finally:
                pass

            await asyncio.sleep(5)


    async def _update_time_delta(self, url: str) -> None:

        info = {}

        try:
            info = await ffprobe_mediainfo(url)
            self.logger.debug(json.dumps(info, indent=2))

            streams = info.get("streams", [])

            if not streams:
                self.logger.warning("no stream found")
            else:
                stream = streams[0]
                start_pts = float(stream.get("start_pts"))

                time_delta = timedelta(milliseconds=start_pts)

                if abs(self._asr_time_delta - time_delta) > timedelta(minutes=1):
                    self.logger.info(f"new delta {time_delta} may be inaccurate, keep original delta {self._asr_time_delta}")
                else:
                    self.logger.info(f"set delta {time_delta}")
                    self._asr_time_delta = time_delta
        except Exception as e:
            self.logger.error(f"{e}, raw mediainfo:\n{json.dumps(info, indent=2)}")


    async def _init_async(self, **kwargs) -> None:

        await super()._init_async(**kwargs)
        dashscope.common.logging.logger.setLevel(logging.INFO)  # type: ignore

        task: dict = self.config.get("task", {})
        cmpt: list = task.get("components", [])

        component = {}
        flag_enable_asr = False

        for k in range(len(cmpt)):
            component = cmpt[k]
            if component.get("type", "") == "live_asr":
                flag_enable_asr = True
                break

        if flag_enable_asr:
            self.logger.info("enable speech to text component")

            self._asr_config = component
            self._asr_output_dir = component["output_dir"] # type: ignore

            self.live_room = biliapi.live.LiveRoom(
                room_display_id=task.get("room_id"), # type: ignore
                credential=self.bili_cred,
            )

            # self.live_danmaku = biliapi.live.LiveDanmaku(
            #     room_display_id=task.get("room_id"),
            #     credential=self.bili_cred,
            # )
            # self._danmaku_task = asyncio.create_task(self.live_danmaku.connect())

            self._asr_fp = None
            self._asr_is_online = False
            self._asr_live_time = datetime.now()

            self.scheduler.add_job(
                self._check_online,
                "interval",
                seconds=int(task.get("interval", 60)),
                id=f"check_online({self.dd_name})",
                replace_existing=True,
            )

            await self._check_online()


    def stop(self) -> None:
        if self._asr_fp: self._asr_fp.close()


def sort_durl(durl: list[dict]) -> list[dict]:
    durl.sort(key=lambda v: v.get("order", 999999))
    return durl


async def ffmpeg_to_audio_bytes(url: str, codec: str="pcm_s16le", sample_rate: int=__SAMPLE_RATE__) -> asyncio.subprocess.Process:
    command = [
        "ffmpeg",
        "-loglevel", "quiet", "-hide_banner",
        "-i", url,
        "-vn",
        "-ac", "1", # mono
        "-ar", f"{sample_rate}",
        "-acodec", codec,
        "-f", "wav", "pipe:1",
    ]

    logger = logging.getLogger("ffmpeg_to_audio_bytes")
    logger.debug(" ".join(command))

    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    return process


async def ffprobe_mediainfo(url: str) -> dict:
    logger = logging.getLogger("ffprobe_mediainfo")

    command = [
        "ffprobe",
        "-v", "quiet",
        "-show_streams", "-show_entries",
        "format_tags", "-of", "json",
        url,
    ]

    logger.debug(" ".join(command))

    process = await asyncio.create_subprocess_exec(*command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await process.communicate()

    if process.returncode != 0:
        logger.error(stderr.decode())
        return {}
    else:
        return json.loads(stdout.decode())


class ASRCallback(asr.RecognitionCallback):

    def __init__(self, name: str, event_loop: asyncio.AbstractEventLoop, callback: Callable) -> None:
        self.logger = logging.getLogger(f"({name})asr_callback")
        self.event_loop = event_loop
        self.callback = callback

    def on_complete(self) -> None:
        self.logger.info("recognition complete")

    def on_error(self, result: asr.RecognitionResult) -> None:
        raise RuntimeError(result.request_id, result.message)

    def on_event(self, result: asr.RecognitionResult) -> None:
        asyncio.run_coroutine_threadsafe(
            self.callback(result),
            self.event_loop,
        )


def timedelta_to_srt(td: timedelta):
    # Get total seconds as a float
    total_seconds = td.total_seconds()

    # Calculate components
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    milliseconds = int(round((total_seconds - int(total_seconds)) * 1000))

    # Handle overflow from rounding (e.g., 999.5ms becoming 1000ms)
    if milliseconds == 1000:
        seconds += 1
        milliseconds = 0
        # You could continue this logic for minutes/hours if needed

    return f"{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}"