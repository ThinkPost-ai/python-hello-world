# api/improve_image.py  -> route: /api/improve_image
from http.server import BaseHTTPRequestHandler
import os, json, base64, logging, sys
from cgi import parse_header

from openai import OpenAI
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
log = logging.getLogger("improve_image")

def send_json(self, code, obj):
    data = json.dumps(obj).encode("utf-8")
    self.send_response(code)
    self.send_header("content-type", "application/json")
    self.send_header("content-length", str(len(data)))
    # (Optional CORS; safe to leave for server-side callers too)
    self.send_header("Access-Control-Allow-Origin", "*")
    self.end_headers()
    self.wfile.write(data)

class handler(BaseHTTPRequestHandler):
    # For browser preflight (not required for curl, but handy)
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
        self.end_headers()

    def do_GET(self):
        log.info("GET /api/improve_image")
        send_json(self, 200, {
            "ok": True,
            "usage": "POST application/json with: { prompt: string, image_url: string }"
        })

    def do_POST(self):
        ctype_raw = self.headers.get("content-type", "")
        clen = int(self.headers.get("content-length", "0") or 0)
        main_type, _ = parse_header(ctype_raw)
        log.info("POST /api/improve_image content-type=%r (main=%r) len=%d",
                 ctype_raw, main_type, clen)

        # Read the body once
        body_bytes = self.rfile.read(clen) if clen else b""
        body_text = body_bytes.decode("utf-8", "ignore").strip()

        # Accept JSON if either:
        #  - main_type == application/json
        #  - or body "looks like" JSON (starts with {)
        if main_type != "application/json" and not body_text.startswith("{"):
            return send_json(self, 415, {
                "error": "Unsupported Media Type. Use application/json with {prompt, image_url}.",
                "got_content_type": ctype_raw
            })

        try:
            data = json.loads(body_text or "{}")
        except json.JSONDecodeError:
            return send_json(self, 400, {"error": "Invalid JSON payload"})

        # Validate
        prompt = data.get("prompt")
        image_url = data.get("image_url")
        if not prompt:
            return send_json(self, 400, {"error": "Missing 'prompt'"})
        if not image_url:
            return send_json(self, 400, {"error": "Missing 'image_url'"})

        # Build multimodal content
        content = [
            {"type": "input_text", "text": prompt},
            {"type": "input_image", "image_url": image_url},
        ]

        try:
            # Ask the Responses API to use the image_generation tool
            resp = client.responses.create(
                model="gpt-4.1",
                input=[{"role": "user", "content": content}],
                tools=[{"type": "image_generation"}],
                # You can force the tool if needed:
                # tool_choice={"type":"tool","name":"image_generation"},
            )

            # Extract base64 image
            image_b64 = None
            for item in getattr(resp, "output", []) or []:
                if getattr(item, "type", None) == "image_generation_call" and hasattr(item, "result"):
                    image_b64 = item.result
                    break
                if getattr(item, "type", None) == "image" and hasattr(item, "image_base64"):
                    image_b64 = item.image_base64
                    break

            if not image_b64:
                # Helpful debug output in logs
                log.warning("No image in response. Full resp: %s", resp)
                return send_json(self, 500, {"error": "No image found in response"})

            image_bytes = base64.b64decode(image_b64)

            self.send_response(200)
            self.send_header("content-type", "image/png")
            self.send_header("content-length", str(len(image_bytes)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(image_bytes)

        except Exception as e:
            log.exception("OpenAI call failed")
            return send_json(self, 500, {"error": f"OpenAI error: {str(e)}"})