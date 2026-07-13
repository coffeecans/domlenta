from __future__ import annotations

import os
import shutil
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, send_file, url_for
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
MAX_ANGLES = 8

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-dom-lenta-secret")
app.config["MAX_CONTENT_LENGTH"] = 512 * 1024 * 1024


@dataclass(frozen=True)
class ModelPreset:
    code: str
    title: str
    description: str


MODEL_PRESETS = [
    ModelPreset(
        "local-studio",
        "Local Studio Diffusion",
        "Локальная бесплатная обработка: чистый студийный фон, мягкая тень и цветокоррекция.",
    ),
    ModelPreset(
        "local-context",
        "Local Context Composer",
        "Локальная бесплатная обработка: имитация контекстного фона по описанию сцены.",
    ),
    ModelPreset(
        "local-catalog",
        "Local Catalog Enhancer",
        "Локальная бесплатная обработка: каталожный вид, повышение резкости и контраста.",
    ),
]


def allowed_image(filename: str) -> bool:
    return Path(filename).suffix.lower() in ALLOWED_EXTENSIONS


def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    value = hex_color.strip().lstrip("#")
    if len(value) != 6:
        return (245, 245, 245)
    try:
        return tuple(int(value[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return (245, 245, 245)


def make_context_texture(size: tuple[int, int], base_color: tuple[int, int, int], context: str) -> Image.Image:
    width, height = size
    background = Image.new("RGB", size, base_color)
    draw = ImageDraw.Draw(background, "RGBA")
    context_lower = context.lower()

    if "сад" in context_lower or "трава" in context_lower or "улиц" in context_lower:
        draw.rectangle((0, int(height * 0.62), width, height), fill=(82, 145, 73, 190))
        for x in range(0, width, 18):
            draw.line((x, height, x + 18, int(height * 0.66)), fill=(42, 110, 45, 95), width=2)
    elif "дом" in context_lower or "интерьер" in context_lower or "комнат" in context_lower:
        draw.rectangle((0, int(height * 0.68), width, height), fill=(166, 128, 91, 150))
        for x in range(0, width, 80):
            draw.line((x, int(height * 0.68), x + 40, height), fill=(255, 255, 255, 35), width=3)
    elif "строй" in context_lower or "мастер" in context_lower or "гараж" in context_lower:
        draw.rectangle((0, int(height * 0.66), width, height), fill=(115, 115, 115, 150))
        for y in range(int(height * 0.66), height, 26):
            draw.line((0, y, width, y), fill=(255, 255, 255, 32), width=1)
    else:
        for offset in range(-height, width, 64):
            draw.line((offset, height, offset + height, 0), fill=(255, 255, 255, 38), width=18)

    return background.filter(ImageFilter.GaussianBlur(radius=0.8))


def fit_product(image: Image.Image, canvas_size: tuple[int, int]) -> Image.Image:
    product = image.convert("RGBA")
    product.thumbnail((int(canvas_size[0] * 0.72), int(canvas_size[1] * 0.72)), Image.Resampling.LANCZOS)
    return product


def compose_variant(
    source_path: Path,
    destination_path: Path,
    background_color: str,
    angle_index: int,
    total_angles: int,
    context: str,
    model_code: str,
) -> None:
    with Image.open(source_path) as image:
        canvas_size = (1400, 1400)
        base_color = hex_to_rgb(background_color)
        background = make_context_texture(canvas_size, base_color, context)
        product = fit_product(image, canvas_size)

        rotation_step = 0 if total_angles == 1 else 18 / max(total_angles - 1, 1)
        rotation = -9 + rotation_step * angle_index
        if model_code == "local-studio":
            rotation *= 0.45
        product = product.rotate(rotation, expand=True, resample=Image.Resampling.BICUBIC)

        if model_code == "local-catalog":
            product = ImageEnhance.Sharpness(product).enhance(1.25)
            product = ImageEnhance.Contrast(product).enhance(1.08)
        elif model_code == "local-context":
            product = ImageEnhance.Color(product).enhance(1.08)

        shadow = Image.new("RGBA", canvas_size, (0, 0, 0, 0))
        shadow_draw = ImageDraw.Draw(shadow, "RGBA")
        center_x = canvas_size[0] // 2
        shadow_y = int(canvas_size[1] * 0.77)
        shadow_draw.ellipse(
            (center_x - 320, shadow_y - 45, center_x + 320, shadow_y + 45),
            fill=(0, 0, 0, 55),
        )
        shadow = shadow.filter(ImageFilter.GaussianBlur(radius=26))
        composed = background.convert("RGBA")
        composed.alpha_composite(shadow)
        x = (canvas_size[0] - product.width) // 2
        y = int(canvas_size[1] * 0.48 - product.height / 2)
        composed.alpha_composite(product, (x, y))

        output = composed.convert("RGB")
        output.save(destination_path, quality=92, optimize=True)


def create_zip(folder: Path, zip_path: Path) -> None:
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for file_path in folder.rglob("*"):
            if file_path.is_file() and file_path != zip_path:
                archive.write(file_path, file_path.relative_to(folder))


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html", models=MODEL_PRESETS, max_angles=MAX_ANGLES)


@app.route("/process", methods=["POST"])
def process_batch():
    files = request.files.getlist("photos")
    background_color = request.form.get("background_color", "#f5f5f5")
    context = request.form.get("context", "").strip()
    prefix = secure_filename(request.form.get("prefix", "ai_").strip()) or "ai_"
    model_code = request.form.get("model", MODEL_PRESETS[0].code)

    try:
        angle_count = max(1, min(MAX_ANGLES, int(request.form.get("angle_count", "1"))))
    except ValueError:
        angle_count = 1

    if model_code not in {model.code for model in MODEL_PRESETS}:
        flash("Выберите доступную нейросеть обработки.", "error")
        return redirect(url_for("index"))

    valid_files = [file for file in files if file and file.filename and allowed_image(file.filename)]
    if not valid_files:
        flash("Загрузите хотя бы одно изображение в формате JPG, PNG или WebP.", "error")
        return redirect(url_for("index"))

    batch_id = uuid.uuid4().hex
    upload_batch_dir = UPLOAD_DIR / batch_id
    result_batch_dir = RESULT_DIR / batch_id
    upload_batch_dir.mkdir(parents=True, exist_ok=True)
    result_batch_dir.mkdir(parents=True, exist_ok=True)

    processed_count = 0
    for uploaded_file in valid_files:
        original_name = secure_filename(uploaded_file.filename)
        sku = Path(original_name).stem
        source_path = upload_batch_dir / original_name
        uploaded_file.save(source_path)

        sku_dir = result_batch_dir / sku
        sku_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, sku_dir / original_name)

        for angle_index in range(angle_count):
            destination = sku_dir / f"{prefix}{sku}_angle-{angle_index + 1:02d}.jpg"
            compose_variant(source_path, destination, background_color, angle_index, angle_count, context, model_code)
            processed_count += 1

    zip_path = result_batch_dir / f"dom_lenta_ai_batch_{batch_id}.zip"
    create_zip(result_batch_dir, zip_path)
    return render_template(
        "result.html",
        batch_id=batch_id,
        processed_count=processed_count,
        sku_count=len(valid_files),
        download_url=url_for("download_batch", batch_id=batch_id),
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
