import os
import pickle
import math
import re
import shutil
import sqlite3
import subprocess
import uuid
import pytesseract
from datetime import datetime
from PIL import Image, ImageEnhance, ImageFilter, ImageOps, UnidentifiedImageError
from pathlib import Path
from flask import Flask, Response, jsonify, redirect, render_template, request, session, url_for
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_FOLDER = BASE_DIR / 'static' / 'uploads'
DATABASE_PATH = Path(os.environ.get("TRUTHLENS_DB_PATH", BASE_DIR / "truthlens.db"))
MAX_UPLOAD_SIZE = 10 * 1024 * 1024
OCR_TIMEOUT_SECONDS = int(os.environ.get("OCR_TIMEOUT_SECONDS", "8"))
ENABLE_SLOW_OCR_FALLBACK = os.environ.get("ENABLE_SLOW_OCR_FALLBACK") == "1"

# Windows-safe folder creation
try:
    UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
except FileExistsError:
    pass

app.config['UPLOAD_FOLDER'] = str(UPLOAD_FOLDER)
app.config['MAX_CONTENT_LENGTH'] = MAX_UPLOAD_SIZE
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "truthlens-dev-secret-key")


def get_db_connection():
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def init_database():
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with get_db_connection() as connection:
        connection.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        connection.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                title TEXT NOT NULL,
                news_text TEXT,
                prediction TEXT NOT NULL,
                display_prediction TEXT NOT NULL,
                result_class TEXT NOT NULL,
                confidence REAL,
                reason TEXT,
                extracted_text TEXT,
                uploaded_image TEXT,
                image_type TEXT,
                language TEXT NOT NULL DEFAULT 'en',
                created_at TEXT NOT NULL,
                FOREIGN KEY(user_id) REFERENCES users(id) ON DELETE CASCADE
            )
        """)


def now_string():
    return datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")


init_database()

model      = pickle.load(open(str(BASE_DIR / 'model.pkl'),      'rb'))
vectorizer = pickle.load(open(str(BASE_DIR / 'vectorizer.pkl'), 'rb'))

UI_TEXT = {
    "en": {
        "real_news": "Real News",
        "fake_news": "Fake News",
        "confidence_score": "Confidence Score",
        "reason_label": "Reason",
        "image_type_label": "Detected Image Type",
        "extracted_text_label": "Extracted Text from Image",
        "invalid_image": "Please upload a valid image file.",
        "file_too_large": "Image is too large. Please upload an image under 10 MB.",
        "ocr_failed": "OCR could not read this image. Please try a clearer image or paste the news text manually.",
        "missing_input": "Please add news headlines or upload an image with readable text before analyzing.",
        "server_error": "Server error while analyzing. Please try again.",
    },
    "hi": {
        "real_news": "असली खबर",
        "fake_news": "फर्जी खबर",
        "confidence_score": "विश्वास स्कोर",
        "reason_label": "कारण",
        "image_type_label": "पहचाना गया इमेज प्रकार",
        "extracted_text_label": "इमेज से निकाला गया टेक्स्ट",
        "invalid_image": "कृपया सही इमेज फाइल अपलोड करें।",
        "ocr_failed": "OCR इस इमेज को पढ़ नहीं पाया। कृपया साफ इमेज अपलोड करें या खबर का टेक्स्ट खुद लिखें।",
        "missing_input": "कृपया विश्लेषण से पहले खबर का टेक्स्ट डालें या पढ़ने योग्य टेक्स्ट वाली इमेज अपलोड करें।",
    },
}

IMAGE_TYPE_LABELS = {
    "en": {
        "WhatsApp Chat": "WhatsApp Chat",
        "Twitter/X Post": "Twitter/X Post",
        "News Screenshot": "News Screenshot",
        "Generic Screenshot": "Generic Screenshot",
    },
    "hi": {
        "WhatsApp Chat": "व्हाट्सऐप चैट",
        "Twitter/X Post": "ट्विटर/X पोस्ट",
        "News Screenshot": "न्यूज स्क्रीनशॉट",
        "Generic Screenshot": "सामान्य स्क्रीनशॉट",
    },
}


def normalize_language(language):
    return "hi" if language == "hi" else "en"


def get_ui_text(language):
    return UI_TEXT[normalize_language(language)]


def get_current_user():
    user_id = session.get("user_id")
    if not user_id:
        return None

    with get_db_connection() as connection:
        user = connection.execute(
            "SELECT id, name, email, created_at FROM users WHERE id = ?",
            (user_id,),
        ).fetchone()

    if not user:
        session.pop("user_id", None)
        return None

    return dict(user)


def make_analysis_title(news_text, extracted_text, uploaded_image):
    source = news_text or extracted_text or uploaded_image or "TruthLens analysis"
    compact = re.sub(r"\s+", " ", source).strip()
    return f"{compact[:67]}..." if len(compact) > 70 else compact


def serialize_analysis_row(row):
    selected_language = normalize_language(row["language"])
    ui = get_ui_text(selected_language)
    uploaded_image = row["uploaded_image"]
    analysis_id = row["id"]

    data = {
        "analysis_id": analysis_id,
        "prediction": row["prediction"],
        "display_prediction": row["display_prediction"],
        "result_class": row["result_class"],
        "confidence": row["confidence"],
        "reason": row["reason"],
        "extracted_text": row["extracted_text"],
        "uploaded_image": uploaded_image,
        "uploaded_image_url": url_for("static", filename=uploaded_image) if uploaded_image else None,
        "image_type": row["image_type"],
        "selected_language": selected_language,
        "show_result": True,
        "report_url": url_for("download_report", analysis_id=analysis_id),
        "ui": {
            "confidence_score": ui["confidence_score"],
            "reason_label": ui["reason_label"],
            "image_type_label": ui["image_type_label"],
            "extracted_text_label": ui["extracted_text_label"],
        },
    }

    return {
        "id": f"saved-{analysis_id}",
        "serverId": analysis_id,
        "title": row["title"],
        "newsText": row["news_text"] or "",
        "createdAt": row["created_at"],
        "data": data,
    }


def get_user_analyses(user_id, limit=8):
    if not user_id:
        return []

    with get_db_connection() as connection:
        rows = connection.execute(
            """
            SELECT *
            FROM analyses
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()

    return [serialize_analysis_row(row) for row in rows]


