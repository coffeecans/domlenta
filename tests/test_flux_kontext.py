from __future__ import annotations

import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

if "requests" not in sys.modules:
    requests_stub = types.ModuleType("requests")
    requests_stub.request = lambda *args, **kwargs: None
    requests_stub.Response = object
    sys.modules["requests"] = requests_stub

from dom_lenta_photo_app.services import flux_kontext


class FakeResponse:
    def __init__(self, status_code=200, payload=None, content=b"image", headers=None):
        self.status_code = status_code
        self._payload = payload
        self.content = content
        self.headers = headers or {"Content-Type": "image/png"}

    def json(self):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class FluxKontextTests(unittest.TestCase):
    def test_build_prompt_preserves_user_prompt(self):
        prompt = flux_kontext.build_kontext_prompt("товар в саду")
        self.assertIn("товар в саду", prompt)
        self.assertIn("Preserve the product as closely as possible", prompt)
        self.assertIn("do not replace it with a similar product", prompt)

    @patch.dict(os.environ, {}, clear=True)
    def test_missing_api_key_raises_clear_error(self):
        with tempfile.NamedTemporaryFile(suffix=".png") as image:
            image.write(b"png")
            image.flush()
            with self.assertRaisesRegex(flux_kontext.FluxKontextError, "Не настроен ключ Black Forest Labs"):
                flux_kontext.submit_kontext_request(Path(image.name), "сцена")

    @patch.dict(os.environ, {"BFL_API_KEY": "test-key"})
    @patch("dom_lenta_photo_app.services.flux_kontext.time.sleep", return_value=None)
    @patch("dom_lenta_photo_app.services.flux_kontext.requests.request")
    def test_generate_context_scene_success(self, request_mock, _sleep_mock):
        request_mock.side_effect = [
            FakeResponse(payload={"id": "task-1", "polling_url": "https://poll.example/task-1"}),
            FakeResponse(payload={"status": "Ready", "result": {"sample": "https://cdn.example/out.png"}}),
            FakeResponse(content=b"png-result", headers={"Content-Type": "image/png"}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.png"
            output_path = Path(tmp) / "output.png"
            input_path.write_bytes(b"png-input")

            flux_kontext.generate_context_scene(input_path, output_path, "товар в гостиной")

            self.assertEqual(output_path.read_bytes(), b"png-result")
            post_payload = request_mock.call_args_list[0].kwargs["json"]
            self.assertEqual(post_payload["output_format"], "png")
            self.assertNotIn("data:image", post_payload["input_image"])

    @patch.dict(os.environ, {"BFL_API_KEY": "test-key"})
    @patch("dom_lenta_photo_app.services.flux_kontext.requests.request")
    def test_failed_status_raises(self, request_mock):
        request_mock.return_value = FakeResponse(payload={"status": "Failed"})
        with self.assertRaisesRegex(flux_kontext.FluxKontextError, "Failed"):
            flux_kontext.poll_kontext_result("https://poll.example/task-1")

    @patch.dict(os.environ, {"BFL_API_KEY": "test-key"})
    @patch("dom_lenta_photo_app.services.flux_kontext.time.sleep", return_value=None)
    @patch("dom_lenta_photo_app.services.flux_kontext.requests.request")
    def test_pending_timeout_raises(self, request_mock, _sleep_mock):
        request_mock.return_value = FakeResponse(payload={"status": "Pending"})
        with self.assertRaisesRegex(flux_kontext.FluxKontextError, "таймаут"):
            flux_kontext.poll_kontext_result("https://poll.example/task-1", timeout_seconds=0)

    @patch.dict(os.environ, {"BFL_API_KEY": "test-key"})
    @patch("dom_lenta_photo_app.services.flux_kontext.requests.request")
    def test_no_fake_ai2_created_when_api_download_fails(self, request_mock):
        request_mock.side_effect = [
            FakeResponse(payload={"id": "task-1", "polling_url": "https://poll.example/task-1"}),
            FakeResponse(payload={"status": "Ready", "result": {"sample": "https://cdn.example/out.png"}}),
            FakeResponse(status_code=500, payload={"error": "server"}),
            FakeResponse(status_code=500, payload={"error": "server"}),
            FakeResponse(status_code=500, payload={"error": "server"}),
            FakeResponse(status_code=500, payload={"error": "server"}),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            input_path = Path(tmp) / "input.png"
            output_path = Path(tmp) / "output.png"
            input_path.write_bytes(b"png-input")
            with self.assertRaises(flux_kontext.FluxKontextError):
                flux_kontext.generate_context_scene(input_path, output_path, "сцена")
            self.assertFalse(output_path.exists())


if __name__ == "__main__":
    unittest.main()
