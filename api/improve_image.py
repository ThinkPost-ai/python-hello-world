# route: /api/improve_image  -- GPT-4.1 Responses + image_generation (no fallbacks)
from http.server import BaseHTTPRequestHandler
import os, json, base64, logging, sys, re
from cgi import parse_header
from urllib.parse import urlparse, parse_qs
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

DATA_URL_RE = re.compile(r"^data:(image/[^;]+);base64,(.+)$", re.IGNORECASE)

def _strip_data_url(b64: str):
    m = DATA_URL_RE.match(b64.strip())
    if m:
        return m.group(1).lower(), m.group(2)
    return None, b64

def _decode_image_b64(b64: str) -> bytes:
    return base64.b64decode("".join(b64.split()))

def _detect_mime(buf: bytes) -> str:
    if buf.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if buf.startswith(b"\xff\xd8") and buf.endswith(b"\xff\xd9"):
        return "image/jpeg"
    if len(buf) >= 12 and buf[:4] == b"RIFF" and buf[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"

def _check_key():
    k = os.environ.get("OPENAI_API_KEY", "")
    if not k: return "OPENAI_API_KEY is not set"
    if not (k.startswith("sk-") or k.startswith("sk-proj-")):
        return "OPENAI_API_KEY format looks wrong"
    return ""

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
            "usage": "POST application/json {prompt, image_url[, size][, force_tool]}  | add ?format=json to get base64"
        })

    def do_POST(self):
        err = _check_key()
        if err:
            return send_json(self, 500, {"error": err})

        ctype_raw = self.headers.get("content-type", "")
        main_type, _ = parse_header(ctype_raw)
        clen = int(self.headers.get("content-length", "0") or 0)
        body = self.rfile.read(clen) if clen else b"{}"
        if main_type != "application/json" and not body.strip().startswith(b"{"):
            return send_json(self, 415, {"error": "Use application/json with {prompt, image_url}"})

        try:
            data = json.loads(body.decode("utf-8", "ignore") or "{}")
        except json.JSONDecodeError:
            return send_json(self, 400, {"error": "Invalid JSON payload"})

        prompt      = (data.get("prompt") or "").strip()
        image_url   = (data.get("image_url") or "").strip()
        size        = (data.get("size") or "1024x1024").strip()
        force_tool  = bool(data.get("force_tool", True))  # force the image_generation tool

        if not prompt:    return send_json(self, 400, {"error": "Missing 'prompt'"})
        if not image_url: return send_json(self, 400, {"error": "Missing 'image_url'"})

        # Build input EXACTLY like your Colab (text + input_image by URL)
        content = [{"type": "input_text", "text": prompt},
                   {"type": "input_image", "image_url": image_url}]

        # Responses API with image_generation tool (no fallbacks, no gpt-image-1)
        kwargs = dict(
            model="gpt-4.1",
            input=[{"role": "user", "content": content}],
            tools=[{"type": "image_generation"}],  # keep minimal to mirror Colab behavior
        )
        if force_tool:
            kwargs["tool_choice"] = {"type": "tool", "name": "image_generation"}

        try:
            resp = client.responses.create(**kwargs)
        except Exception as e:
            return send_json(self, 500, {"error": f"OpenAI error: {str(e)}"})

        # Extract base64 image(s)
        image_b64 = None
        for item in getattr(resp, "output", []) or []:
            t = getattr(item, "type", None)
            if t == "image_generation_call" and hasattr(item, "result"):
                image_b64 = item.result
                break
            if t == "image" and hasattr(item, "image_base64"):
                image_b64 = item.image_base64
                break

        if not image_b64:
            # return the model's text if it chose to speak instead of generate
            txt = getattr(resp, "output", []) or []
            return send_json(self, 200, {"note": "no image in tool output; returning raw message", "raw": repr(txt)})

        # Clean and decode (supports data:image/...;base64, prefix)
        mime_hint, payload = _strip_data_url(image_b64)
        try:
            out_bytes = _decode_image_b64(payload)
        except Exception as de:
            return send_json(self, 500, {"error": f"base64 decode failed: {de}"})

        mime = mime_hint or _detect_mime(out_bytes)

        # Optional JSON return
        qs = parse_qs(urlparse(self.path).query)
        if (qs.get("format", ["png"])[0] or "png").lower() == "json":
            return send_json(self, 200, {
                "mime": mime,
                "image_base64": base64.b64encode(out_bytes).decode("utf-8"),
                "source": "responses.image_generation.gpt-4.1"
            })

        self.send_response(200)
        self.send_header("content-type", mime or "image/png")
        self.send_header("x-source", "responses.image_generation.gpt-4.1")
        self.send_header("content-length", str(len(out_bytes)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(out_bytes)
