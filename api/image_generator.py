# route: /api/image_generator
from http.server import BaseHTTPRequestHandler
import os, json, base64, logging, sys, io, re
from cgi import parse_header
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

# --- helpers ---------------------------------------------------------------
def image_post_process(image_b64: str) -> str:
    # Decode base64 → bytes
    img_bytes = base64.b64decode(image_b64)
    
    # Open with Pillow
    with Image.open(io.BytesIO(img_bytes)) as img:
        # Convert to 24-bit RGB
        rgb_img = img.convert("RGB")
        # TODO: crop the image to 9:16 aspect ratio in center of the image
        # rgb_img = rgb_img.crop((0, 0, 1080, 1920))

        
        buf = io.BytesIO()
        rgb_img.save(buf, format="PNG")  # keep PNG
        rgb_bytes = buf.getvalue()
    
    # Encode back to base64
    return base64.b64encode(rgb_bytes).decode("utf-8")

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

def parse_json_safe(text: str) -> dict:
    # Extract {...} from the string
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        return json.loads(match.group())
    else:
        raise ValueError("No JSON found in AI output")

# --- Image processing functions (adapted from agentic.py) ---

# Step 1: Image -> Description
IMAGE_DESCRIPTION_PROMPT_TEMPLATE = """ 
Describe the image in detail, focus on the main subject in the image usually in the center, 
extract all brand info like brand name and slogan if applicable. Make sure to include all details of the image.

IMPORTANT: If there is Arabic text in the image:
- Clearly identify that Arabic text is present
- Note the direction and layout of the Arabic text
- Describe the style and positioning of Arabic text elements
- Mention if there's bilingual text (Arabic with other languages)
- Preserve the exact appearance and positioning of Arabic script elements
"""

def describe_image(base64_image: str) -> str:
    response = client.responses.create(
        model="gpt-4.1",  # vision-capable
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": IMAGE_DESCRIPTION_PROMPT_TEMPLATE},
                    {"type": "input_image", "image_url": f"data:image/jpeg;base64,{base64_image}"},
                ],
            }
        ],
    )
    return response.output_text

# Step 2: Description + Image -> Creative Prompts
def generate_creative_prompts(description: str, base64_image: str, number_of_images: int) -> dict:
    creative_prompt_text = f"""
Your task is to generate {number_of_images} prompt ideas for another image-image model for a given product image. 
The product is usually in the center of the image. Focus on ehnancing the product presentation and not changing the product itself.
You can change the background, scene, composition, lighting, props, angel of view, or presentation style.
Be creative and think outside the box.

Here is the image description:

{description}

and the actual image.

Return only JSON with exactly {number_of_images} keys: prompt1, prompt2, etc. (up to prompt{number_of_images}).

Start generate impressive ideas:
"""

    # Use OpenAI Chat API for creative prompts
    chat_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    
    response = chat_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a creative AI designer for marketing."},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": creative_prompt_text},
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}
                ]
            }
        ]
    )
    
    return parse_json_safe(response.choices[0].message.content)

