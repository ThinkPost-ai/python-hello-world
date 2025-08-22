# api/edit.py  (route => /api/edit)
from http.server import BaseHTTPRequestHandler
import os, json, base64, io, cgi

from openai import OpenAI
client = OpenAI(api_key=os.environ.get("6fe1f651f3cf42841ecbb901f9bf2f4c873f626110506207066ff6cf71157e31"))

MAX_BYTES = 4_300_000  # keep under Vercel's 4.5MB body cap

class handler(BaseHTTPRequestHandler):
    def _error(self, code: int, msg: str):
        payload = json.dumps({"error": msg}).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        # Simple health/info
        self.send_response(200)
        self.send_header("content-type", "application/json")
        body = json.dumps({"ok": True, "usage": "POST multipart/form-data with fields: image (file), prompt (text)"}).encode("utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        ctype = self.headers.get("content-type", "")
        if not ctype.startswith("multipart/form-data"):
            return self._error(415, "Use multipart/form-data with fields: image (file) and prompt (text)")

        # Parse multipart form
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype},
        )

        # Grab prompt + image
        if "prompt" not in form or "image" not in form:
            return self._error(400, "Missing 'prompt' or 'image' field")

        prompt = form["prompt"].value
        fileitem = form["image"]

        try:
            img_bytes = fileitem.file.read()
        except Exception:
            return self._error(400, "Could not read uploaded file")

        if not img_bytes:
            return self._error(400, "Empty image file")
        if len(img_bytes) > MAX_BYTES:
            return self._error(413, "Image too large for serverless payload limit (~4.5MB)")

        # Wrap bytes as file-like object for OpenAI Images Edit
        bio = io.BytesIO(img_bytes)
        bio.name = fileitem.filename or "upload.png"  # name hint helps the SDK

        try:
            # Use GPT Image edit with your prompt + the uploaded image
            # (You can add size="1024x1024" or other options as needed)
            result = client.images.edit(
                model="gpt-image-1",
                image=bio,
                prompt=prompt,
                size="1024x1024"
            )
            b64 = result.data[0].b64_json
            out_bytes = base64.b64decode(b64)
        except Exception as e:
            return self._error(500, f"OpenAI error: {str(e)}")

        # Return the image bytes
        self.send_response(200)
        self.send_header("content-type", "image/png")
        self.send_header("content-length", str(len(out_bytes)))
        self.end_headers()
        self.wfile.write(out_bytes)
