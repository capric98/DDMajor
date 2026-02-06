import argparse
import json
import logging

import ddmajor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--room", "-r", type=int, help="live room id", required=True)
    parser.add_argument("--config", "-c", type=str, help="config json file", required=True)
    parser.add_argument("--log-level", type=str.upper, choices=["info", "warning", "debug"], default="info", help="log level")

    args: argparse.Namespace = parser.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="[%(levelname)s][%(asctime)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )
    logger = logging.getLogger("main")

    try:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        print(f'❌ 配置文件"{args.config}"解析失败：{e}')
        exit(1)
    except Exception as e:
        print(f"❌ 解析配置文件时出错: {e}")
        exit(1)


    try:
        dd = ddmajor.DDMajor(config)
        dd.run(block=True)
    except KeyboardInterrupt:
        logger.info("退出程序")
        dd.stop()
    except Exception as e:
        print(f"运行时出现错误：{e}")



if __name__ == "__main__":
    main()