# Step 3: Prompts -> Images using GPT-4.1 with original image
def generate_images_from_prompts(prompts_json: dict, base64_image: str, description: str, number_of_images: int) -> dict:
    images = {}
    
    for key, prompt in list(prompts_json.items())[:number_of_images]:
        try:
            log.info(f"Generating image for {key}: {prompt}")
            enhanced_text_prompt = f"""ORIGINAL IMAGE DESCRIPTION: {description}

ENHANCEMENT IDEA: {prompt}

STRICT INSTRUCTIONS:
- Generate a new image that enhances the original product presentation
- Generated image should be 9:16 aspect ratio, for TikTok.
- PRESERVE the exact same product, brand name, packaging, and product identity from the original image
- DO NOT change the product itself, its colors, shape, size, or branding
- Only enhance: lighting, background, composition, props, angel of view, or presentation style
- The product should remain clearly recognizable as the same item from the original image

CRITICAL TEXT HANDLING INSTRUCTIONS:
- Maintain all text, logos, and brand elements exactly as they appear in the original
- For Arabic text specifically:
* Arabic text MUST be written RIGHT-TO-LEFT (RTL direction)
* Arabic letters MUST connect properly and maintain correct letterforms
* Preserve Arabic script integrity with proper letter shapes (initial, medial, final, isolated forms)
* Keep Arabic text spacing and alignment consistent with original
* Arabic numerals should follow the correct Arabic-Indic numeral system if used in original
* Do NOT mirror or flip Arabic text - maintain proper RTL reading direction
* Ensure Arabic diacritics (tashkeel) are preserved if present in original
- For any bilingual text (Arabic + English/Latin), maintain the correct direction for each script
- Text should appear natural and readable and match exactly the original text, not distorted or backwards"""

            
            # Use GPT-4.1 with image generation tools, including original image
            response = client.responses.create(
                model="gpt-4.1",
                input=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "input_text", "text": enhanced_text_prompt},
                            {
                                "type": "input_image",
                                "image_url": f"data:image/jpeg;base64,{base64_image}",
                            },
                        ],
                    }
                ],
                tools=[{"type": "image_generation"}],
            )
            
            # Look for image_generation_call outputs
            image_generation_calls = [
                output
                for output in response.output
                if output.type == "image_generation_call"
            ]
            
            if image_generation_calls:
                # Get the base64 image data from the result
                image_data = image_generation_calls[0].result
                log.info(f"Generated image for {key}, base64 length: {len(image_data)}")
                images[key] = image_data
            else:
                log.warning(f"No image generated for {key}. Response output:")
                for output in response.output:
                    log.warning(f"  Output type: {getattr(output, 'type', 'unknown')}")
                    if hasattr(output, 'content'):
                        for content in output.content:
                            if hasattr(content, 'text'):
                                log.warning(f"  Text: {content.text}")
                images[key] = None
                
        except Exception as e:
            log.exception(f"Error generating image for {key}: {e}")
            images[key] = None
            
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
            "usage": "POST application/json with: { image_base64: string, number_of_images: number }. Generates enhanced product images with creative prompts.",
            "description": "This API takes a product image as base64 and generates enhanced versions with different creative presentations while preserving the original product identity."
        })

    def do_POST(self):
        # fail fast on key issues
        err = _check_key()
        if err:
            return send_json(self, 500, {"error": err})

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
                # Try JSON regardless of charset or exact header
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
            # Validate base64 image data
            _decode_image_b64(base64_image)
        except Exception as e:
            return send_json(self, 400, {"error": f"Invalid base64 image data: {str(e)}"})

        try:
            # Step 1: Describe the image
            log.info("Step 1: Describing image...")
            description = describe_image(base64_image)
            log.info(f"Image description: {description[:200]}...")

            # Step 2: Generate creative prompts
            log.info("Step 2: Generating creative prompts...")
            prompts_json = generate_creative_prompts(description, base64_image, number_of_images)
            log.info(f"Generated prompts: {list(prompts_json.keys())}")

            # Step 3: Generate images from prompts
            log.info("Step 3: Generating images...")
            generated_images = generate_images_from_prompts(prompts_json, base64_image, description, number_of_images)

            # Prepare response - focus on generated images
            result = {
                "success": True,
                "generated_images": []
            }

            # Add generated images to result as array
            for key, image_b64 in generated_images.items():
                if image_b64:
                    try:
                        # Force 24-bit RGB before returning
                        image_b64_rgb = image_post_process(image_b64)
                    except Exception as e:
                        log.warning(f"Failed to convert image {key} to 24-bit: {e}")
                        image_b64_rgb = image_b64  # fallback to original

                    result["generated_images"].append({
                        "prompt": prompts_json.get(key, ""),
                        "image": f"data:image/png;base64,{image_b64_rgb}"
                    })


            log.info(f"Successfully generated {len(result['generated_images'])} images")
            return send_json(self, 200, result)

        except Exception as e:
            import traceback
            log.exception("Image generation pipeline failed")
            return send_json(self, 500, {
                "error": "Image generation pipeline failed",
                "message": str(e),
                "trace": traceback.format_exc(),
            })
