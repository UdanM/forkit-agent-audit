import io
import json
import unittest
from unittest.mock import patch

from local_ask import ask_local


class LocalAskTests(unittest.TestCase):
    def test_model_citations_are_checked_against_available_evidence(self):
        payload = {"question": "Who edited this?", "evidence": ["Actor unknown."]}
        with patch("local_ask.build_opener") as opener:
            def respond(answer):
                content = json.dumps({"message": {"content": json.dumps(answer)}}).encode()
                opener.return_value.open.return_value = io.BytesIO(content)
                return ask_local("fixture", payload)
            self.assertEqual(respond({"answer": "The recorded actor is unknown.", "citations": [1, 1]}),
                             {"answer": "The recorded actor is unknown.", "citations": [1]})
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url, "http://127.0.0.1:11434/api/chat")
            self.assertIsNone(respond({"answer": "Unverified claim.", "citations": [9]}))
