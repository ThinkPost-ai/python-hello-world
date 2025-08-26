# route: /api/improve_image
from http.server import BaseHTTPRequestHandler
import os, json, base64, logging, sys, io, urllib.request
from cgi import parse_header
from openai import OpenAI

logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
log = logging.getLogger("improve_image")

MAX_BYTES = 4_300_000  # stay under Vercel's ~4.5MB body cap
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def send_json(self, code, obj):
    data = json.dumps(obj).encode("utf-8")
    self.send_response(code)
    self.send_header("content-type", "application/json")
    self.send_header("content-length", str(len(data)))
    self.send_header("Access-Control-Allow-Origin", "*")
    self.end_headers()
    self.wfile.write(data)

def _mask(k: str) -> str:
    return (k[:4] + "…" + k[-4:]) if k else ""

def _check_key():
    k = os.environ.get("OPENAI_API_KEY", "")
    if not k:
        return "OPENAI_API_KEY is not set"
    if not (k.startswith("sk-") or k.startswith("sk-proj-")):
        return "OPENAI_API_KEY format looks wrong"
    return ""

def _fetch_image_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "curl/8"})
    with urllib.request.urlopen(req, timeout=20) as r:
        if r.status != 200:
            raise ValueError(f"fetch failed: HTTP {r.status}")
        ct = r.headers.get("Content-Type", "")
        if not (ct.startswith("image/") or ct == "application/octet-stream"):
            raise ValueError(f"not an image content-type: {ct}")
        data = r.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("image too large for serverless limit (~4.5MB)")
        return data

class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        # Add ?diag=1 to quickly verify env + key at runtime
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(self.path).query)
        if qs.get("diag", ["0"])[0] == "1":
            env = os.environ.get("VERCEL_ENV", "unknown")  # preview | production | development
            k = os.environ.get("OPENAI_API_KEY", "")
            return send_json(self, 200, {
                "vercel_env": env,
                "has_key": bool(k),
                "key_masked": _mask(k),
                "key_check": _check_key() or "looks_ok",
                "usage": "POST application/json {prompt, image_url}"
            })
        return send_json(self, 200, {"ok": True, "usage": "POST application/json {prompt, image_url}"})

    def do_POST(self):
        # fail-fast if key is missing/wrong format (prevents long waits)
        err = _check_key()
        if err:
            return send_json(self, 500, {"error": err})

        ctype_raw = self.headers.get("content-type", "")
        main_type, _ = parse_header(ctype_raw)
        clen = int(self.headers.get("content-length", "0") or 0)
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
        if not prompt:
            return send_json(self, 400, {"error": "Missing 'prompt'"})
        if not image_url:
            return send_json(self, 400, {"error": "Missing 'image_url'"})

        # 1) Download the image
        try:
            img_bytes = _fetch_image_bytes(image_url)
        except Exception as e:
            log.warning("fetch error: %r", e)
            return send_json(self, 400, {"error": f"Could not fetch image_url: {e}"})

        # 2) Edit with Images API (faster + predictable)
        bio = io.BytesIO(img_bytes)
        bio.name = "input.png"
        try:
            result = client.images.edit(
                model="gpt-image-1",
                image=bio,
                prompt=prompt,
                size="1024x1024"
            )
            b64 = result.data[0].b64_json
            out_png = base64.b64decode(b64)
        except Exception as e:
            log.exception("OpenAI images.edit failed")
            return send_json(self, 500, {"error": f"OpenAI error: {str(e)}"})

        self.send_response(200)
        self.send_header("content-type", "image/png")
        self.send_header("content-length", str(len(out_png)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(out_png)
