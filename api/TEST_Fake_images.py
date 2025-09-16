# route: /api/image_generator
from http.server import BaseHTTPRequestHandler
import os, json, base64, logging, sys, io, re, random
from cgi import parse_header

logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
log = logging.getLogger("image_generator")

def send_json(self, code, obj):
    data = json.dumps(obj).encode("utf-8")
    self.send_response(code)
    self.send_header("content-type", "application/json")
    self.send_header("content-length", str(len(data)))
    self.send_header("Access-Control-Allow-Origin", "*")
    self.end_headers()
    self.wfile.write(data)

# --- helpers ---------------------------------------------------------------

DATA_URL_RE = re.compile(r"^data:(image/[^;]+);base64,(.+)$", re.IGNORECASE)

def _strip_data_url(b64: str):
    m = DATA_URL_RE.match(b64.strip())
    if m:
        return m.group(1).lower(), m.group(2)
    return None, b64

def _decode_image_b64(b64: str) -> bytes:
    # remove whitespace/newlines just in case
    return base64.b64decode("".join(b64.split()))

def _detect_mime(buf: bytes) -> str:
    # PNG
    if buf.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    # JPEG
    if buf.startswith(b"\xff\xd8") and buf.endswith(b"\xff\xd9"):
        return "image/jpeg"
    # WEBP (RIFF....WEBP)
    if len(buf) >= 12 and buf[0:4] == b"RIFF" and buf[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"

def parse_json_safe(text: str) -> dict:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    else:
        raise ValueError("No JSON found in AI output")

# --- STUB IMAGES (valid tiny PNGs) ----------------------------------------
# 1x1 transparent PNG
TRANSPARENT_PX_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMB/WRg3gAAAABJRU5ErkJggg=="
)
# 1x1 white PNG
WHITE_PX_PNG_B64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8AAAAMBAQF3iQm1AAAAAElFTkSuQmCC"
)

SAMPLE_PNGS = [TRANSPARENT_PX_PNG_B64, WHITE_PX_PNG_B64]

# --- STUBBED logic (no OpenAI calls) --------------------------------------

def _fake_describe_image(_: str) -> str:
    # minimal placeholder to keep structure
    return "Stubbed description for testing; no model call made."

def _fake_generate_creative_prompts(description: str, _: str, number_of_images: int) -> dict:
    # produce deterministic but varied prompts
    prompts = {}
    for i in range(1, number_of_images + 1):
        prompts[f"prompt{i}"] = f"[STUB] test variant #{i}: {description[:40]}"
    return prompts

def _fake_generate_images_from_prompts(prompts_json: dict, base64_image: str, number_of_images: int) -> dict:
    """
    Returns base64 images WITHOUT using OpenAI.
    Strategy:
      - 70% of the time echo back the uploaded image (proves client can render real images).
      - 30% of the time return a tiny built-in PNG (proves client can handle any valid base64 image).
    """
    images = {}
    for key in list(prompts_json.keys())[:number_of_images]:
        if random.random() < 0.7:
            # echo the input image (assume PNG when wrapping below; most viewers handle data URL regardless)
            images[key] = base64_image
        else:
            images[key] = random.choice(SAMPLE_PNGS)
    return images

# --- handler ---------------------------------------------------------------

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        send_json(self, 200, {
            "ok": True,
            "usage": "POST application/json with: { image_base64: string, number_of_images: number }. Returns base64 images WITHOUT calling OpenAI.",
            "description": "Stub mode for testing: echoes your image and/or tiny placeholder PNGs as base64 so you can test your pipeline without incurring costs."
        })

    def do_POST(self):
        # --- tolerant body read + parsing -----------------------------------
        ctype_raw = self.headers.get("content-type", "") or ""
        clen_raw  = self.headers.get("content-length", "") or ""
        try:
            clen = int(clen_raw) if clen_raw else 0
        except Exception:
            clen = 0

        main_type, _ = parse_header(ctype_raw)
        main_type = (main_type or "").lower()

        body = self.rfile.read(clen) if clen else b""
        raw = (body or b"").strip()

        # Accept JSON (with/without charset), or anything that starts with "{"
        is_jsonish = main_type.startswith("application/json") or raw.startswith(b"{")
        # Accept classic curl form posts too
        is_form    = main_type == "application/x-www-form-urlencoded"

        log.info("POST content-type=%r main=%r len=%d jsonish=%s form=%s",
                 ctype_raw, main_type, len(raw), is_jsonish, is_form)

        try:
            if is_form:
                from urllib.parse import parse_qs
                qs = parse_qs(raw.decode("utf-8", "ignore"))
                data = {k: v[0] for k, v in qs.items()}
            else:
                data = json.loads(raw.decode("utf-8", "ignore") or "{}")
        except Exception:
            return send_json(self, 400, {
                "error": "Invalid request body. Send JSON or x-www-form-urlencoded.",
                "got_content_type": ctype_raw,
                "body_preview": raw[:100].decode("utf-8", "ignore")
            })

        image_base64 = (data.get("image_base64") or "").strip()
        number_of_images = data.get("number_of_images", 2)

        if not image_base64:
            return send_json(self, 400, {"error": "Missing 'image_base64'"})

        # Validate number_of_images
        try:
            number_of_images = int(number_of_images)
            if number_of_images < 1 or number_of_images > 10:
                return send_json(self, 400, {"error": "number_of_images must be between 1 and 10"})
        except (ValueError, TypeError):
            return send_json(self, 400, {"error": "number_of_images must be a valid integer"})

        # Extract base64 from image_base64 (if it's a data URL, otherwise use as-is)
        mime_hint, base64_image = _strip_data_url(image_base64)
        if not base64_image:
            base64_image = image_base64

        try:
            # Validate base64 image data (so clients get early, clear errors)
            _decode_image_b64(base64_image)
        except Exception as e:
            return send_json(self, 400, {"error": f"Invalid base64 image data: {str(e)}"})

        try:
            # ---- STUB PIPELINE (no OpenAI) ---------------------------------
            log.info("STUB: Describing image (no API call)...")
            description = _fake_describe_image(base64_image)

            log.info("STUB: Generating prompts (no API call)...")
            prompts_json = _fake_generate_creative_prompts(description, base64_image, number_of_images)

            log.info("STUB: Generating images (no API call)...")
            generated_images = _fake_generate_images_from_prompts(prompts_json, base64_image, number_of_images)

            # Prepare response
            result = {
                "success": True,
                "generated_images": []
            }

            # Wrap as data URLs; prefer PNG hint for placeholders, else fall back to original hint or png
            mime = "image/png" if (mime_hint is None or mime_hint == "application/octet-stream") else mime_hint
            for key, image_b64 in generated_images.items():
                if image_b64:
                    result["generated_images"].append({
                        "prompt": prompts_json.get(key, ""),
                        "image": f"data:{mime};base64,{image_b64}"
                    })

            log.info(f"STUB: Returned {len(result['generated_images'])} images without external calls")
            return send_json(self, 200, result)

        except Exception as e:
            import traceback
            log.exception("Stub image generation failed")
            return send_json(self, 500, {
                "error": "Stub pipeline failed",
                "message": str(e),
                "trace": traceback.format_exc(),
            })
