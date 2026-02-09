import logging


choices = ["info", "warning", "debug"]
logger = logging.getLogger("ddmajor")

handler = logging.StreamHandler()
handler.setFormatter(
    logging.Formatter(
        fmt="[%(levelname)s][%(asctime)s] %(name)s - %(module)s - %(funcName)s L%(lineno)d: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
)

logger.addHandler(handler)


def set_level(level: str | int) -> None:
    logger.setLevel(logging.getLevelName(level.upper()) if isinstance(level, str) else level)

logger.set_level = set_level
