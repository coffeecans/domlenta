from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any

import requests

BFL_API_URL = "https://api.bfl.ai/v1/flux-kontext-pro"
REQUEST_TIMEOUT_SECONDS = 30
POLL_INTERVAL_SECONDS = 1
MAX_GENERATION_WAIT_SECONDS = 180
RETRY_DELAYS_SECONDS = (1, 2, 4)
TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}
TERMINAL_ERROR_STATUSES = {"Error", "Failed", "Request Moderated"}


class FluxKontextError(RuntimeError):
    """Raised when Black Forest Labs FLUX Kontext generation fails."""


PROMPT_TEMPLATE = """Edit the provided product image and create a realistic commercial product photography scene.

Place the exact product from the input image into the following environment:
{user_prompt}

Preserve the product as closely as possible:
- keep the same shape;
- keep the same proportions;
- keep the same colors;
- keep the same materials and textures;
- keep all visible details;
- keep labels, logos and branding unchanged;
- do not redesign the product;
- do not replace it with a similar product;
- do not add duplicate products;
- do not crop important parts of the product.

The product must remain the main subject of the image.
The product must appear naturally integrated into the environment.
Create realistic lighting, shadows, scale and perspective.
The result must look like professional e-commerce lifestyle photography.
Do not add text, captions, watermarks, frames or graphic elements.

Scene description:
{user_prompt}
"""


def build_kontext_prompt(user_prompt: str) -> str:
    return PROMPT_TEMPLATE.format(user_prompt=user_prompt.strip())


def get_bfl_api_key() -> str:
    return os.environ.get("BFL_API_KEY", "").strip()


def encode_image_to_base64(image_path: Path) -> str:
    return base64.b64encode(image_path.read_bytes()).decode("ascii")


def _json_or_error(response: requests.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise FluxKontextError("Black Forest Labs вернул некорректный JSON") from exc
    if not isinstance(payload, dict):
        raise FluxKontextError("Black Forest Labs вернул неожиданный формат ответа")
    return payload


def _request_with_retries(method: str, url: str, **kwargs: Any) -> requests.Response:
    for attempt, delay in enumerate((*RETRY_DELAYS_SECONDS, None), start=1):
        response = requests.request(method, url, timeout=REQUEST_TIMEOUT_SECONDS, **kwargs)
        if response.status_code not in TRANSIENT_STATUS_CODES:
            return response
        if delay is None:
            return response
        time.sleep(delay)
    return response


def _raise_for_bfl_error(response: requests.Response, action: str) -> None:
    if response.status_code in {401, 403}:
        raise FluxKontextError("Black Forest Labs отклонил API-ключ. Проверьте BFL_API_KEY")
    if response.status_code == 402:
        raise FluxKontextError("Недостаточно кредитов Black Forest Labs")
    if response.status_code == 429:
        raise FluxKontextError("Black Forest Labs временно ограничил запросы: HTTP 429")
    if 400 <= response.status_code < 500:
        raise FluxKontextError(f"Black Forest Labs отклонил запрос при этапе: {action}")
    if response.status_code >= 500:
        raise FluxKontextError(f"Black Forest Labs временно недоступен при этапе: {action}")


def submit_kontext_request(image_path: Path, prompt: str) -> tuple[str, str]:
    api_key = get_bfl_api_key()
    if not api_key:
        raise FluxKontextError("Не настроен ключ Black Forest Labs. Добавьте BFL_API_KEY в переменные окружения сервера")

    payload = {
        "prompt": build_kontext_prompt(prompt),
        "input_image": encode_image_to_base64(image_path),
        "output_format": "png",
    }
    headers = {
        "accept": "application/json",
        "Content-Type": "application/json",
        "x-key": api_key,
    }
    response = _request_with_retries("POST", BFL_API_URL, headers=headers, json=payload)
    _raise_for_bfl_error(response, "создание задачи FLUX Kontext")
    data = _json_or_error(response)
    task_id = str(data.get("id") or "")
    polling_url = str(data.get("polling_url") or "")
    if not polling_url:
        raise FluxKontextError("Black Forest Labs не вернул polling_url")
    return task_id, polling_url


def poll_kontext_result(polling_url: str, timeout_seconds: int = MAX_GENERATION_WAIT_SECONDS) -> str:
    api_key = get_bfl_api_key()
    if not api_key:
        raise FluxKontextError("Не настроен ключ Black Forest Labs. Добавьте BFL_API_KEY в переменные окружения сервера")

    deadline = time.monotonic() + timeout_seconds
    headers = {"x-key": api_key, "accept": "application/json"}

    while True:
        response = _request_with_retries("GET", polling_url, headers=headers)
        _raise_for_bfl_error(response, "опрос результата FLUX Kontext")
        data = _json_or_error(response)
        status = str(data.get("status") or "")

        if status == "Ready":
            sample_url = data.get("result", {}).get("sample") if isinstance(data.get("result"), dict) else None
            if not sample_url:
                raise FluxKontextError("Black Forest Labs не вернул result.sample")
            return str(sample_url)
        if status == "Pending":
            if time.monotonic() >= deadline:
                raise FluxKontextError("таймаут ожидания FLUX Kontext")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        if status in TERMINAL_ERROR_STATUSES:
            raise FluxKontextError(f"FLUX Kontext завершился со статусом: {status}")
        raise FluxKontextError(f"Неизвестный статус FLUX Kontext: {status or 'пустой статус'}")


def download_generated_image(url: str, output_path: Path) -> None:
    response = _request_with_retries("GET", url, stream=True)
    _raise_for_bfl_error(response, "скачивание результата FLUX Kontext")
    content_type = response.headers.get("Content-Type", "")
    if "image" not in content_type.lower() and content_type:
        raise FluxKontextError("Black Forest Labs вернул не изображение при скачивании результата")
    output_path.write_bytes(response.content)
    if output_path.stat().st_size == 0:
        output_path.unlink(missing_ok=True)
        raise FluxKontextError("скачанный результат FLUX Kontext пустой")


def generate_context_scene(input_path: Path, output_path: Path, user_prompt: str) -> None:
    _, polling_url = submit_kontext_request(input_path, user_prompt)
    sample_url = poll_kontext_result(polling_url)
    download_generated_image(sample_url, output_path)
