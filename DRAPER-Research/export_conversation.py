"""把 Codex JSONL 任务记录导出成可直接交给 ChatGPT 阅读的 Markdown。"""

import argparse
import json
from pathlib import Path
import re


def messages(lines):
    """保留用户和助手正文，去掉工具记录、内部指令和运行环境标签。"""
    result = []
    for line in lines:
        record = json.loads(line)
        payload = record.get("payload", {})
        role = payload.get("role")
        if record.get("type") != "response_item" or payload.get("type") != "message":
            continue
        if role not in {"user", "assistant"}:
            continue
        parts = [part.get("text", "") for part in payload.get("content", [])
                 if part.get("type") in {"input_text", "output_text"}]
        text = "\n".join(part for part in parts if part).strip()
        text = re.sub(r"<environment_context>.*?</environment_context>", "", text,
                      flags=re.DOTALL).strip()
        if text and (not result or (role, text) != (result[-1][1], result[-1][2])):
            result.append((record.get("timestamp", ""), role, text))
    return result


def render(entries):
    labels = {"user": "用户", "assistant": "助手"}
    sections = ["# DRAPER 项目对话记录", "",
                "本文件从本地 Codex 任务记录导出，只保留用户和助手可见正文。", ""]
    for timestamp, role, text in entries:
        sections.extend((f"## {labels[role]} · {timestamp}", "", text, ""))
    return "\n".join(sections)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    arguments = parser.parse_args()
    entries = messages(arguments.input.read_text().splitlines())
    arguments.output.write_text(render(entries))
    print(f"Exported {len(entries)} messages to {arguments.output}")


if __name__ == "__main__":
    main()
