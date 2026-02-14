import argparse
import json

from dashscope.audio import asr
from ddmajor.component.live_asr import __ASR_MODEL__


def input_vocabulary(input_fn: str) -> list[dict]:
    vocabularies = []

    if input_fn:
        with open(input_fn, "r", encoding="utf-8") as f:
            vocabularies = json.load(f)
        return vocabularies

    while True:
        text = input("请输入单词，留空结束：").strip()
        if not text: break

        weight = input("请输入权重（1-5），默认4：").strip()

        try:
            weight = int(weight)
            if weight < 1 or weight > 5:
                print("权重必须在1-5之间，已重置为默认值4")
                weight = 4
        except (ValueError, Exception):
            weight = 4

        vocabularies.append({
            "text": text,
            "weight": weight
        })

    return vocabularies


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", "-c", type=str, help="path to config file", required=True)
    parser.add_argument("--action", "-a", type=str, choices=["create", "edit", "delete", "list", "query"], help="action to perform")
    parser.add_argument("--vocabulary", "-v", type=str, default="", help="vocabulary in json format, e.g. '[{\"text\": \"粉丝牌\", \"weight\": 4}]'")

    args = parser.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        config: dict = json.load(f)

    dashscope_config: dict = config.get("dashscope", {}).get("asr", {})
    dashscope_config.setdefault("base_websocket_api_url", "wss://dashscope.aliyuncs.com/api-ws/v1/inference")

    service = asr.VocabularyService(
        api_key=dashscope_config["api_key"],
        base_websocket_api_url=dashscope_config["base_websocket_api_url"]
    )

    match args.action:
        case "create":
            prefix = input("请输入词表前缀）：").strip()
            vocabularies = input_vocabulary(args.vocabulary)
            vocabulary_id = service.create_vocabulary(
                target_model=__ASR_MODEL__,
                prefix=prefix,
                vocabulary=vocabularies
            )
            print(f"已创建词表，vocabulary_id为：{vocabulary_id}")
        case "edit":
            vocabulary_id = input("请输入要编辑的vocabulary_id：").strip()
            vocabularies = input_vocabulary(args.vocabulary)
            service.update_vocabulary(vocabulary_id=vocabulary_id, vocabulary=vocabularies)
            print("已发送更新请求，请通过list命令确认更新结果")
        case "delete":
            vocabulary_id = input("请输入要删除的vocabulary_id：").strip()
            service.delete_vocabulary(vocabulary_id=vocabulary_id)
            print("已发送删除请求，请通过list命令确认删除结果")
        case "list":
            vocabularies = service.list_vocabularies()
            print(json.dumps(vocabularies, indent=2, ensure_ascii=False))
        case "query":
            vocabulary_id = input("请输入要查询的vocabulary_id：").strip()
            vocabulary = service.query_vocabulary(vocabulary_id=vocabulary_id)
            print(json.dumps(vocabulary, indent=2, ensure_ascii=False))

        case _:
            print("不认识的操作")