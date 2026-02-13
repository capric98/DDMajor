import json

import bilibili_api as biliapi

from ddmajor.logging import logger
from ddmajor import DDMajor


bili_cred = biliapi.Credential()


def init_credential(credential: dict) -> None:
    global bili_cred
    bili_cred = biliapi.Credential(**credential)


def get_credential() -> biliapi.Credential:
    return bili_cred


def check_and_rotate_credential(config: dict, fn: str, dd_list: list[DDMajor]=[]) -> None:

    global bili_cred

    try:
        if biliapi.sync(bili_cred.check_refresh()):
            logger.info("crendential expired, refreshing...")
            biliapi.sync(bili_cred.refresh())

            cookies = bili_cred.get_cookies()
            for key in ["SESSDATA", "DedeUserID"]:
                if key in cookies: cookies.pop(key)
            for k, v in cookies.items():
                if not v: cookies.pop(k)

            # in case the config file is edited after the program is launched
            with open(fn, "r", encoding="utf-8") as f:
                new_config = json.load(f)

            new_config.update({"bili_credential": cookies})

            with open(fn, "w", encoding="utf-8") as f:
                json.dump(new_config, f, indent=4, sort_keys=False)

            for dd in dd_list:
                dd._event_loop.call_soon_threadsafe(dd.update_cred, bili_cred)

    except Exception:
        logger.exception("failed to refresh credential")