def get_dashboard_stats(user_id):
    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN result_class = 'real' THEN 1 ELSE 0 END) AS real_count,
                SUM(CASE WHEN result_class = 'fake' THEN 1 ELSE 0 END) AS fake_count
            FROM analyses
            WHERE user_id = ?
            """,
            (user_id,),
        ).fetchone()

    total = row["total"] or 0
    return {
        "total": total,
        "real": row["real_count"] or 0,
        "fake": row["fake_count"] or 0,
    }


def save_analysis_for_current_user(**analysis):
    user_id = session.get("user_id")
    if not user_id:
        return None

    title = make_analysis_title(
        analysis.get("typed_text") or analysis.get("news_text"),
        analysis.get("extracted_text"),
        analysis.get("uploaded_image"),
    )

    with get_db_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO analyses (
                user_id, title, news_text, prediction, display_prediction,
                result_class, confidence, reason, extracted_text, uploaded_image,
                image_type, language, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                user_id,
                title,
                analysis.get("news_text", ""),
                analysis["prediction"],
                analysis["display_prediction"],
                analysis["result_class"],
                analysis.get("confidence"),
                analysis.get("reason"),
                analysis.get("extracted_text"),
                analysis.get("uploaded_image"),
                analysis.get("image_type"),
                normalize_language(analysis.get("selected_language")),
                now_string(),
            ),
        )
        return cursor.lastrowid


def configure_tesseract():
    tessdata_paths = [
        os.environ.get("TESSDATA_PREFIX"),
        "/usr/share/tesseract-ocr/5/tessdata",
        "/usr/share/tesseract-ocr/4.00/tessdata",
    ]
    for tessdata_path in tessdata_paths:
        if tessdata_path and Path(tessdata_path).exists():
            os.environ["TESSDATA_PREFIX"] = tessdata_path
            break

    candidates = [
        os.environ.get("TESSERACT_CMD"),
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        shutil.which("tesseract"),
    ]

    for candidate in candidates:
        if candidate and Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = candidate
            return True

    return False


TESSERACT_CONFIGURED = configure_tesseract()
IS_WINDOWS = os.name == "nt"


def run_windows_ocr(image_path):
    if not IS_WINDOWS:
        raise RuntimeError("Windows OCR is only available on Windows.")

    powershell = shutil.which("powershell") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    if not Path(powershell).exists():
        raise RuntimeError("Windows OCR is not available.")

    script = r"""
