import json
import unittest

from export_conversation import messages


class ConversationExportTest(unittest.TestCase):
    def test_keeps_visible_messages_only(self):
        records = [
            {"type": "response_item", "payload": {"type": "message", "role": "developer",
             "content": [{"type": "input_text", "text": "hidden"}]}},
            {"timestamp": "t1", "type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "<environment_context>x</environment_context>继续"}]}},
            {"timestamp": "t2", "type": "response_item", "payload": {"type": "message", "role": "assistant",
             "content": [{"type": "output_text", "text": "完成"}]}},
        ]
        actual = messages(json.dumps(record, ensure_ascii=False) for record in records)
        self.assertEqual(actual, [("t1", "user", "继续"), ("t2", "assistant", "完成")])


if __name__ == "__main__":
    unittest.main()
