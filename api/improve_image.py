# route: /api/improve_image
from http.server import BaseHTTPRequestHandler
import os, json, base64, logging, sys, io, re
from cgi import parse_header
from openai import OpenAI

logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
log = logging.getLogger("improve_image")

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

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

def _check_key():
    k = os.environ.get("OPENAI_API_KEY", "")
    if not k: return "OPENAI_API_KEY is not set"
    if not (k.startswith("sk-") or k.startswith("sk-proj-")):
        return "OPENAI_API_KEY format looks wrong"
    return ""

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
            "usage": "POST application/json with: { prompt: string, image_url: string }. Add ?format=json for base64."
        })

    def do_POST(self):
        # fail fast on key issues
        err = _check_key()
        if err:
            return send_json(self, 500, {"error": err})

        ctype_raw = self.headers.get("content-type", "")
        clen = int(self.headers.get("content-length", "0") or 0)
        main_type, _ = parse_header(ctype_raw)
        log.info("POST content-type=%r main=%r len=%d", ctype_raw, main_type, clen)

        body = self.rfile.read(clen) if clen else b"{}"
        if main_type != "application/json" and not body.strip().startswith(b"{"):
            return send_json(self, 415, {
                "error": "Use application/json with {prompt, image_url}",
                "got_content_type": ctype_raw
            })

        try:
            data = json.loads(body.decode("utf-8", "ignore") or "{}")
        except json.JSONDecodeError:
            return send_json(self, 400, {"error": "Invalid JSON payload"})

        prompt = (data.get("prompt") or "").strip()
        image_url = (data.get("image_url") or "").strip()
        size = (data.get("size") or "1024x1024").strip()
        if not prompt:     return send_json(self, 400, {"error": "Missing 'prompt'"})
        if not image_url:  return send_json(self, 400, {"error": "Missing 'image_url'"})

        # 1) Ask the Responses API to run the image_generation tool (force PNG)
        image_b64 = None
        try:
            resp = client.responses.create(
                model="gpt-4.1",
                input=[{
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": prompt},
                        {"type": "input_image", "image_url": image_url},
                    ],
                }],
                tools=[{
                    "type": "image_generation",
                    "output_format": "png",
                    "size": size,
                    "n": 1
                }],
                tool_choice={"type": "tool", "name": "image_generation"},
            )

            # extract base64 from the tool output
            for item in getattr(resp, "output", []) or []:
                t = getattr(item, "type", None)
                if t == "image_generation_call" and hasattr(item, "result"):
                    image_b64 = item.result
                    break
                if t == "image" and hasattr(item, "image_base64"):
                    image_b64 = item.image_base64
                    break

            if not image_b64:
                log.warning("No image in response; will fallback. Resp: %s", resp)
                raise RuntimeError("tool produced no image")

        except Exception as e:
            # 2) Fallback to Images API: generate-from-text using refs in prompt
            log.info("Falling back to Images API due to: %s", e)
            try:
                ref_text = f" Use this reference image: {image_url}"
                gen = client.images.generate(
                    model="gpt-image-1",
                    prompt=prompt + ref_text,
                    size=size,
                )
                image_b64 = gen.data[0].b64_json
            except Exception as e2:
                log.exception("Images API fallback failed")
                return send_json(self, 500, {"error": f"OpenAI error: {str(e2)}"})

        # 3) Clean + decode the base64 (strip data URL if present)
        mime_hint, payload_b64 = _strip_data_url(image_b64)
        try:
            out_bytes = _decode_image_b64(payload_b64)
        except Exception as de:
            log.exception("base64 decode failed")
            return send_json(self, 500, {"error": f"base64 decode failed: {de}"})

        # 4) Detect mime and return accordingly
        mime = mime_hint or _detect_mime(out_bytes)
        if mime == "application/octet-stream":
            # not a recognized image; return debug
            head = out_bytes[:16].hex()
            log.warning("Unrecognized image content. First 16 bytes: %s", head)
            return send_json(self, 500, {"error": "unrecognized image bytes", "head_hex": head})

        # optional JSON output for debugging
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        if (qs.get("format", ["png"])[0] or "png").lower() == "json":
            return send_json(self, 200, {
                "mime": mime,
                "image_base64": base64.b64encode(out_bytes).decode("utf-8")
            })

        self.send_response(200)
        self.send_header("content-type", mime)  # may be png/webp/jpeg
        self.send_header("content-length", str(len(out_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(out_bytes)
