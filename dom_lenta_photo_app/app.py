from __future__ import annotations

import importlib.util
import os
import shutil
import uuid
import zipfile
from functools import lru_cache
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, send_file, url_for
from PIL import Image, ImageDraw, ImageFilter
from rembg import new_session, remove
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "uploads"
RESULT_DIR = BASE_DIR / "results"
ALLOWED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
CANVAS_SIZE = (1400, 1400)
DEFAULT_BACKGROUND_COLOR = "#FFFFFF"
APP_VERSION = "v4-ai-bg-removal"
CONTEXT_MODEL_ID = os.environ.get("CONTEXT_MODEL_ID", "runwayml/stable-diffusion-v1-5")

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


@lru_cache(maxsize=1)
def context_scene_pipeline():
    if importlib.util.find_spec("diffusers") is None or importlib.util.find_spec("torch") is None:
        raise RuntimeError(
            "Локальная генерация контекстной сцены не установлена. "
            "Для CPU/GPU worker установите dom_lenta_photo_app/requirements-ai.txt или подключите отдельный сервис генерации."
        )

    import torch
    from diffusers import StableDiffusionPipeline

    device = "cuda" if torch.cuda.is_available() else "cpu"
    dtype = torch.float16 if device == "cuda" else torch.float32
    pipeline = StableDiffusionPipeline.from_pretrained(CONTEXT_MODEL_ID, torch_dtype=dtype)
    pipeline = pipeline.to(device)
    if device == "cuda":
        pipeline.enable_attention_slicing()
    return pipeline


def generate_ai_scene_background(prompt: str) -> Image.Image:
    full_prompt = (
        f"{prompt}. Photorealistic ecommerce product photography scene, realistic lighting, "
        "clean composition, space in the center for the original product, high quality."
    )
    negative_prompt = (
        "extra duplicate product, changed product design, distorted object, low quality, blurry, "
        "text, watermark, logo, deformed geometry, bad perspective"
    )
    pipeline = context_scene_pipeline()
    result = pipeline(
        prompt=full_prompt,
        negative_prompt=negative_prompt,
        width=768,
        height=768,
        num_inference_steps=int(os.environ.get("CONTEXT_INFERENCE_STEPS", "30")),
        guidance_scale=float(os.environ.get("CONTEXT_GUIDANCE_SCALE", "7.5")),
    )
    return result.images[0].convert("RGBA").resize(CANVAS_SIZE, Image.Resampling.LANCZOS)


def create_context_scene(product_cutout: Image.Image, prompt: str) -> Image.Image:
    scene = generate_ai_scene_background(prompt)
    product, position = fit_product_on_canvas(product_cutout)
    scene.alpha_composite(add_product_shadow(product, position, CANVAS_SIZE))
    scene.alpha_composite(product, position)
    return scene


def process_product_images(source_path: Path, catalog_path: Path, context_path: Path, background_color: str, prompt: str) -> None:
    product_cutout = remove_product_background(source_path)
    create_catalog_image(product_cutout, background_color).save(catalog_path, format="PNG", optimize=True)
    create_context_scene(product_cutout, prompt).save(context_path, format="PNG", optimize=True)


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

        try:
            process_product_images(
                source_path=source_path,
                catalog_path=sku_dir / ai_output_name(sku, 1),
                context_path=sku_dir / ai_output_name(sku, 2),
                background_color=background_color,
                prompt=prompt,
            )
        except RuntimeError as error:
            flash(str(error), "error")
            shutil.rmtree(result_batch_dir, ignore_errors=True)
            shutil.rmtree(upload_batch_dir, ignore_errors=True)
            return redirect(url_for("index"))
        processed_count += 2

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
