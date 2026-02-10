import argparse
import json
import time

import ddmajor


def main():
    parser = argparse.ArgumentParser()
    # parser.add_argument("--room", "-r", type=int, help="live room id", required=True)
    parser.add_argument("--config", "-c", type=str, help="config json file", required=True)
    parser.add_argument("--level", type=str.lower, choices=ddmajor.logging.choices, default="info", help="log level")

    args: argparse.Namespace = parser.parse_args()

    ddmajor.logging.set_level(args.level)
    logger = ddmajor.logging.logger

    try:
        with open(args.config, "r", encoding="utf-8") as f:
            config: dict = json.load(f)
    except json.JSONDecodeError as e:
        logger.critical(f'❌ 配置文件"{args.config}"解析失败：{e}')
        exit(1)
    except Exception as e:
        logger.critical(f"❌ 解析配置文件时出错: {e}")
        exit(1)

    single_config = config.copy()

    try:
        ddmajor.credential.init_credential(config["bili_credential"])
        ddmajor.credential.check_and_rotate_credential(config, args.config)

        tasks = single_config.pop("tasks")

        for k in range(len(tasks)):
            single_config.update({"task": tasks[k]})
            dd = ddmajor.DDMajor(single_config)
            dd.run()

        while True:
            time.sleep(1800)
            ddmajor.credential.check_and_rotate_credential(config, args.config)

    except KeyboardInterrupt:
        logger.info("退出程序")
    except Exception:
        logger.exception("运行时发生错误")


if __name__ == "__main__":
    main()
