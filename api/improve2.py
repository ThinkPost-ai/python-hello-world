
# # route: /api/improve2
# from http.server import BaseHTTPRequestHandler
# import os, json, base64, logging, sys, re
# from cgi import parse_header
# from urllib.request import urlopen, Request
# from urllib.error import URLError, HTTPError
# import urllib.request  # for callback POSTs
# from openai import OpenAI

# logging.basicConfig(stream=sys.stdout, level=logging.INFO, force=True)
# log = logging.getLogger("image_generator")

# client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# def send_json(self, code, obj):
#     data = json.dumps(obj).encode("utf-8")
#     self.send_response(code)
#     self.send_header("content-type", "application/json")
#     self.send_header("content-length", str(len(data)))
#     self.send_header("Access-Control-Allow-Origin", "*")
#     self.end_headers()
#     self.wfile.write(data)

# # --- helpers ---------------------------------------------------------------

# DATA_URL_RE = re.compile(r"^data:(image/[^;]+);base64,(.+)$", re.IGNORECASE)

# def _strip_data_url(b64: str):
#     m = DATA_URL_RE.match(b64.strip())
#     if m:
#         return m.group(1).lower(), m.group(2)
#     return None, b64

# def _decode_image_b64(b64: str) -> bytes:
#     return base64.b64decode("".join(b64.split()))

# def _detect_mime(buf: bytes) -> str:
#     if buf.startswith(b"\x89PNG\r\n\x1a\n"): return "image/png"
#     if buf.startswith(b"\xff\xd8"): return "image/jpeg"
#     if len(buf) >= 12 and buf[0:4] == b"RIFF" and buf[8:12] == b"WEBP": return "image/webp"
#     return "application/octet-stream"

# def _check_key():
#     k = os.environ.get("OPENAI_API_KEY", "")
#     if not k: return "OPENAI_API_KEY is not set"
#     if not (k.startswith("sk-") or k.startswith("sk-proj-")):
#         return "OPENAI_API_KEY format looks wrong"
#     return ""

# def _fetch_url_to_base64(url: str, timeout: int = 20):
#     req = Request(url, headers={"User-Agent": "image-generator/1.0"})
#     with urlopen(req, timeout=timeout) as resp:
#         mime = (resp.headers.get("Content-Type") or "image/jpeg").split(";")[0].lower()
#         data = resp.read()
#         if len(data) > 6 * 1024 * 1024:
#             raise ValueError("Image too large (limit 6MB)")
#         b64 = base64.b64encode(data).decode("ascii")
#         return mime, b64

# def parse_json_safe(text: str) -> dict:
#     match = re.search(r"\{.*\}", text, re.DOTALL)
#     if match:
#         return json.loads(match.group())
#     else:
#         raise ValueError("No JSON found in AI output")

# # --- OpenAI steps ----------------------------------------------------------

# IMAGE_DESCRIPTION_PROMPT_TEMPLATE = """ 
# Describe the image in detail, focus on the main subject in the image usually in the center, 
# extract all brand info like brand name and slogan if applicable. Make sure to include all details of the image.

# IMPORTANT: If there is Arabic text in the image:
# - Clearly identify that Arabic text is present
# - Note the direction and layout of the Arabic text
# - Describe the style and positioning of Arabic text elements
# - Mention if there's bilingual text (Arabic with other languages)
# - Preserve the exact appearance and positioning of Arabic script elements
# """

# def describe_image(base64_image: str, mime: str) -> str:
#     response = client.responses.create(
#         model="gpt-4.1",
#         input=[{
#             "role": "user",
#             "content": [
#                 {"type": "input_text", "text": IMAGE_DESCRIPTION_PROMPT_TEMPLATE},
#                 {"type": "input_image", "image_url": f"data:{mime};base64,{base64_image}"},
#             ],
#         }],
#     )
#     return response.output_text

# def generate_creative_prompts(description: str, base64_image: str, mime: str, number_of_images: int) -> dict:
#     creative_prompt_text = f"""
# Your task is to generate {number_of_images} prompt ideas for another image-image model for a given product image. 
# The product is usually in the center of the image. Focus on enhancing the product presentation and not changing the product itself.
# You can change the background, scene, composition, lighting, props, angle of view, or presentation style.
# Be creative and think outside the box.

# Here is the image description:

# {description}

