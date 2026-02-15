import asyncio
import json
import logging
import re

from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp
import bilibili_api as biliapi
import dashscope

from .live_asr import timedelta_to_srt
from .DDMajorInterface import DDMajorInterface


class ComponentKeynote(DDMajorInterface):

    async def get_latest_replay(self) -> biliapi.video.Video | None:
        replay = None
        replay_series = None

        channels = await self._keynote_user.get_channel_list()
        series_list = channels.get("items_lists", {}).get("series_list", [])

        for series in series_list:
            if series.get("meta", {}).get("name", "") == "直播回放":
                replay_series = series
                break

        archives = replay_series.get("archives", []) # type: ignore

        if archives:
            first_archive = archives[0]
            replay = biliapi.video.Video(
                aid=first_archive["aid"] if "aid" in first_archive else first_archive["bvid"],
                credential=self.bili_cred,
            )

        return replay


    async def ai_subtitle_to_srt(self, video: biliapi.video.Video, view: dict | None) -> str: # type: ignore
        srt = ""

        sid   = 0
        count = 0
        delta = timedelta(seconds=0)

        if not view: view: dict = (await video.get_detail()).get("View", {})

        for page in view.get("pages", []):
            cid = page["cid"]
            sub = await video.get_subtitle(cid=int(cid))

            subtitle = None

            for subtitle in sub.get("subtitles"): # type: ignore
                if subtitle.get("lan", "").startswith("ai"):
                    break

            if subtitle:
                sub_url = "https:" + subtitle.get("subtitle_url", "")
                if sub_url:
                    self.logger.info(f"get subtitle url for cid {cid}: {sub_url}")

                    async with aiohttp.ClientSession() as session:
                        resp = await session.get(f"{sub_url}")
                        sub_json = await resp.json()

                    for body in sub_json.get("body", []):
                        try:
                            sid = int(body["sid"])
                            tfrom = float(body["from"])
                            tto = float(body["to"])
                            content = body["content"]

                            t_from = delta + timedelta(seconds=tfrom)
                            t_to   = delta + timedelta(seconds=tto)

                            srt += (
                                f"{count + sid}\n"
                                f"{timedelta_to_srt(t_from)} --> {timedelta_to_srt(t_to)}\n"
                                f"{content}\n\n"
                            )
                        except Exception:
                            self.logger.exception("failed to get subtitle body")

            count = sid
            delta = timedelta(seconds=page.get("duration", 7200))

        return srt


    async def _cron_check_replay(self) -> None:

        try:

            replay = await self.get_latest_replay()
            detail = (await replay.get_detail()).get("View", {}) # title, ctime, owner # type: ignore

            title = detail.get("title", "")
            match = re.search(r"(\d+)年(\d+)月(\d+)日(\d+)点", title)

            if match:
                year, month, day, hour = match.groups()
                live_date = datetime(
                    int(year), int(month), int(day), int(hour),
                    tzinfo=ZoneInfo("Asia/Shanghai")
                )
                # self.logger.info(f"最新回放：{live_date}")

                srt_file = find_transcription(
                    self._keynote_conf["search_dir"],
                    self._keynote_room,
                    live_date
                )

                ai_subtitle = ""

                if srt_file:
                    if ".finish" in srt_file:
                        self.logger.debug(f"find finished {srt_file}, skip")
                        return

                    self.logger.info(f"find {srt_file} to match {title}")

                    with open(srt_file, "r", encoding="utf-8") as f:
                        ai_subtitle = f.read()

                    srt_path = Path(srt_file)
                    with open(str(srt_path.with_suffix("").resolve()) + ".finish" + srt_path.suffix, "w") as _:
                        pass

                if not ai_subtitle:
                    self.logger.debug("find no transcription, try to use ai subtitle from bilibili")
                    ai_subtitle = await self.ai_subtitle_to_srt(replay, detail) # type: ignore

                    if ai_subtitle:

                        # save to .srt file
                        with open(
                            Path(self._keynote_conf["search_dir"]).joinpath(
                                f"{self._keynote_room}_{int(live_date.timestamp())}.srt"
                            ), "w", encoding="utf-8",
                        ) as f: print(ai_subtitle, file=f, end="")

                    else:
                        self.logger.debug("failed to get ai subtitle")
                        return

                ai_subtitle = compress_srt(ai_subtitle) # srt format consumes too many tokens and causes dilution
                self.logger.debug("compress subtitle to:\n" + ai_subtitle)

                if (comment := await self.prepare_comment(ai_subtitle, replay)): # type: ignore
                    self.logger.info(f"prepare to send comment:\n{comment}")
                    await self.send_comment(comment, replay.get_aid()) # type: ignore

                    # save a finish file
                    with open(
                        Path(self._keynote_conf["search_dir"]).joinpath(
                            f"{self._keynote_room}_{int(live_date.timestamp())}.finish.txt"
                        ), "w", encoding="utf-8",
                    ) as f: print(comment, file=f)

            else:
                self.logger.warning(f"time not find in title '{title}'")

        except Exception as e:
            self.logger.error(e)


    async def prepare_comment(self, subtitle: str, video: biliapi.video.Video, view: dict | None = None) -> str:
        comment = ""

        if await self.video_is_commented(video):
            self.logger.debug("this video is already commented")
        else:

            if not view: view = (await video.get_detail()).get("View", {})
            pages = view.get("pages", []) # type: ignore

            role = self._keynote_conf.get("role", "你是一个专业且幽默的直播切片区骨灰级观众，拒绝任何AI感十足的陈词滥调，擅长从长篇语音识别文本中精准提取核心话题，并整理成高质量的、能抓住直播中精彩瞬间的评论。")
            prompt = self._keynote_conf["prompt"]

            if (extra_info := self._keynote_conf.get("extra_info", "")):
                prompt = prompt + "\n\n# Extra Information\n" + extra_info

            llm_resp = await self.summarize(subtitle, prompt, role)

            page = {}
            p_number = 0
            p_seconds = timedelta(microseconds=-1)
            total_shift = timedelta(0)

            for line in llm_resp.splitlines(keepends=True):
                if re.match(r"^\d+:\d+.*? ", line):
                    # start with time
                    tstr, content = line.lstrip().split(" ", maxsplit=1)

                    delta = srt_like_str_to_delta(tstr) - total_shift

                    if delta > p_seconds:
                        p_number += 1

                        page = {} if p_number > len(pages) else pages[p_number-1]
                        total_shift += p_seconds # sum of previous ps
                        p_seconds += timedelta(seconds=page.get("duration", 7200)) # current p time

                        comment += f"\nP{p_number}\n"
                        delta = srt_like_str_to_delta(tstr) - total_shift

                    seconds = delta.total_seconds()
                    minutes = seconds // 60
                    seconds -= minutes * 60

                    comment += f"{int(minutes):02d}:{int(seconds):02d} {content}"

                else:
                    comment += line

        return comment


    async def video_is_commented(self, video: biliapi.video.Video) -> bool:
        page = 1
        offset = ""
        result = False

        myid = str(self.bili_cred.dedeuserid)

        while page <= 3:

            comments = await biliapi.comment.get_comments_lazy(
                oid=video.get_aid(),
                type_=biliapi.comment.CommentResourceType.VIDEO,
                offset=offset, credential=self.bili_cred,
            )

            offset  = comments.get("cursor", {}).get("pagination_reply", {}).get("next_offset", "")
            replies = comments.get("replies", [])

            for reply in replies:
                rmid = str(reply.get("member", {}).get("mid", "-1"))
                if rmid == myid:
                    result = True
                    break

            page += 1
            if not offset or not replies: break

        return result


    async def send_comment(self, content: str, id: str | int) -> list[int]:

        if isinstance(id, str):
            try:
                video = biliapi.video.Video(id)
                id = video.get_aid()
            except Exception as e:
                pass

        rpids = [] # reply id list

        lines = content.strip().splitlines(keepends=True)
        line  = ""
        text  = ""
        rpid  = None

        try:
            while lines:
                line = lines.pop(0)
                if len(text+line) >= 1000:
                    rpid = await self._send_comment(content=text, oid=int(id), root=rpid)
                    await asyncio.sleep(15)
                    self.logger.debug(f"sleep 15s after sending: {text}")

                    text = "接上条\n" + line
                    rpids.append(rpid)
                else:
                    text = text + line

            if text:
                rpids.append(await self._send_comment(content=text, oid=int(id), root=rpid))
                self.logger.debug(f"sent: {text}")

            self.logger.info(f"finish sending comment: {rpids}")

        except Exception:
            self.logger.exception("failed to send comment")


        return rpids


    async def _send_comment(
        self, content: str, oid: int, root: int | None = None, parent: int | None = None,
        type_: biliapi.comment.CommentResourceType = biliapi.comment.CommentResourceType.VIDEO
    ) -> int:

        rpid = -1 # if failed

        try:
            resp = await biliapi.comment.send_comment(
                text=content, oid=oid, type_=type_,
                root=root, parent=parent, pic=None,
                credential=self.bili_cred,
            )
            rpid = resp.get("rpid", -1)
        except Exception as e:
            self.logger.error(e)
        else:
            pass

        return rpid


    async def summarize(self, content: str, prompt: str, role: str = "") -> str:

        summation = ""
        messages  = []

        if role: messages.append({"role": "system", "content": role})

        messages.append({"role": "user", "content": prompt})

        self.logger.debug(messages)

        messages.append({"role": "user", "content": content})
        messages.append({"role": "user", "content": prompt}) # repeat in case the context is too long


        responses = await dashscope.AioGeneration.call(
            api_key=self._keynote_llm["api_key"],
            model=self._keynote_llm.get("model", "qwen-plus"),
            enable_thinking=True,
            messages=messages,
            stream=True,
            result_format="message",
            incremental_output=True,
            **self._keynote_conf.get("llm_params", {}),
        )

        response = {}

        async for response in responses: # type: ignore

            status_code = response.get("status_code", 200)

            if status_code != 200:
                self.logger.warning(f"[{status_code}] {response.get('message', 'no message')}")
                continue

            choices = response.get("output", {}).get("choices", []) # type: ignore
            if choices:
                choice = choices[0]
                message = choice.get("message", {})
                content_chunk = message.get("content", "")

                if content_chunk: summation += content_chunk

                # # debug only
                # reasoning_content = message.get("reasoning_content", "")
                # if reasoning_content: print(reasoning_content, end="")

        self.logger.debug("got llm response:\n" + summation)
        self.logger.debug("token usage:\n" + json.dumps(response.get("usage", {}), indent=2))

        return summation


    async def _init_async(self, **kwargs) -> None:

        await super()._init_async(**kwargs)
        dashscope.common.logging.logger.setLevel(logging.INFO) # type: ignore

        task = self.config.get("task")
        cmpt = task.get("components", []) # type: ignore

        flag_enable_keynote = False

        for k in range(len(cmpt)):
            component = cmpt[k]
            if component.get("type", "") == "keynote":
                flag_enable_keynote = True
                break


        if flag_enable_keynote:

            self._keynote_llm  = self.config.get("dashscope", {}).get("llm", {})
            self._keynote_conf = component # type: ignore
            self._keynote_user = biliapi.user.User(int(task.get("user_id")), self.bili_cred) # type: ignore
            self._keynote_room = task.get("room_id") # type: ignore


            if "api_key" not in self._keynote_llm:
                raise ValueError("api_key not configured in dashscope -> llm -> api_key")


            self.logger.info("enable keynote component")


            self.scheduler.add_job(
                self._cron_check_replay,
                "interval",
                seconds=int(self._keynote_conf.get("interval", 60)),
                id=f"cron_check_replay({self.dd_name})",
                replace_existing=True,
            )


            try:
                await self._cron_check_replay()
            except Exception:
                self.logger.exception(f"initial cron check replay failed")


    async def send_note(self) -> None:
        # POST http://api.bilibili.com/x/note/add
        # application/x-www-form-urlencoded
        # Cookie (SESSDATA)
        # oid	num	目标id	必要
        # oid_type	num	目标id类型	必要	0视频(oid=avid)
        # note_id	num	笔记id	非必要	创建时无需此项
        # title	str	笔记标题	必要
        # summary	str	笔记预览文本	必要
        # content	str	笔记正文json序列	必要	格式见附表
        # csrf	str	CSRF Token（位于cookie）	必要
        # tags	str	笔记跳转标签列表	非必要
        # cls	num	1	非必要	作用尚不明确
        # from	str	提交类型	非必要	auto自动提交
        #                            save手动提交
        #                            close关闭时自动提交
        # cont_len	num	正文字数	非必要
        # platform	str	平台	非必要	可为web
        # publish	num	是否公开笔记	非必要	0不公开 1公开
        # auto_comment	num	是否添加到评论区	非必要	0不添加 1添加

        raise RuntimeError("not implemented")


