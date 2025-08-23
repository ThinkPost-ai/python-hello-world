# api/edit.py  (route => /api/edit)
from http.server import BaseHTTPRequestHandler
import os, json, base64, io, cgi

from openai import OpenAI
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

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
        body = json.dumps({"ok": True, "usage": "POST multipart/form-data with field: prompt (text) - generates images using GPT-4.1"}).encode("utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        ctype = self.headers.get("content-type", "")
        if not ctype.startswith("multipart/form-data"):
            return self._error(415, "Use multipart/form-data with field: prompt (text)")

        # Parse multipart form
        form = cgi.FieldStorage(
            fp=self.rfile,
            headers=self.headers,
            environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype},
        )
        # GPT-4.1 generates images from text prompts only

        # Grab prompt (image is optional now since GPT-4.1 generates from text)
        if "prompt" not in form:
            return self._error(400, "Missing 'prompt' field")

        prompt = form["prompt"].value

        try:
            # Use GPT-4.1 with responses.create for image generation
            stream = client.responses.create(
                model="gpt-4.1",
                input=prompt,
                stream=True,
                tools=[{"type": "image_generation", "partial_images": 1}],
            )
            
            # Collect the generated image from the stream
            out_bytes = None
            for event in stream:
                if event.type == "response.image_generation_call.partial_image":
                    image_base64 = event.partial_image_b64
                    out_bytes = base64.b64decode(image_base64)
                    break  # Take the first generated image
            
            if out_bytes is None:
                return self._error(500, "No image generated from GPT-4.1")
                
        except Exception as e:
            return self._error(500, f"OpenAI error: {str(e)}")

        # Return the image bytes
        self.send_response(200)
        self.send_header("content-type", "image/png")
        self.send_header("content-length", str(len(out_bytes)))
        self.end_headers()
        self.wfile.write(out_bytes)