# and the actual image.

# Return only JSON with exactly {number_of_images} keys: prompt1, prompt2, etc. (up to prompt{number_of_images}).
# """

#     chat_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
#     response = chat_client.chat.completions.create(
#         model="gpt-4o-mini",
#         messages=[
#             {"role": "system", "content": "You are a creative AI designer for marketing."},
#             {
#                 "role": "user",
#                 "content": [
#                     {"type": "text", "text": creative_prompt_text},
#                     {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{base64_image}"}}
#                 ]
#             }
#         ]
#     )
#     return parse_json_safe(response.choices[0].message.content)

# def generate_images_from_prompts(prompts_json: dict, base64_image: str, mime: str, description: str, number_of_images: int) -> dict:
#     images = {}
#     for key, prompt in list(prompts_json.items())[:number_of_images]:
#         try:
#             log.info(f"Generating image for {key}: {prompt}")
#             enhanced_text_prompt = f"""ORIGINAL IMAGE DESCRIPTION: {description}

# ENHANCEMENT IDEA: {prompt}

# STRICT INSTRUCTIONS:
# - Generate a new image that enhances the original product presentation
# - Generated image should be 9:16 aspect ratio, for TikTok.
# - PRESERVE the exact same product, brand name, packaging, and product identity from the original image
# - DO NOT change the product itself, its colors, shape, size, or branding
# - Only enhance: lighting, background, composition, props, angle of view, or presentation style
# - The product should remain clearly recognizable as the same item from the original image
# """
#             response = client.responses.create(
#                 model="gpt-4.1",
#                 input=[{
#                     "role": "user",
#                     "content": [
#                         {"type": "input_text", "text": enhanced_text_prompt},
#                         {"type": "input_image", "image_url": f"data:{mime};base64,{base64_image}"},
#                     ],
#                 }],
#                 tools=[{"type": "image_generation"}],
#             )
#             image_generation_calls = [o for o in response.output if getattr(o, "type", "") == "image_generation_call"]
#             if image_generation_calls:
#                 image_data = image_generation_calls[0].result
#                 images[key] = image_data
#             else:
#                 log.warning("No image generated for %s", key)
#                 images[key] = None
#         except Exception as e:
#             log.exception(f"Error generating image for {key}: {e}")
#             images[key] = None
#     return images

# # --- handler ---------------------------------------------------------------

# class handler(BaseHTTPRequestHandler):
#     def do_OPTIONS(self):
#         self.send_response(204)
#         self.send_header("Access-Control-Allow-Origin", "*")
#         self.send_header("Access-Control-Allow-Headers", "content-type")
#         self.send_header("Access-Control-Allow-Methods", "POST, GET, OPTIONS")
#         self.end_headers()

#     def do_GET(self):
#         send_json(self, 200, {
#             "ok": True,
#             "usage": "POST JSON: { image_url?: string, image_base64?: string, number_of_images?: number, callback_url?: string, product_id?: string }",
#             "description": "Pass image_url (recommended) or base64. Optionally include callback_url and product_id for async processing."
#         })

#     def do_POST(self):
#         err = _check_key()
#         if err:
#             return send_json(self, 500, {"error": err})

#         clen_raw = self.headers.get("content-length", "") or ""
#         try:
#             clen = int(clen_raw) if clen_raw else 0
#         except Exception:
#             clen = 0
#         body = self.rfile.read(clen) if clen else b""
#         raw = (body or b"").strip()

#         try:
#             data = json.loads(raw.decode("utf-8", "ignore") or "{}")
#         except Exception:
#             return send_json(self, 400, {"error": "Invalid JSON body"})

#         image_url       = (data.get("image_url") or "").strip()
#         image_base64_in = (data.get("image_base64") or "").strip()
#         number_of_images = data.get("number_of_images", 2)
#         callback_url    = (data.get("callback_url") or "").strip()  # NEW
#         product_id      = (data.get("product_id") or "").strip()    # NEW

#         try:
#             number_of_images = int(number_of_images)
#             if number_of_images < 1 or number_of_images > 10:
#                 return send_json(self, 400, {"error": "number_of_images must be between 1 and 10"})
#         except Exception:
#             return send_json(self, 400, {"error": "number_of_images must be a valid integer"})

