from __future__ import annotations

import os
import shutil
import traceback
import uuid
import zipfile
from functools import lru_cache
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, send_file, url_for
from PIL import Image, ImageDraw, ImageFilter
from dotenv import load_dotenv
from rembg import new_session, remove
from werkzeug.utils import secure_filename

from services.flux_kontext import FluxKontextError, generate_context_scene, get_bfl_api_key

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
CANVAS_SIZE = (1400, 1400)
DEFAULT_BACKGROUND_COLOR = "#FFFFFF"
APP_VERSION = "v4-ai-bg-removal"

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-dom-lenta-secret")
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024


def allowed_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.strip().lstrip("#")
    if len(value) != 6:
        return (255, 255, 255)
    try:
        return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return (255, 255, 255)


def ai_output_name(sku: str, index: int) -> str:
    return f"{sku}_ai_{index}.png"


@lru_cache(maxsize=1)
def background_removal_session():
    return new_session("isnet-general-use")


def remove_product_background(source_path: Path) -> Image.Image:
    """Use rembg/IS-Net to cut the main product out of the original photo."""
    with Image.open(source_path) as image:
        source = image.convert("RGBA")
    cutout = remove(
        source,
        session=background_removal_session(),
        alpha_matting=True,
        alpha_matting_foreground_threshold=240,
        alpha_matting_background_threshold=10,
        alpha_matting_erode_size=10,
    )
    return cutout.convert("RGBA")


def trim_transparent_padding(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A")
    bbox = alpha.getbbox()
    if bbox is None:
        return image
    return image.crop(bbox)


def fit_product_on_canvas(product: Image.Image, canvas_size: tuple[int, int] = CANVAS_SIZE) -> tuple[Image.Image, tuple[int, int]]:
    fitted = trim_transparent_padding(product).copy()
    fitted.thumbnail((int(canvas_size[0] * 0.82), int(canvas_size[1] * 0.82)), Image.Resampling.LANCZOS)
    x = (canvas_size[0] - fitted.width) // 2
    y = (canvas_size[1] - fitted.height) // 2
    return fitted, (x, y)


def add_product_shadow(product: Image.Image, position: tuple[int, int], canvas_size: tuple[int, int]) -> Image.Image:
    shadow = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
    alpha = product.getchannel("A").filter(ImageFilter.GaussianBlur(radius=18))
    shadow_layer = Image.new("RGBA", product.size, (0, 0, 0, 70))
    shadow_layer.putalpha(alpha.point(lambda value: int(value * 0.28)))
    shadow.alpha_composite(shadow_layer, (position[0] + 12, position[1] + 18))
    return shadow


def create_catalog_image(product_cutout: Image.Image, background_color: str) -> Image.Image:
    background = Image.new("RGBA", CANVAS_SIZE, (*hex_to_rgb(background_color), 255))
    product, position = fit_product_on_canvas(product_cutout)
    background.alpha_composite(add_product_shadow(product, position, CANVAS_SIZE))
    background.alpha_composite(product, position)
    return background


def create_context_scene(catalog_image_path: Path, context_path: Path, prompt: str) -> None:
    generate_context_scene(catalog_image_path, context_path, prompt)


def process_product_images(source_path: Path, catalog_path: Path, context_path: Path, background_color: str, prompt: str) -> None:
    product_cutout = remove_product_background(source_path)
    create_catalog_image(product_cutout, background_color).save(catalog_path, format="PNG", optimize=True)
    create_context_scene(catalog_path, context_path, prompt)


def create_zip(folder: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in folder.rglob("*"):
            if file_path.is_file() and file_path != zip_path:
                archive.write(file_path, file_path.relative_to(folder))


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", default_background_color=DEFAULT_BACKGROUND_COLOR, app_version=APP_VERSION)


@app.route("/healthz", methods=["GET"])
def healthz():
    return {"status": "ok", "version": APP_VERSION}


@app.route("/process", methods=["POST"])
def process_batch():
    files = request.files.getlist("photos")
    background_color = request.form.get("background_color", DEFAULT_BACKGROUND_COLOR)
    prompt = request.form.get("prompt", "").strip()

    if not prompt:
        flash("Введите промт для контекстной сцены использования товара.", "error")
        return redirect(url_for("index"))

    valid_files = [file for file in files if file and file.filename and allowed_image(file.filename)]
    if not valid_files:
        flash("Загрузите хотя бы одно изображение в формате JPG, PNG или WebP.", "error")
        return redirect(url_for("index"))

    if not get_bfl_api_key():
        flash("Не настроен ключ Black Forest Labs. Добавьте BFL_API_KEY в переменные окружения сервера", "error")
        return redirect(url_for("index"))

    batch_id = uuid.uuid4().hex
    upload_batch_dir = UPLOAD_DIR / batch_id
    result_batch_dir = RESULT_DIR / batch_id
    upload_batch_dir.mkdir(parents=True, exist_ok=True)
    result_batch_dir.mkdir(parents=True, exist_ok=True)

    processed_count = 0
    report_lines: list[str] = []
    for uploaded_file in valid_files:
        original_name = secure_filename(uploaded_file.filename)
        sku = Path(original_name).stem
        source_path = upload_batch_dir / original_name
        uploaded_file.save(source_path)

        sku_dir = result_batch_dir / sku
        sku_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, sku_dir / original_name)

        try:
            process_product_images(
                source_path=source_path,
                catalog_path=sku_dir / ai_output_name(sku, 1),
                context_path=sku_dir / ai_output_name(sku, 2),
                background_color=background_color,
                prompt=prompt,
            )
        except FluxKontextError as error:
            app.logger.error("FLUX Kontext failed for batch %s SKU %s: %s", batch_id, sku, error)
            report_lines.append(f"{original_name} — ошибка FLUX: {error}")
            continue
        except Exception as error:
            app.logger.error("Batch %s failed for SKU %s", batch_id, sku)
            app.logger.error(traceback.format_exc())
            report_lines.append(f"{original_name} — ошибка обработки: {error}")
            continue

        processed_count += 2
        report_lines.append(f"{original_name} — готово")

    (result_batch_dir / "processing_report.txt").write_text("\n".join(report_lines), encoding="utf-8")

    zip_path = result_batch_dir / f"dom_lenta_ai_batch_{batch_id}.zip"
    create_zip(result_batch_dir, zip_path)
    return render_template(
        "result.html",
        batch_id=batch_id,
        processed_count=processed_count,
        sku_count=len(valid_files),
        download_url=url_for("download_batch", batch_id=batch_id),
        app_version=APP_VERSION,
    )


@app.route("/download/<batch_id>", methods=["GET"])
def download_batch(batch_id: str):
    safe_batch_id = secure_filename(batch_id)
    zip_path = RESULT_DIR / safe_batch_id / f"dom_lenta_ai_batch_{safe_batch_id}.zip"
    if not zip_path.exists():
        flash("Архив не найден. Повторите обработку партии.", "error")
        return redirect(url_for("index"))
    return send_file(zip_path, as_attachment=True, download_name=zip_path.name)


if __name__ == "__main__":
    app.run(debug=True)
