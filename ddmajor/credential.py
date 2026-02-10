import json

import bilibili_api as biliapi

from ddmajor.logging import logger


bili_cred = biliapi.Credential()


def init_credential(credential: dict) -> None:
    global bili_cred
    bili_cred = biliapi.Credential(**credential)


def check_and_rotate_credential(config: dict, fn: str) -> None:

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

            config.update({"bili_credential": cookies})
            with open(fn, "w", encoding="utf-8") as f:
                json.dump(config, f, indent=4, sort_keys=False)

    except Exception:
        logger.exception("failed to refresh credential")