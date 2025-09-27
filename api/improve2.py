from http.server import BaseHTTPRequestHandler
import os, json, base64, logging, sys, re
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed
import urllib.request  # used for callback POST
from openai import OpenAI
from PIL import Image
import io


logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
log = logging.getLogger("image_generator")

client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

def send_json(self, code, obj):
    data = json.dumps(obj).encode("utf-8")
    self.send_response(code)
    self.send_header("content-type", "application/json")
    self.send_header("content-length", str(len(data)))
    self.send_header("Access-Control-Allow-Origin", "*")
    self.end_headers()
    self.wfile.write(data)

# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def image_post_process(image_b64: str) -> str:
    """Image post processing (force 24-bit RGB PNG)."""
    # If the string has a data URL prefix, strip it
    if image_b64.startswith("data:"):
        _, b64data = image_b64.split(",", 1)
    else:
        b64data = image_b64

    img_bytes = base64.b64decode(b64data)
    with Image.open(io.BytesIO(img_bytes)) as img:
        log.info(f"Pre-conversion mode: {img.mode}")  # <-- useful for debugging
        rgb_img = img.convert("RGB")  # Force 24-bit
        log.info(f"Post-conversion mode: {rgb_img.mode}")
        buf = io.BytesIO()
        rgb_img.save(buf, format="PNG")
        return base64.b64encode(buf.getvalue()).decode("utf-8")



DATA_URL_RE = re.compile(r"^data:(image/[^;]+);base64,(.+)$", re.IGNORECASE)

def _strip_data_url(b64: str):
    m = DATA_URL_RE.match(b64.strip())
    if m:
        return m.group(1).lower(), m.group(2)
    return None, b64

def _decode_image_b64(b64: str) -> bytes:
    return base64.b64decode("".join(b64.split()))

def _detect_mime(buf: bytes) -> str:
    if buf.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
    if buf.startswith(b"\xff\xd8"): return "image/jpeg"
    if len(buf) >= 12 and buf[0:4] == b"RIFF" and buf[8:12] == b"WEBP": return "image/webp"
    return "application/octet-stream"

def _check_key():
    k = os.environ.get("OPENAI_API_KEY", "")
    if not k: return "OPENAI_API_KEY is not set"
    if not (k.startswith("sk-") or k.startswith("sk-proj-")):
        return "OPENAI_API_KEY format looks wrong"
    return ""

def _read_body(self) -> bytes:
    """
    Read request body supporting both Content-Length and
    Transfer-Encoding: chunked (used by Deno fetch).
    """
    if (self.headers.get("transfer-encoding", "").lower() == "chunked"):
        buf = b""
        while True:
            size_line = self.rfile.readline()
            if not size_line:
                break
            size_line = size_line.strip()
            if not size_line:
                continue
            try:
                size = int(size_line, 16)
            except Exception:
                break
            if size == 0:
                self.rfile.readline()  # trailing CRLF
                break
            chunk = self.rfile.read(size)
            buf += chunk
            self.rfile.read(2)  # CRLF
        return buf
    try:
        clen = int(self.headers.get("content-length", "0") or "0")
    except Exception:
        clen = 0
    return self.rfile.read(clen) if clen > 0 else b""

def parse_json_safe(text: str) -> dict:
    if not text:
        raise ValueError("Empty model output")
    m = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL | re.IGNORECASE)
    if m: return json.loads(m.group(1))
    m = re.search(r"```\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m: return json.loads(m.group(1))
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if m: return json.loads(m.group())
    raise ValueError("No JSON found in AI output")

# --------------------------------------------------------------------------
# Planner (Chat Completions) + Generator (Responses API)
# --------------------------------------------------------------------------

PLANNER_PROMPT = """
You are a creative ad art director.
Given the reference product image (see attached), produce EXACTLY {k} diverse, high-impact enhancement ideas as JSON:
{{
  "prompt1": "...",
  "prompt2": "...",
  "prompt3": "..."
}}
Rules:
- Keep the same product identity/packaging; change only scene/lighting/props/composition/angle.
- Prefer short, concrete directions (background, lighting, props, vibe).
- Include 9:16 composition guidance if relevant.
- Return ONLY valid JSON. No commentary.
"""

def to_chat_image_content(url_or_data_url: str) -> dict:
    return {"type": "image_url", "image_url": {"url": url_or_data_url}}

def to_responses_image_content(url_or_data_url: str) -> dict:
    return {"type": "input_image", "image_url": url_or_data_url}

def plan_prompts(image_url_or_dataurl: str, k: int) -> dict:
    prompt_text = PLANNER_PROMPT.format(k=k)
    log.info("Planner: generating %d prompts with gpt-4o-mini", k)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Return only valid JSON. No commentary."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    to_chat_image_content(image_url_or_dataurl)
                ],
            },
        ],
        temperature=0.7,
    )
    return parse_json_safe(resp.choices[0].message.content)

def gen_one_image(prompt: str, image_url_or_dataurl: str) -> str:
    enhanced_text_prompt = f"""ENHANCEMENT IDEA: {prompt}

STRICT:
- Enhance only scene/lighting/props/composition/angle.
- Keep product/branding unchanged and readable.
- Output 9:16 aspect suitable for TikTok."""
    r = client.responses.create(
        model="gpt-4.1",
        input=[{
            "role": "user",
            "content": [
                {"type": "input_text", "text": enhanced_text_prompt},
                to_responses_image_content(image_url_or_dataurl),
            ],
        }],
        tools=[{"type": "image_generation"}],
    )
    calls = [o for o in r.output if getattr(o, "type", "") == "image_generation_call"]
    if not calls:
        raise RuntimeError("image_generation_call missing")
    return calls[0].result  # base64 PNG