$path = $env:TRUTHLENS_OCR_IMAGE
[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new()
Add-Type -AssemblyName System.Runtime.WindowsRuntime
[Windows.Storage.StorageFile, Windows.Storage, ContentType=WindowsRuntime] | Out-Null
[Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
[Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType=WindowsRuntime] | Out-Null
[Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType=WindowsRuntime] | Out-Null

function AwaitOperation($operation, $resultType) {
    $asTask = [System.WindowsRuntimeSystemExtensions].GetMethods() |
        Where-Object { $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 } |
        Select-Object -First 1
    $task = $asTask.MakeGenericMethod($resultType).Invoke($null, @($operation))
    $task.Wait() | Out-Null
    return $task.Result
}

$file = AwaitOperation ([Windows.Storage.StorageFile]::GetFileFromPathAsync($path)) ([Windows.Storage.StorageFile])
$stream = AwaitOperation ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
$decoder = AwaitOperation ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
$bitmap = AwaitOperation ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
$engine = [Windows.Media.Ocr.OcrEngine]::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {
    throw "No Windows OCR language is available."
}
$result = AwaitOperation ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
$result.Text
"""

    completed = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "TRUTHLENS_OCR_IMAGE": str(image_path)},
        timeout=45,
    )

    if completed.returncode != 0:
        message = completed.stderr.strip() or completed.stdout.strip() or "Windows OCR failed."
        raise RuntimeError(message)

    return completed.stdout.strip()


def score_ocr_text(text):
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9'-]{1,}", text or "")
    return len(words), len(text or "")


def has_enough_ocr_text(text, min_words=20):
    words, characters = score_ocr_text(text)
    return words >= min_words or characters >= 140


def resize_for_ocr(image, min_dimension=1000, max_dimension=1600):
    width, height = image.size
    largest = max(width, height)
    scale = 1

    if largest < min_dimension:
        scale = min_dimension / largest
    elif largest > max_dimension:
        scale = max_dimension / largest

    if scale == 1:
        return image

    new_size = (max(1, int(width * scale)), max(1, int(height * scale)))
    return image.resize(new_size, Image.Resampling.LANCZOS)


def save_uploaded_image(file_storage):
    original_name = secure_filename(file_storage.filename or "")
    if original_name == "":
        raise UnidentifiedImageError("Missing image filename.")

    filename = f"news_{uuid.uuid4().hex}.jpg"
    image_path = UPLOAD_FOLDER / filename

    with Image.open(file_storage.stream) as uploaded:
        normalized = ImageOps.exif_transpose(uploaded).convert("RGB")
        normalized = resize_for_ocr(normalized, min_dimension=800, max_dimension=1100)
        normalized.save(image_path, "JPEG", quality=86, optimize=True)

    return image_path, f"uploads/{filename}"


def save_ocr_variant(image, image_path, suffix):
    temp_path = UPLOAD_FOLDER / f"ocr_{Path(image_path).stem}_{suffix}_{os.getpid()}.png"
    image.save(temp_path)
    return temp_path


def prepare_ocr_candidates(image_path):
    candidates = []

    with Image.open(image_path) as uploaded:
        base = ImageOps.exif_transpose(uploaded).convert("RGB")
        base = resize_for_ocr(base, min_dimension=900, max_dimension=1200)

        gray = ImageOps.grayscale(base)
        enhanced = ImageEnhance.Contrast(gray).enhance(1.9)
        enhanced = ImageEnhance.Sharpness(enhanced).enhance(1.4)
        enhanced = enhanced.filter(ImageFilter.SHARPEN)

        contrast_path = save_ocr_variant(enhanced, image_path, "contrast")
        candidates.append((contrast_path, True))

        if ENABLE_SLOW_OCR_FALLBACK:
            normalized_path = save_ocr_variant(gray, image_path, "gray")
            candidates.append((normalized_path, True))

    return candidates


def extract_text_from_image(image_path):
    candidates = prepare_ocr_candidates(image_path)
    texts = []
    last_error = None

    try:
        if TESSERACT_CONFIGURED:
            ocr_runs = []
            if candidates:
                ocr_runs.append((candidates[0][0], "eng", "--oem 1 --psm 6 --dpi 220"))
            if ENABLE_SLOW_OCR_FALLBACK and len(candidates) > 1:
                ocr_runs.append((candidates[1][0], "eng", "--oem 1 --psm 4 --dpi 220"))

            for candidate_path, language, config in ocr_runs:
                try:
                    with Image.open(candidate_path) as candidate:
                        text = pytesseract.image_to_string(
                            candidate,
                            lang=language,
                            config=config,
                            timeout=OCR_TIMEOUT_SECONDS,
                        ).strip()
                        texts.append(text)
                        if has_enough_ocr_text(text):
                            return text
                except UnidentifiedImageError:
                    raise
                except (RuntimeError, pytesseract.TesseractNotFoundError, pytesseract.TesseractError) as error:
                    last_error = error

        if IS_WINDOWS:
            for candidate_path, _cleanup in candidates:
                try:
                    text = run_windows_ocr(candidate_path).strip()
                    texts.append(text)
                    if has_enough_ocr_text(text):
                        return text
                except RuntimeError as error:
                    last_error = error
                except subprocess.SubprocessError as error:
                    last_error = error

        best_text = max(texts, key=score_ocr_text, default="").strip()
        if best_text:
            return best_text

        if last_error:
            app.logger.warning("OCR failed for %s: %s", image_path, last_error)

        return ""
    finally:
        for candidate_path, cleanup in candidates:
            if cleanup and candidate_path.exists():
                candidate_path.unlink(missing_ok=True)


@app.errorhandler(RequestEntityTooLarge)
def handle_request_too_large(_error):
    selected_language = "en"
    ui = get_ui_text(selected_language)
    message = ui.get("file_too_large", "Image is too large. Please upload an image under 10 MB.")
    response = respond_index(error=message, selected_language=selected_language)
    return response, 413


@app.errorhandler(Exception)
def handle_unexpected_error(error):
    if isinstance(error, HTTPException):
        return error

    app.logger.exception("Unexpected server error")
    selected_language = normalize_language(request.form.get("language", "en"))
    ui = get_ui_text(selected_language)
    response = respond_index(
        error=ui.get("server_error", "Server error while analyzing. Please try again."),
        selected_language=selected_language,
    )
    return response, 500


def build_template_context(**context):
    selected_language = normalize_language(context.get("selected_language", "en"))
    current_user = get_current_user()
    defaults = {
        "prediction": None,
        "display_prediction": None,
        "result_class": None,
        "confidence": None,
        "reason": None,
        "extracted_text": None,
        "uploaded_image": None,
        "image_type": None,
        "error": None,
        "show_result": False,
        "show_dashboard": False,
        "clear_on_reload": False,
        "analysis_id": None,
        "report_url": None,
        "selected_language": selected_language,
        "ui": get_ui_text(selected_language),
        "current_user": current_user,
        "saved_recents": get_user_analyses(current_user["id"], limit=6) if current_user else [],
        "dashboard_stats": None,
        "dashboard_items": [],
    }
    defaults.update(context)
    defaults["selected_language"] = selected_language
    defaults["ui"] = get_ui_text(selected_language)
    return defaults


def render_index(**context):
    defaults = build_template_context(**context)
    return render_template("index2.html", **defaults)


def wants_json_response():
    accept_header = request.headers.get("Accept", "")
    requested_with = request.headers.get("X-Requested-With", "")
    return requested_with == "XMLHttpRequest" or "application/json" in accept_header


def build_json_response(**context):
    data = build_template_context(**context)
    uploaded_image = data.get("uploaded_image")

    return jsonify({
        "ok": bool(data.get("show_result")) and not bool(data.get("error")),
        "error": data.get("error"),
        "prediction": data.get("prediction"),
        "display_prediction": data.get("display_prediction"),
        "result_class": data.get("result_class"),
        "confidence": data.get("confidence"),
        "reason": data.get("reason"),
        "extracted_text": data.get("extracted_text"),
        "uploaded_image": uploaded_image,
        "uploaded_image_url": url_for("static", filename=uploaded_image) if uploaded_image else None,
        "image_type": data.get("image_type"),
        "analysis_id": data.get("analysis_id"),
        "report_url": data.get("report_url"),
        "selected_language": data.get("selected_language"),
        "show_result": data.get("show_result"),
        "ui": {
            "confidence_score": data["ui"]["confidence_score"],
            "reason_label": data["ui"]["reason_label"],
            "image_type_label": data["ui"]["image_type_label"],
            "extracted_text_label": data["ui"]["extracted_text_label"],
        },
    })


def respond_index(**context):
    if wants_json_response():
        return build_json_response(**context)

    return render_index(**context)


def auth_error(message, status=400):
    if wants_json_response():
        return jsonify({"ok": False, "error": message}), status

    return render_index(error=message), status


@app.route("/signup", methods=["POST"])
def signup():
    name = request.form.get("name", "").strip()
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    if not name or not email or not password:
        return auth_error("Please enter your name, email, and password.")

    if len(password) < 6:
        return auth_error("Password must be at least 6 characters.")

    try:
        with get_db_connection() as connection:
            cursor = connection.execute(
                """
                INSERT INTO users (name, email, password_hash, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (name, email, generate_password_hash(password), now_string()),
            )
            session["user_id"] = cursor.lastrowid
    except sqlite3.IntegrityError:
        return auth_error("An account with this email already exists.")

    if wants_json_response():
        return jsonify({"ok": True, "message": "Account created successfully."})

    return redirect(url_for("home"))


@app.route("/login", methods=["POST"])
def login():
    email = request.form.get("email", "").strip().lower()
    password = request.form.get("password", "")

    with get_db_connection() as connection:
        user = connection.execute(
            "SELECT id, password_hash FROM users WHERE email = ?",
            (email,),
        ).fetchone()

    if not user or not check_password_hash(user["password_hash"], password):
        return auth_error("Invalid email or password.")

    session["user_id"] = user["id"]

    if wants_json_response():
        return jsonify({"ok": True, "message": "Logged in successfully."})

    return redirect(url_for("home"))


@app.route("/logout", methods=["POST"])
def logout():
    session.pop("user_id", None)

    if wants_json_response():
        return jsonify({"ok": True})

    return redirect(url_for("home"))


@app.route("/dashboard")
def dashboard():
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("home"))

    return render_index(
        show_dashboard=True,
        dashboard_stats=get_dashboard_stats(current_user["id"]),
        dashboard_items=get_user_analyses(current_user["id"], limit=30),
    )


@app.route("/history/<int:analysis_id>/delete", methods=["POST"])
def delete_analysis(analysis_id):
    current_user = get_current_user()
    if not current_user:
        if wants_json_response():
            return jsonify({"ok": False, "error": "Login required."}), 401

        return redirect(url_for("home"))

    with get_db_connection() as connection:
        connection.execute(
            "DELETE FROM analyses WHERE id = ? AND user_id = ?",
            (analysis_id, current_user["id"]),
        )

    if wants_json_response():
        return jsonify({"ok": True})

    return redirect(url_for("dashboard"))


@app.route("/report/<int:analysis_id>")
def download_report(analysis_id):
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for("home"))

    with get_db_connection() as connection:
        row = connection.execute(
            """
            SELECT *
            FROM analyses
            WHERE id = ? AND user_id = ?
            """,
            (analysis_id, current_user["id"]),
        ).fetchone()

    if not row:
        return Response("Report not found.", status=404, mimetype="text/plain")

    report = f"""TruthLens AI Analysis Report

Generated: {row['created_at']}
User: {current_user['name']} <{current_user['email']}>

Verdict: {row['display_prediction']}
Confidence: {row['confidence'] if row['confidence'] is not None else 'N/A'}%
Detected Image Type: {row['image_type'] or 'Text input'}

Reason:
{row['reason'] or 'No reason available.'}

Submitted Text:
{row['news_text'] or 'No typed text submitted.'}

Extracted Image Text:
{row['extracted_text'] or 'No extracted image text.'}
"""

    filename = f"truthlens-report-{analysis_id}.txt"
    return Response(
        report,
        mimetype="text/plain",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


def get_model_confidence(text_vector, prediction_raw):
    if hasattr(model, "predict_proba"):
        probabilities = model.predict_proba(text_vector)[0]
        return round(float(max(probabilities)) * 100, 2)

    if not hasattr(model, "decision_function"):
        return None

    scores = model.decision_function(text_vector)
    first_score = scores[0]

    if hasattr(first_score, "__len__"):
        values = [float(value) for value in first_score]
        max_score = max(values)
        exp_scores = [math.exp(max(min(value - max_score, 60), -60)) for value in values]
        return round((max(exp_scores) / sum(exp_scores)) * 100, 2)

    score = max(min(float(first_score), 60), -60)
    real_probability = 1 / (1 + math.exp(-score))
    confidence = real_probability if int(prediction_raw) == 1 else 1 - real_probability
    return round(confidence * 100, 2)


def get_prediction_signals(text_vector, prediction_raw, limit=6):
    if not hasattr(model, "coef_") or not hasattr(vectorizer, "get_feature_names_out"):
        return []

    try:
        feature_names = vectorizer.get_feature_names_out()
        coefficients = model.coef_

        if coefficients.shape[0] == 1:
            positive_class = model.classes_[-1] if hasattr(model, "classes_") else 1
            direction = 1 if int(prediction_raw) == int(positive_class) else -1
            weights = coefficients[0] * direction
        else:
            class_index = list(model.classes_).index(prediction_raw)
            weights = coefficients[class_index]

        row = text_vector.tocoo()
        signals = []
        for feature_index, value in zip(row.col, row.data):
            score = float(value) * float(weights[feature_index])
            if score > 0:
                signals.append((feature_names[feature_index], score))

        if signals:
            signals.sort(key=lambda item: item[1], reverse=True)
            return [term for term, _score in signals[:limit]]

        fallback_terms = sorted(
            ((feature_names[feature_index], float(value)) for feature_index, value in zip(row.col, row.data)),
            key=lambda item: item[1],
            reverse=True,
        )
        return [term for term, _value in fallback_terms[:limit]]
    except Exception:
        return []


def get_text_terms(news_text, limit=4):
    stop_terms = {
        "about", "after", "also", "and", "are", "before", "from", "have",
        "into", "that", "the", "their", "this", "with", "will", "your",
        "किया", "लिए", "और", "यह", "है", "था", "की", "का", "के", "से",
    }
    terms = []
    seen_terms = set()
    for word in re.findall(r"\b[\w'-]+\b", news_text):
        term = word.lower().strip("-'")
        if len(term) < 4 or term in stop_terms or term in seen_terms:
            continue
        terms.append(term)
        seen_terms.add(term)
        if len(terms) == limit:
            break
    return terms


def build_prediction_reason(news_text, text_vector, prediction_raw, language):
    language = normalize_language(language)
    signals = get_prediction_signals(text_vector, prediction_raw, limit=4) or get_text_terms(news_text)
    signal_text = ", ".join(signals[:4])
    text_lower = news_text.lower()
    sensational_terms = [
        "secret", "secretly", "shocking", "viral", "leaked", "conspiracy",
        "expose", "elite", "miracle", "banned", "fake", "rumor",
    ]
    matched_terms = [term for term in sensational_terms if term in text_lower]

    if int(prediction_raw) == 1:
        if language == "hi":
            if signal_text:
                return f"मॉडल ने इसे असली खबर माना क्योंकि इसका लेखन औपचारिक समाचार जैसा है और मुख्य संकेत शब्द ({signal_text}) भरोसेमंद खबरों के पैटर्न से मेल खाते हैं।"
            return "मॉडल ने इसे असली खबर माना क्योंकि इसकी भाषा सामान्य, औपचारिक और भरोसेमंद समाचार लेखन जैसी दिखती है।"

        if signal_text:
            return f"The model marked this as Real News because its wording looks closer to formal reporting, and key terms like {signal_text} match patterns seen in reliable news articles."
        return "The model marked this as Real News because the writing style looks more formal and closer to reliable news articles."

    if language == "hi":
        if matched_terms:
            return f"मॉडल ने इसे फर्जी खबर माना क्योंकि इसमें संदिग्ध या सनसनीखेज शब्द मिले ({', '.join(matched_terms[:4])}), और इसकी भाषा भ्रामक खबरों के पैटर्न से मेल खाती है।"
        if signal_text:
            return f"मॉडल ने इसे फर्जी खबर माना क्योंकि मुख्य संकेत शब्द ({signal_text}) फेक या भ्रामक खबरों में मिलने वाले पैटर्न से मेल खाते हैं।"
        return "मॉडल ने इसे फर्जी खबर माना क्योंकि इसकी भाषा फेक या भ्रामक खबरों के पैटर्न जैसी दिखती है।"

    if matched_terms:
        return f"The model marked this as Fake News because it found suspicious or sensational wording such as {', '.join(matched_terms[:4])}, and the text pattern is closer to misleading news articles."
    if signal_text:
        return f"The model marked this as Fake News because key terms like {signal_text} match patterns commonly found in fake or misleading news articles."
    return "The model marked this as Fake News because the writing pattern is closer to fake or misleading news articles."


def localize_image_type(platform, language):
    language = normalize_language(language)
    return IMAGE_TYPE_LABELS[language].get(platform, platform)


def build_analysis(news_text, typed_text, extracted_text, uploaded_image, text_vector, prediction_raw, confidence):
    words = re.findall(r"\b[\w'-]+\b", news_text)
    word_count = len(words)
    char_count = len(news_text)
    stop_terms = {
        "about", "after", "also", "and", "are", "before", "from", "have",
        "into", "that", "the", "their", "this", "with", "will", "your",
    }
    signals = get_prediction_signals(text_vector, prediction_raw)

    if not signals:
        seen_terms = set()
        for word in words:
            term = word.lower().strip("-'")
            if len(term) < 4 or term in stop_terms or term in seen_terms:
                continue
            signals.append(term)
            seen_terms.add(term)
            if len(signals) == 6:
                break

    if typed_text and uploaded_image:
        source = "Text + Image OCR"
    elif uploaded_image:
        source = "Image OCR"
    else:
        source = "Typed Text"

    if confidence is None:
        confidence_label = "Unavailable"
    elif confidence >= 85:
        confidence_label = "High"
    elif confidence >= 65:
        confidence_label = "Moderate"
    else:
        confidence_label = "Low"

    notes = []
    if uploaded_image:
        notes.append("OCR text extracted from the uploaded image.")
    if word_count < 30:
        notes.append("Short inputs can produce less stable predictions.")
    if confidence is not None and confidence < 65:
        notes.append("Prediction confidence is low, so manual review is recommended.")
    if not notes:
        notes.append("Input length and model confidence look suitable for this analysis.")

    return {
        "source": source,
        "word_count": word_count,
        "char_count": char_count,
        "reading_time": "Less than 1 min" if word_count < 220 else f"{math.ceil(word_count / 220)} min",
        "confidence_label": confidence_label,
        "signals": signals,
        "notes": notes,
    }


def has_date_or_time(text):
    patterns = [
        r"\b\d{1,2}:\d{2}\b",
        r"\b\d{1,2}\s?(am|pm)\b",
        r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b",
        r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b",
        r"\b(today|yesterday|minutes ago|hours ago|updated)\b",
    ]
    return any(re.search(pattern, text, re.IGNORECASE) for pattern in patterns)


def has_url_or_source(text):
    return bool(re.search(r"\b(https?://|www\.|\.com|\.in|\.org|\.net|source:|via\s+)\b", text, re.IGNORECASE))


def get_image_forensics(image_path):
    with Image.open(image_path) as image:
        width, height = image.size
        image_format = image.format or Path(image_path).suffix.lstrip(".").upper() or "Unknown"
        megapixels = round((width * height) / 1_000_000, 2)
        aspect_ratio = round(width / height, 2) if height else 0
        metadata_present = bool(image.getexif())

        sample = image.convert("RGB").resize((80, 80))
        pixels = list(sample.getdata())
        total = len(pixels) or 1
        green_pixels = sum(1 for r, g, b in pixels if g > 115 and g - r > 25 and g - b > 15)
        blue_pixels = sum(1 for r, g, b in pixels if b > 120 and b - r > 25 and b - g > 5)
        dark_pixels = sum(1 for r, g, b in pixels if r < 70 and g < 70 and b < 70)
        light_pixels = sum(1 for r, g, b in pixels if r > 210 and g > 210 and b > 210)

    return {
        "width": width,
        "height": height,
        "format": image_format,
        "megapixels": megapixels,
        "aspect_ratio": aspect_ratio,
        "metadata_present": metadata_present,
        "green_percent": round((green_pixels / total) * 100, 1),
        "blue_percent": round((blue_pixels / total) * 100, 1),
        "dark_percent": round((dark_pixels / total) * 100, 1),
        "light_percent": round((light_pixels / total) * 100, 1),
    }


def detect_screenshot_platform(text, image_info):
    text_lower = text.lower()
    scores = {
        "WhatsApp Chat": 0,
        "Twitter/X Post": 0,
        "News Screenshot": 0,
    }

    whatsapp_terms = ["whatsapp", "online", "typing", "forwarded", "end-to-end", "message", "voice message"]
    twitter_terms = ["twitter", "tweet", "retweeted", "reposted", "reply", "quote", "followers", "following", "likes"]
    news_terms = [
        "breaking", "exclusive", "news", "reported", "headline", "subscribe",
        "advertisement", "updated", "source", "live", "reuters", "edition",
        "volume", "issue", "business", "international", "newspaper", "article",
    ]

    scores["WhatsApp Chat"] += sum(2 for term in whatsapp_terms if term in text_lower)
    scores["Twitter/X Post"] += sum(2 for term in twitter_terms if term in text_lower)
    scores["News Screenshot"] += sum(2 for term in news_terms if term in text_lower)

    if image_info["green_percent"] > 4:
        scores["WhatsApp Chat"] += 2
    if image_info["blue_percent"] > 3:
        scores["Twitter/X Post"] += 1
    if image_info["aspect_ratio"] < 0.75:
        scores["WhatsApp Chat"] += 1
        scores["Twitter/X Post"] += 1
    if re.search(r"@\w+", text):
        scores["Twitter/X Post"] += 2
    if re.search(r"\b(reuters|associated press|pti|afp|ap news)\b", text_lower):
        scores["News Screenshot"] += 4
    if re.search(r"\b(volume|issue|edition|newspaper|page|p\d+)\b", text_lower):
        scores["News Screenshot"] += 3
    if has_url_or_source(text):
        scores["News Screenshot"] += 3

    platform, score = max(scores.items(), key=lambda item: item[1])
    if score < 2:
        return "Generic Screenshot", scores

    return platform, scores


def analyze_screenshot_forensics(image_path, extracted_text):
    text = extracted_text or ""
    text_lower = text.lower()
    image_info = get_image_forensics(image_path)
    platform, platform_scores = detect_screenshot_platform(text, image_info)
    words = re.findall(r"\b[\w'-]+\b", text)

    risk_score = 18
    checks = []
    red_flags = []

    if platform == "Generic Screenshot":
        risk_score += 16
        red_flags.append("Platform layout could not be confidently identified.")
    else:
        checks.append(f"Detected layout: {platform}.")

    if len(words) < 8:
        risk_score += 18
        red_flags.append("Very little readable text was found in the screenshot.")
    else:
        checks.append(f"OCR captured {len(words)} readable words.")

    if image_info["megapixels"] < 0.3:
        risk_score += 14
        red_flags.append("Low image resolution can hide editing artifacts.")
    else:
        checks.append(f"Image quality is {image_info['width']}x{image_info['height']} ({image_info['megapixels']} MP).")

    if not image_info["metadata_present"]:
        risk_score += 6
        checks.append("No embedded camera/app metadata found; this is common for screenshots but weakens provenance.")
    else:
        checks.append("Embedded metadata is present.")

    sensational_terms = [
        "secret", "expose", "shocking", "viral", "leaked", "conspiracy",
        "elite", "miracle", "banned", "before he died", "you won't believe",
    ]
    matched_terms = [term for term in sensational_terms if term in text_lower]
    if matched_terms:
        risk_score += min(len(matched_terms) * 7, 24)
        red_flags.append("Sensational wording detected: " + ", ".join(matched_terms[:4]) + ".")

    if platform == "WhatsApp Chat":
        if "forwarded" in text_lower:
            risk_score += 8
            red_flags.append("Forwarded-message language often appears in viral misinformation.")
        if not has_date_or_time(text):
            risk_score += 8
            red_flags.append("No clear chat timestamp was detected.")
        if not has_url_or_source(text):
            risk_score += 8
            red_flags.append("No verifiable source link appears in the chat text.")
    elif platform == "Twitter/X Post":
        if "@" not in text:
            risk_score += 12
            red_flags.append("No visible @ handle was detected.")
        if not has_date_or_time(text):
            risk_score += 8
            red_flags.append("No visible post date or time was detected.")
        if not re.search(r"\b(like|likes|view|views|reply|repost|retweeted)\b", text_lower):
            risk_score += 6
            red_flags.append("Normal post engagement labels were not detected.")
    elif platform == "News Screenshot":
        if not has_url_or_source(text):
            risk_score += 12
            red_flags.append("No visible publisher URL or source marker was detected.")
        if not has_date_or_time(text):
            risk_score += 8
            red_flags.append("No visible publish date or update time was detected.")
        if "breaking" in text_lower and len(words) < 16:
            risk_score += 8
            red_flags.append("Short breaking-news claims need external verification.")

    risk_score = max(0, min(100, risk_score))
    if risk_score >= 70:
        risk_level = "High Risk"
        risk_class = "high"
    elif risk_score >= 45:
        risk_level = "Medium Risk"
        risk_class = "medium"
    else:
        risk_level = "Low Risk"
        risk_class = "low"

    if not red_flags:
        red_flags.append("No major screenshot-specific warning signs were detected.")

    return {
        "platform": platform,
        "risk_level": risk_level,
        "risk_class": risk_class,
        "risk_score": risk_score,
        "checks": checks[:4],
        "red_flags": red_flags[:5],
        "image_info": image_info,
        "platform_scores": platform_scores,
    }


@app.route('/')
def home():
    return render_index()

@app.route("/predict", methods=["POST"])
def predict():
    selected_language = normalize_language(request.form.get("language", "en"))
    ui = get_ui_text(selected_language)
    typed_text = request.form.get("news", "").strip()
    news_text = typed_text
    extracted_text = ""
    uploaded_image = None
    image_type = None

    image = request.files.get("image")

    if image and image.filename != "":
        try:
            image_path, uploaded_image = save_uploaded_image(image)
            extracted_text = extract_text_from_image(image_path)
            image_info = get_image_forensics(image_path)
            platform, _platform_scores = detect_screenshot_platform(extracted_text, image_info)
            image_type = localize_image_type(platform, selected_language)
        except UnidentifiedImageError:
            return respond_index(
                error=ui["invalid_image"],
                uploaded_image=uploaded_image,
                selected_language=selected_language
            )
        except (pytesseract.TesseractNotFoundError, pytesseract.TesseractError, RuntimeError, subprocess.SubprocessError):
            return respond_index(
                error=ui["ocr_failed"],
                uploaded_image=uploaded_image,
                selected_language=selected_language
            )
        except Exception:
            app.logger.exception("Image OCR failed")
            return respond_index(
                error=ui["ocr_failed"],
                uploaded_image=uploaded_image,
                selected_language=selected_language
            )

        news_text = f"{news_text} {extracted_text}".strip()

        if not extracted_text.strip() and not typed_text:
            return respond_index(
                error=ui["ocr_failed"],
                uploaded_image=uploaded_image,
                selected_language=selected_language
            )

    if news_text.strip() == "":
        return respond_index(
            error=ui["missing_input"],
            uploaded_image=uploaded_image,
            selected_language=selected_language
        )

    text_vector = vectorizer.transform([news_text])
    prediction_raw = model.predict(text_vector)[0]
    confidence = get_model_confidence(text_vector, prediction_raw)

    if prediction_raw == 1:
        prediction = "Real News"
        display_prediction = ui["real_news"]
        result_class = "real"
    else:
        prediction = "Fake News"
        display_prediction = ui["fake_news"]
        result_class = "fake"

    reason = build_prediction_reason(news_text, text_vector, prediction_raw, selected_language)
    analysis_id = save_analysis_for_current_user(
        news_text=news_text,
        typed_text=typed_text,
        prediction=prediction,
        display_prediction=display_prediction,
        result_class=result_class,
        confidence=confidence,
        reason=reason,
        extracted_text=extracted_text,
        uploaded_image=uploaded_image,
        image_type=image_type,
        selected_language=selected_language,
    )
    report_url = url_for("download_report", analysis_id=analysis_id) if analysis_id else None

    return respond_index(
        prediction=prediction,
        display_prediction=display_prediction,
        result_class=result_class,
        confidence=confidence,
        reason=reason,
        extracted_text=extracted_text,
        uploaded_image=uploaded_image,
        image_type=image_type,
        analysis_id=analysis_id,
        report_url=report_url,
        selected_language=selected_language,
        ui=ui,
        show_result=True,
        clear_on_reload=True
    )

if __name__ == '__main__':
    app.run(host="0.0.0.0", port=5000,debug=True)