#         mime, base64_image = None, None
#         if image_url and not image_base64_in:
#             try:
#                 mime, base64_image = _fetch_url_to_base64(image_url)
#                 log.info("Fetched image_url -> mime=%s, b64_len=%d", mime, len(base64_image))
#             except (HTTPError, URLError, ValueError) as e:
#                 return send_json(self, 400, {"error": f"Failed to fetch image_url: {str(e)}"})
#         elif image_base64_in:
#             mime, base64_image = _strip_data_url(image_base64_in)
#             if not base64_image:
#                 base64_image = image_base64_in
#             try:
#                 buf = _decode_image_b64(base64_image)
#             except Exception as e:
#                 return send_json(self, 400, {"error": f"Invalid base64 image data: {str(e)}"})
#             if not mime or mime == "application/octet-stream":
#                 mime = _detect_mime(buf)
#             if len(buf) > 6 * 1024 * 1024:
#                 return send_json(self, 400, {"error": "Image too large (limit 6MB)"})
#         else:
#             return send_json(self, 400, {"error": "Provide image_url or image_base64"})

#         try:
#             log.info("Step 1: Describing image...")
#             description = describe_image(base64_image, mime or "image/jpeg")
            
#             log.info("Step 2: Generating creative prompts...")
#             prompts_json = generate_creative_prompts(description, base64_image, mime or "image/jpeg", number_of_images)
            
#             log.info("Step 3: Generating images...")
#             generated_images = generate_images_from_prompts(
#                 prompts_json, base64_image, mime or "image/jpeg", description, number_of_images
#             )
            
#             result = {"success": True, "generated_images": []}
#             for key, image_b64 in generated_images.items():
#                 if image_b64:
#                     result["generated_images"].append({
#                         "prompt": prompts_json.get(key, ""),
#                         "image": f"data:image/png;base64,{image_b64}"
#                     })

#             # NEW: Callback handling
#             if callback_url and result["generated_images"]:
#                 log.info(f"Making callback to: {callback_url}")
#                 callback_data = {
#                     "product_id": product_id,
#                     "success": True,
#                     "generated_images": result["generated_images"]
#                 }
                
#                 try:
#                     # when building the callback request
#                     headers = {
#                         "Content-Type": "application/json",
#                         "Authorization": f"Bearer {os.environ['SUPABASE_ANON_KEY']}",  # <- add this env in Vercel
#                     }
#                     callback_req = urllib.request.Request(
#                         callback_url,
#                         data=json.dumps(callback_data).encode("utf-8"),
#                         headers=headers
#                     )
#                     with urllib.request.urlopen(callback_req, timeout=30) as callback_resp:
#                         log.info(f"Callback successful: HTTP {callback_resp.status}")
#                 except Exception as cb_err:
#                     log.error(f"Callback failed: {cb_err}")
#                     # Don't fail the main request if callback fails
            
#             log.info("Successfully generated %d images", len(result["generated_images"]))
#             return send_json(self, 200, result)
            
#         except Exception as e:
#             import traceback
#             log.exception("Image generation pipeline failed")
            
#             # NEW: Error callback
#             if callback_url:
#                 error_data = {
#                     "product_id": product_id,
#                     "success": False,
#                     "error": str(e)
#                 }
#                 try:
#                     callback_req = urllib.request.Request(
#                         callback_url,
#                         data=json.dumps(error_data).encode("utf-8"),
#                         headers={"Content-Type": "application/json"}
#                     )
#                     with urllib.request.urlopen(callback_req, timeout=30) as callback_resp:
#                         log.info(f"Error callback successful: HTTP {callback_resp.status}")
#                 except Exception as cb_err:
#                     log.error(f"Error callback failed: {cb_err}")
            
#             return send_json(self, 500, {
#                 "error": "Image generation pipeline failed",
#                 "message": str(e),
#                 "trace": traceback.format_exc(),
#             })