# --------------------------------------------------------------------------
# HTTP handler
# --------------------------------------------------------------------------

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
            "usage": "POST JSON: { image_url?, image_base64?, number_of_images?, callback_url?, product_id?, auth_token?, user_id?, product_name?, product_price?, product_description?, original_image_path? }",
            "hint": "Use image_url (public) for fastest performance."
        })

    def do_POST(self):
        err = _check_key()
        if err:
            return send_json(self, 500, {"error": err})

        # Read body (supports chunked)
        raw = (_read_body(self) or b"").strip()
        try:
            data = json.loads(raw.decode("utf-8", "ignore") or "{}")
        except Exception:
            log.error(f"Invalid JSON body; headers={dict(self.headers)}, first200={raw[:200]!r}")
            return send_json(self, 400, {"error": "Invalid JSON body"})

        image_url        = (data.get("image_url") or "").strip()
        image_base64_in  = (data.get("image_base64") or "").strip()
        callback_url     = (data.get("callback_url") or "").strip()
        product_id       = (data.get("product_id") or "").strip()
        user_auth_token  = (data.get("auth_token") or "").strip()

        # ✨ NEW: extra fields to pass through to Supabase callback
        user_id               = (data.get("user_id") or "").strip()
        product_name          = (data.get("product_name") or "").strip()
        product_price         = data.get("product_price")
        product_description   = (data.get("product_description") or "").strip()
        original_image_path   = (data.get("original_image_path") or "").strip()

        try:
            number_of_images = int(data.get("number_of_images", 3))
        except Exception:
            return send_json(self, 400, {"error": "number_of_images must be an integer"})
        if number_of_images < 1 or number_of_images > 6:
            return send_json(self, 400, {"error": "number_of_images must be 1..6"})

        if not image_url and not image_base64_in:
            return send_json(self, 400, {"error": "Provide image_url or image_base64"})

        # Build canonical url/dataURL
        if image_url:
            url_or_dataurl = image_url
        else:
            mime, base64_image = _strip_data_url(image_base64_in)
            if not base64_image:
                base64_image = image_base64_in
            try:
                buf = _decode_image_b64(base64_image)
            except Exception as e:
                return send_json(self, 400, {"error": f"Invalid base64 image data: {str(e)}"})
            if len(buf) > 6 * 1024 * 1024:
                return send_json(self, 400, {"error": "Image too large (limit 6MB)"})
            if not mime or mime == "application/octet-stream":
                mime = _detect_mime(buf) or "image/jpeg"
            url_or_dataurl = f"data:{mime};base64,{base64_image}"

        try:
            # 1) get prompts
            prompts_json = plan_prompts(url_or_dataurl, number_of_images)
            keys = sorted(
                [k for k in prompts_json.keys() if k.lower().startswith("prompt")],
                key=lambda x: int(re.sub(r"[^\d]", "", x) or "9999")
            )
            prompts = [prompts_json[k] for k in keys][:number_of_images]
            log.info("Planner returned %d prompts", len(prompts))

            # 2) generate images in parallel
            results = []
            max_workers = min(4, max(1, number_of_images))
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(gen_one_image, p, url_or_dataurl): p for p in prompts}
                for fut in as_completed(futures):
                    p = futures[fut]
                    try:
                        img_b64 = fut.result()
                        try:
                            # Force 24-bit RGB before returning
                            img_b64_rgb = image_post_process(img_b64)
                        except Exception as conv_err:
                            log.warning(f"Failed to convert image to 24-bit: {conv_err}")
                            img_b64_rgb = None

                        results.append({"prompt": p, "image": f"data:image/png;base64,{img_b64_rgb}"})
                    except Exception as e:
                        log.error("Image gen failed for a prompt: %s", e)

            # 3) callback with results (if provided)
            if callback_url and results:
                try:
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {os.environ.get('SUPABASE_ANON_KEY', '')}",
                        "apikey": os.environ.get("SUPABASE_ANON_KEY", ""),
                    }
                    payload = {
                        "product_id": product_id,
                        "success": True,
                        "generated_images": results,
                        # pass-through fields
                        "user_id": user_id,
                        "product_name": product_name,
                        "product_price": product_price,
                        "product_description": product_description,
                        "original_image_path": original_image_path,
                        "auth_token": user_auth_token,
                    }
                    req = urllib.request.Request(
                        callback_url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers=headers,
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        body = resp.read().decode("utf-8", "ignore")
                        log.info(f"Callback -> {callback_url} status={resp.status} body={body[:500]}")
                except Exception as e:
                    log.error(f"Callback failed: {e}")

            return send_json(self, 200, {"success": True, "generated_images": results})

        except Exception as e:
            import traceback
            log.exception("Fast pipeline failed")
            # Send error callback too (best-effort) with the same pass-through fields
            if callback_url:
                try:
                    req = urllib.request.Request(
                        callback_url,
                        data=json.dumps({
                            "product_id": product_id,
                            "success": False,
                            "error": str(e),
                            "user_id": user_id,
                            "product_name": product_name,
                            "product_price": product_price,
                            "product_description": product_description,
                            "original_image_path": original_image_path,
                            "auth_token": user_auth_token
                        }).encode("utf-8"),
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {os.environ.get('SUPABASE_ANON_KEY', '')}",
                            "apikey": os.environ.get("SUPABASE_ANON_KEY", ""),
                        }
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        log.info("Error callback -> %s status=%d", callback_url, resp.status)
                except Exception as cb_err:
                    log.error("Error callback failed: %s", cb_err)

            return send_json(self, 500, {
                "error": "pipeline_failed",
                "message": str(e),
                "trace": traceback.format_exc(),
            })
