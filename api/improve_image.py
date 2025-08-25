# api/improve_image.py  (route => /api/improve_image)
from http.server import BaseHTTPRequestHandler
import os, json, base64

from openai import OpenAI
client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

class handler(BaseHTTPRequestHandler):
    def _set_cors_headers(self):
        """Set CORS headers for all responses"""
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _error(self, code: int, msg: str):
        payload = json.dumps({"error": msg}).encode("utf-8")
        self.send_response(code)
        self.send_header("content-type", "application/json")
        self._set_cors_headers()
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        # Log the error for debugging
        print(f"ERROR {code}: {msg}")

    def do_OPTIONS(self):
        """Handle preflight requests"""
        self.send_response(200)
        self._set_cors_headers()
        self.end_headers()

    def do_GET(self):
        # Simple health/info
        print("GET request received")  # Debug log
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self._set_cors_headers()
        body = json.dumps({
            "ok": True, 
            "usage": "POST JSON with fields: prompt (text), image_url (URL) - generates improved images using GPT-4.1 Responses API",
            "example": {
                "prompt": "Make this photo look like a professional Instagram advertisement",
                "image_url": "https://example.com/image.jpg"
            }
        }).encode("utf-8")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        print("POST request received")  # Debug log
        
        # Check content type
        ctype = self.headers.get("content-type", "")
        print(f"Content-Type: {ctype}")  # Debug log
        
        if not ctype.startswith("application/json"):
            return self._error(415, "Use application/json with fields: prompt (text) and image_url (URL)")

        # Get content length and read request body
        try:
            content_length = int(self.headers.get('content-length', 0))
            print(f"Content-Length: {content_length}")  # Debug log
            post_data = self.rfile.read(content_length)
            print(f"Raw data: {post_data[:200]}...")  # Debug log (first 200 chars)
            data = json.loads(post_data.decode('utf-8'))
            print(f"Parsed JSON: {data}")  # Debug log
        except (ValueError, json.JSONDecodeError) as e:
            return self._error(400, f"Invalid JSON payload: {str(e)}")

        # Validate required fields
        if "prompt" not in data:
            return self._error(400, "Missing 'prompt' field")
        if "image_url" not in data:
            return self._error(400, "Missing 'image_url' field")

        prompt = data["prompt"]
        image_url = data["image_url"]
        print(f"Processing request - Prompt: {prompt[:50]}..., Image URL: {image_url}")

        try:
            # Build the multimodal input (text + image by URL) - same as Colab cell
            content = [{"type": "input_text", "text": prompt}]
            content.append({"type": "input_image", "image_url": image_url})
            print("Making OpenAI API call...")

            # Call the Responses API with the image_generation tool - same as Colab cell
            resp = client.responses.create(
                model="gpt-4.1",
                input=[{"role": "user", "content": content}],
                tools=[{"type": "image_generation"}],
            )
            print("OpenAI API call completed")

            # Extract base64 image(s) from the tool output - same as Colab cell
            image_b64_list = []
            for item in getattr(resp, "output", []) or []:
                # Newer SDKs return an "image_generation_call" with .result as base64
                if getattr(item, "type", None) == "image_generation_call" and hasattr(item, "result"):
                    image_b64_list.append(item.result)
                    print("Found image_generation_call with result")
                # In case the SDK returns image objects directly (future-proofing)
                elif getattr(item, "type", None) == "image" and hasattr(item, "image_base64"):
                    image_b64_list.append(item.image_base64)
                    print("Found direct image object")

            if not image_b64_list:
                print(f"No image found in response. Response structure: {resp}")
                return self._error(500, "No image found in response")

            # Decode the base64 image
            image_bytes = base64.b64decode(image_b64_list[0])
            print(f"Successfully generated image of {len(image_bytes)} bytes")

        except Exception as e:
            print(f"OpenAI API error: {str(e)}")  # Debug log
            return self._error(500, f"OpenAI error: {str(e)}")

        # Return the image bytes
        self.send_response(200)
        self.send_header("content-type", "image/png")
        self._set_cors_headers()
        self.send_header("content-length", str(len(image_bytes)))
        self.end_headers()
        self.wfile.write(image_bytes)
        print("Response sent successfully")
