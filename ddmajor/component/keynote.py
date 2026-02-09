import json
import logging

import bilibili_api as biliapi
import dashscope

from .DDMajorInterface import DDMajorInterface


class DDMajorKeynote(DDMajorInterface):

    async def send_comment(self, content: str, id: str | int) -> list[int]:

        if isinstance(id, str):
            try:
                video = biliapi.video.Video(id)
                id = await video.get_cid()
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
                    rpid = await self._send_comment(content=text, oid=id, root=rpid)
                    text = "接上条\n" + line
                    rpids.append(rpid)
                else:
                    text = text + line

            if text:
                rpids.append(await self._send_comment(content=text, oid=id, root=rpid))

        except Exception as e:
            self.logger.error(e)


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


    async def summarize(content: str, prompt: str, role: str = "") -> str:

        summation = ""
        messages  = []

        if role: messages.append({"role": "system", "content": role}) # "你是一个专业且幽默的直播切片区骨灰级观众，拒绝任何AI感十足的陈词滥调，擅长从长篇 SRT 语音识别文本中精准提取核心话题，并整理成高质量的、能抓住直播中精彩瞬间的评论。"
        messages.append({"role": "user", "content": prompt})
        messages.append({"role": "user", "content": content})

        responses = dashscope.Generation.call(
            api_key=self._keynote_llm["api_key"],
            model=self._keynote_llm.get("model", "qwen-plus"),
            enable_thinking=True,
            messages=messages,
            stream=True,
            result_format="message",
            incremental_output=True,
        )


        for response in responses:

            status_code = response.get("status_code", 200)

            if status_code != 200:
                self.logger.warning(f"[{status_code}] {response.get('message', 'no message')}")
                continue

            choices = response.get("output", {}).get("choices", [])
            if choices:
                choice = choices[0]
                message = choice.get("message", {})
                content_chunk = message.get("content", "")
                # reasoning_content = message.get("reasoning_content", "")

                if content_chunk: summation += content_chunk
                # if reasoning_content: print(reasoning_content, end="")

        self.logger.debug("got llm response:\n" + summation)
        self.logger.debug("token usage:\n" + json.dumps(response.get("usage", {}), indent=2))

        return summation


    async def _init_async(self, **kwargs) -> None:

        await super()._init_async(**kwargs)
        dashscope.common.logging.logger.setLevel(logging.INFO)

        task = self.config.get("task")
        cmpt = task.get("components", [])

        flag_enable_keynote = False

        for k in range(len(cmpt)):
            component = cmpt[k]
            if component.get("type", "") == "keynote":
                flag_enable_keynote = True
                break


        if flag_enable_keynote:

            self._keynote_llm  = self.config.get("dashscope", {}).get("llm", {})
            self._keynote_conf = component
            self._keynote_user = biliapi.user.User(int(task.get("user_id")), self.bili_cred)
            self._keynote_room = task.get("room_id")


            if "api_key" not in self._keynote_llm:
                raise ValueError("api_key not configured in dashscope -> llm -> api_key")


            self.logger.info("enable keynote component")


            # self.scheduler.add_job(
            #     self._check_online,
            #     "interval",
            #     seconds=int(task.get("interval", 60)),
            #     id=f"check_online({self.dd_name})",
            #     replace_existing=True,
            # )