##########################################################################
# route: /api/improve2
# route: /api/improve2
from http.server import BaseHTTPRequestHandler
import os, json, base64, logging, sys, re, urllib.request
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

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
{
  "prompt1": "...",
  "prompt2": "...",
  ...
}
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
    log.info("Planner: generating %d prompts with gpt-4o-mini", k)
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "Return only valid JSON. No commentary."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PLANNER_PROMPT.format(k=k)},
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
            "usage": "POST JSON: { image_url?: string, image_base64?: string, number_of_images?: number, callback_url?: string, product_id?: string }",
            "hint": "Use image_url (public) for fastest performance."
        })

    def do_POST(self):
        # API key check
        err = _check_key()
        if err:
            return send_json(self, 500, {"error": err})

        # Parse body
        clen_raw = self.headers.get("content-length", "") or ""
        try:
            clen = int(clen_raw) if clen_raw else 0
        except Exception:
            clen = 0
        raw = (self.rfile.read(clen) if clen else b"").strip()
        try:
            data = json.loads(raw.decode("utf-8", "ignore") or "{}")
        except Exception:
            return send_json(self, 400, {"error": "Invalid JSON body"})

        image_url        = (data.get("image_url") or "").strip()
        image_base64_in  = (data.get("image_base64") or "").strip()
        callback_url     = (data.get("callback_url") or "").strip()    # <--- support callback
        product_id       = (data.get("product_id") or "").strip()      # <--- pass-through id
        try:
            number_of_images = int(data.get("number_of_images", 3))
        except Exception:
            return send_json(self, 400, {"error": "number_of_images must be an integer"})

        if number_of_images < 1 or number_of_images > 6:
            return send_json(self, 400, {"error": "number_of_images must be 1..6"})

        # Build a single canonical URL/DataURL string to feed both APIs
        url_or_dataurl = None

        if image_url:
            url_or_dataurl = image_url
        elif image_base64_in:
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
        else:
            return send_json(self, 400, {"error": "Provide image_url or image_base64"})

        try:
            # 1) Plan once with Chat Completions
            prompts_json = plan_prompts(url_or_dataurl, number_of_images)
            keys = sorted([k for k in prompts_json.keys() if k.lower().startswith("prompt")],
                          key=lambda x: int(re.sub(r"[^\d]", "", x) or "9999"))
            prompts = [prompts_json[k] for k in keys][:number_of_images]
            log.info("Planner returned %d prompts", len(prompts))

            # 2) Generate images in parallel
            results = []
            max_workers = min(4, max(1, number_of_images))
            with ThreadPoolExecutor(max_workers=max_workers) as ex:
                futures = {ex.submit(gen_one_image, p, url_or_dataurl): p for p in prompts}
                for fut in as_completed(futures):
                    p = futures[fut]
                    try:
                        img_b64 = fut.result()
                        results.append({"prompt": p, "image": f"data:image/png;base64,{img_b64}"})
                    except Exception as e:
                        log.error("Image gen failed for a prompt: %s", e)

            # 3) Optional: callback to Supabase with auth (so your EF is authorized)
            if callback_url and results:
                try:
                    headers = {
                        "Content-Type": "application/json",
                        # put this ENV in Vercel Project Settings
                        "Authorization": f"Bearer {os.environ.get('SUPABASE_ANON_KEY','')}",
                        "apikey": os.environ.get('SUPABASE_ANON_KEY',''),
                    }
                    payload = {
                        "product_id": product_id,
                        "success": True,
                        "generated_images": results,
                    }
                    req = urllib.request.Request(
                        callback_url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers=headers
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        log.info(f"Callback -> {callback_url} status={resp.status}")
                except Exception as cb_err:
                    log.error(f"Callback failed: {cb_err}")

            return send_json(self, 200, {"success": True, "generated_images": results})

        except Exception as e:
            import traceback
            log.exception("Fast pipeline failed")

            # If callback was provided, also send a failure notification
            if callback_url:
                try:
                    headers = {
                        "Content-Type": "application/json",
                        "Authorization": f"Bearer {os.environ.get('SUPABASE_ANON_KEY','')}",
                        "apikey": os.environ.get('SUPABASE_ANON_KEY',''),
                    }
                    payload = {"product_id": product_id, "success": False, "error": str(e)}
                    req = urllib.request.Request(
                        callback_url,
                        data=json.dumps(payload).encode("utf-8"),
                        headers=headers
                    )
                    with urllib.request.urlopen(req, timeout=30) as resp:
                        log.info(f"Error callback -> {callback_url} status={resp.status}")
                except Exception as cb_err:
                    log.error(f"Error callback failed: {cb_err}")

            return send_json(self, 500, {
                "error": "pipeline_failed",
                "message": str(e),
                "trace": traceback.format_exc(),
            })