def find_transcription(path: str, room_id: int | str, start_date: datetime) -> str:
    transcription = ""

    room_id = str(room_id)
    files = [f for f in Path(path).iterdir() if f.is_file()]

    for file in files:
        if room_id in file.stem:
            _, ts_str = file.stem.split("_")

            try:

                ts_str  = ts_str.removesuffix(".finish") # ensure not .finish
                ts_time = datetime.fromtimestamp(int(ts_str), tz=ZoneInfo("Asia/Shanghai"))

                delta = abs(ts_time - start_date)
                if delta <= timedelta(hours=1):
                    finish_ts = file.parent.resolve().joinpath(Path(f"{file.stem}.finish{file.suffix}"))

                    if finish_ts.exists():
                        # file is not .finish, but file.finish exists
                        file = finish_ts

                    transcription = file.resolve()
                    break

            except Exception:
                pass

    return str(transcription)


def srt_like_str_to_delta(tstr: str) -> timedelta:
    ts = tstr.split(",", maxsplit=1)

    delta = timedelta(0)

    if len(ts) >= 2:
        try:
            ms = timedelta(milliseconds=float(ts[1]))
            delta += ms
        except Exception:
            pass

    tstr = ts[0]
    tstr = tstr.replace("：", ":") # use english :
    ts = tstr.split(":")

    count = 0
    for t in ts:
        it = 0
        try:
            it = int(t)
        except Exception:
            pass

        count = count * 60 + it

    delta += timedelta(seconds=count)

    return delta


def compress_srt(srt: str) -> str:
    lines = srt.strip().splitlines()
    output = []

    i = 0
    while i < len(lines):
        line = lines[i].strip()

        if line.isdigit():
            if i + 1 < len(lines) and "-->" in lines[i+1]:

                time_line = lines[i+1].strip()
                start_time_str = time_line.split("-->")[0].strip()

                try:
                    delta = srt_like_str_to_delta(start_time_str)
                    total_seconds = int(delta.total_seconds())
                    minutes = total_seconds // 60
                    seconds = total_seconds % 60
                    timestamp = f"{minutes:02d}:{seconds:02d}"
                except Exception:
                    timestamp = "00:00"

                content_lines = []
                j = i + 2
                while j < len(lines):
                    content_line = lines[j].strip()
                    if not content_line:
                        break
                    content_lines.append(content_line)
                    j += 1

                content = " ".join(content_lines)
                if content:
                    output.append(f"{timestamp} {content}")

                i = j
                continue

        i += 1

    return "\n".join(output)
