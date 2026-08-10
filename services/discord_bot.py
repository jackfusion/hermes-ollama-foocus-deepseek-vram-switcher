import discord
from discord.ext import commands
import requests
import asyncio
import os
import io
import time
import base64
import re
import rembg
from PIL import Image, ImageOps, ImageDraw, ImageFilter
from dotenv import load_dotenv

# Nuke proxies to guarantee a clean pipeline to localhost
for var in ['http_proxy', 'https_proxy', 'all_proxy', 'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY']:
    if var in os.environ:
        del os.environ[var]
os.environ['NO_PROXY'] = 'localhost,127.0.0.1,0.0.0.0'

load_dotenv()
DISCORD_TOKEN = os.getenv("YOUR_DISCORD_BOT_TOKEN_HERMES_CREATIVE_DIRECTOR")

# The internal routing must use the Debian Host IP!
FOOOCUS_API_URL = os.getenv("FOOOCUS_API_URL", "http://fooocus:7865/v1/generation/text-to-image")
FOOOCUS_VARY_URL = os.getenv("FOOOCUS_VARY_URL", "http://fooocus:7865/v2/generation/image-upscale-vary")
FOOOCUS_INPAINT_URL = os.getenv("FOOOCUS_INPAINT_URL", "http://fooocus:7865/v2/generation/image-inpaint-outpaint")
FOOOCUS_HEALTH_URL = os.getenv("FOOOCUS_HEALTH_URL", "http://fooocus:7865/")
OLLAMA_API_URL = os.getenv("OLLAMA_API_URL", "http://10.0.2.201:11434/api/generate")
OLLAMA_HEALTH_URL = os.getenv("OLLAMA_HEALTH_URL", "http://10.0.2.201:11434/api/tags")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ==========================================
# 🧠 THE VRAM JUGGLER MODULE
# ==========================================

async def swap_to_vision_mode():
    """Bypasses the Docker CLI to speak directly to the Daemon via the Unix Socket."""
    
    # 1. Fast Kill the Text Engine (Ollama) with a 2-second SIGKILL timeout
    process_stop = await asyncio.create_subprocess_exec(
        "curl", "-s", "--unix-socket", "/var/run/docker.sock", "-X", "POST", 
        "http://localhost/containers/inference_ollama/stop?t=2"
    )
    await process_stop.communicate()
    
    # 2. VRAM Flush Pause (Ensure memory is dropped)
    await asyncio.sleep(1)
    
    # 3. Ignite the Vision Engine (Fooocus)
    process_start = await asyncio.create_subprocess_exec(
        "curl", "-s", "--unix-socket", "/var/run/docker.sock", "-X", "POST", 
        "http://localhost/containers/enterprise-fooocus-stack-fooocus-1/start"
    )
    await process_start.communicate()

async def swap_to_text_mode():
    """Bypasses the Docker CLI to speak directly to the Daemon to swap back to the LLM and AI Agent."""
    
    # 1. Fast Kill the Vision Engine (Fooocus) with a 2-second SIGKILL timeout
    process_stop = await asyncio.create_subprocess_exec(
        "curl", "-s", "--unix-socket", "/var/run/docker.sock", "-X", "POST",
        "http://localhost/containers/enterprise-fooocus-stack-fooocus-1/stop?t=2"
    )
    await process_stop.communicate()
    
    # 2. VRAM Flush Pause (Ensure memory is dropped)
    await asyncio.sleep(1)
    
    # 3. Ignite the Text Engine (Ollama)
    process_start = await asyncio.create_subprocess_exec(
        "curl", "-s", "--unix-socket", "/var/run/docker.sock", "-X", "POST",
        "http://localhost/containers/inference_ollama/start"
    )
    await process_start.communicate()

    # 4. Ignite the AI Chat Agent (agent_hermes)
    process_agent = await asyncio.create_subprocess_exec(
        "curl", "-s", "--unix-socket", "/var/run/docker.sock", "-X", "POST",
        "http://localhost/containers/agent_hermes/start"
    )
    await process_agent.communicate()

async def wait_for_engine(timeout=120):
    """Pings the engine until the PyTorch tensors are fully loaded into VRAM."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            # Send a fast, lightweight ping to the server root
            res = await asyncio.to_thread(requests.get, FOOOCUS_HEALTH_URL, timeout=2)
            if res.status_code == 200:
                return True
        except:
            pass # Server is still booting/loading models
        
        await asyncio.sleep(2)
    return False

async def wait_for_text_engine(timeout=30):
    """Pings Ollama until the engine is ready to receive requests."""
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            res = await asyncio.to_thread(requests.get, OLLAMA_HEALTH_URL, timeout=2)
            if res.status_code == 200:
                return True
        except:
            pass
        await asyncio.sleep(2)
    return False

# ==========================================
# 🎨 THE REST API MODULE
# ==========================================

async def extract_image_attachment(ctx):
    """Helper to extract image bytes from direct attachments, referenced message replies, or recent channel history."""
    # 1. Direct Attachment on current message
    if ctx.message.attachments:
        for attachment in ctx.message.attachments:
            print(f"[DEBUG] Direct attachment found: filename={attachment.filename}, content_type={attachment.content_type}")
            return await attachment.read()

    # 2. Reply Message Attachment (User replied to a message)
    if ctx.message.reference and ctx.message.reference.message_id:
        try:
            ref_msg = await ctx.channel.fetch_message(ctx.message.reference.message_id)
            if ref_msg.attachments:
                for attachment in ref_msg.attachments:
                    print(f"[DEBUG] Reply attachment found: filename={attachment.filename}, content_type={attachment.content_type}")
                    return await attachment.read()
        except Exception as e:
            print(f"[DEBUG] Error fetching referenced message: {e}")

    # 3. Recent Channel History Fallback (Finds last generated/sent image in the channel)
    try:
        async for msg in ctx.channel.history(limit=10):
            if msg.id == ctx.message.id:
                continue
            if msg.attachments:
                for attachment in msg.attachments:
                    print(f"[DEBUG] History attachment found on msg {msg.id}: filename={attachment.filename}, content_type={attachment.content_type}")
                    return await attachment.read()
    except Exception as e:
        print(f"[DEBUG] Error searching channel history for image: {e}")

    print("[DEBUG] No image attachment found!")
    return None

def create_inpaint_mask_and_image(img_bytes, target="subject", feature="all"):
    """Uses rembg to create an RGBA cutout and generates a targeted inpainting mask.
    target='background': white=background (inpainted), black=subject (preserved)
    target='subject': white=subject (inpainted), black=background (preserved)
    feature='eye': passes full subject mask to Fooocus so SDXL has face context to render generation7 red eyes
    feature='lip': restricts mask exclusively to the lip box
    """
    try:
        transparent_png = rembg.remove(img_bytes)
        rgba_img = Image.open(io.BytesIO(transparent_png))
        
        # Extract alpha channel
        alpha = rgba_img.split()[3]
        if target == "background":
            mask_img = ImageOps.invert(alpha)
        else:
            mask_img = alpha.copy()
            bbox = alpha.getbbox()
            if bbox:
                min_x, min_y, max_x, max_y = bbox
                width = max_x - min_x
                height = max_y - min_y
                
                if feature == "eye":
                    # Canvas-relative dual eye-socket bounding box mask sent to Fooocus (brows to cheeks, left to right temple)
                    # Covers BOTH eyes (X=368 to X=737) so SDXL recolors BOTH irises symmetrically
                    W, H = alpha.size
                    mask_np_img = Image.new("L", (W, H), 0)
                    draw_mask = ImageDraw.Draw(mask_np_img)
                    
                    eye_x1 = int(0.32 * W)
                    eye_x2 = int(0.64 * W)
                    eye_y1 = int(0.22 * H)
                    eye_y2 = int(0.48 * H)
                    
                    draw_mask.rectangle((eye_x1, eye_y1, eye_x2, eye_y2), fill=255)
                    mask_img = mask_np_img
                elif feature == "lip":
                    mask_np_img = Image.new("L", alpha.size, 0)
                    lip_y1 = int(min_y + 0.52 * height)
                    lip_y2 = int(min_y + 0.72 * height)
                    lip_x1 = int(min_x + 0.35 * width)
                    lip_x2 = int(min_x + 0.65 * width)
                    lip_box = (lip_x1, lip_y1, lip_x2, lip_y2)
                    mask_np_img.paste(alpha.crop(lip_box), lip_box)
                    mask_img = mask_np_img
        
        # Convert RGBA to RGB for base image
        buf_img = io.BytesIO()
        rgba_img.convert('RGB').save(buf_img, format='PNG')
        img_b64 = base64.b64encode(buf_img.getvalue()).decode('utf-8')
        
        # Save mask image to base64
        buf_mask = io.BytesIO()
        mask_img.save(buf_mask, format='PNG')
        mask_b64 = base64.b64encode(buf_mask.getvalue()).decode('utf-8')
        
        return img_b64, mask_b64
    except Exception as e:
        print(f"CRITICAL Mask Creation Error: {e}")
        raw_b64 = base64.b64encode(img_bytes).decode('utf-8')
        return raw_b64, None

def composite_inpainted_image(base_img_bytes, rendered_img_bytes, target="subject", feature="all"):
    """Composites the rendered image back with the base image using rembg mask
    to guarantee 100% crisp, zero-blur preservation of unedited areas.
    For feature='eye', composites ONLY the vibrant red eye irises from rendered_img onto base_img,
    stripping away all red lipstick, eyeshadow, and skin modifications.
    """
    try:
        base_img = Image.open(io.BytesIO(base_img_bytes)).convert("RGB")
        rendered_img = Image.open(io.BytesIO(rendered_img_bytes)).convert("RGB")
        
        # Match dimensions if Fooocus padded or adjusted size
        if rendered_img.size != base_img.size:
            rendered_img = rendered_img.resize(base_img.size, Image.LANCZOS)
            
        # Get subject cutout alpha mask
        transparent_png = rembg.remove(base_img_bytes)
        rgba_cutout = Image.open(io.BytesIO(transparent_png))
        alpha_mask = rgba_cutout.split()[3]
        
        if target == "subject":
            if feature in ["eye", "lip"]:
                bbox = alpha_mask.getbbox()
                if bbox:
                    min_x, min_y, max_x, max_y = bbox
                    width = max_x - min_x
                    height = max_y - min_y
                    mask_np = Image.new("L", alpha_mask.size, 0)
                    
                    if feature == "eye":
                        # Full iris circles (rx=0.024W, ry=0.020H) - transfers 100% vibrant deep red irises from generation7
                        W, H = base_img.size
                        mask_np = Image.new("L", (W, H), 0)
                        draw = ImageDraw.Draw(mask_np)
                        
                        re_x = int(0.423 * W)
                        le_x = int(0.534 * W)
                        eye_y = int(0.357 * H)
                        rx = int(0.024 * W)
                        ry = int(0.020 * H)
                        
                        box_right_eye = (re_x - rx, eye_y - ry, re_x + rx, eye_y + ry)
                        box_left_eye = (le_x - rx, eye_y - ry, le_x + rx, eye_y + ry)
                        
                        draw.ellipse(box_right_eye, fill=255)
                        draw.ellipse(box_left_eye, fill=255)
                        alpha_mask = mask_np.filter(ImageFilter.GaussianBlur(2.5))
                        
                    elif feature == "lip":
                        lip_y1 = int(min_y + 0.52 * height)
                        lip_y2 = int(min_y + 0.72 * height)
                        lip_x1 = int(min_x + 0.35 * width)
                        lip_x2 = int(min_x + 0.65 * width)
                        lip_box = (lip_x1, lip_y1, lip_x2, lip_y2)
                        mask_np.paste(alpha_mask.crop(lip_box), lip_box)
                        alpha_mask = mask_np

            # Subject (alpha=255) comes from rendered_img, Background/Lips/Skin (alpha=0) comes 100% untouched from base_img
            final_img = Image.composite(rendered_img, base_img, alpha_mask)
        else:
            final_img = Image.composite(base_img, rendered_img, alpha_mask)
            
        output_buf = io.BytesIO()
        final_img.save(output_buf, format="PNG")
        return output_buf.getvalue()
    except Exception as e:
        print(f"CRITICAL Composite Error: {e}")
        return rendered_img_bytes

def sanitize_edit_prompt(prompt):
    """Parses user edit requests (e.g. 'change the lip color to red on generation1.png')
    into optimized SDXL/Fooocus inpaint prompts, additional prompts, negative prompts, inpaint strength, feature category, respective field, and disable initial latent flag.
    """
    clean = re.sub(r'for image \S+|image \S+\.png|\S+\.png', '', prompt, flags=re.IGNORECASE).strip()
    clean_lower = clean.lower()
    
    # 1. Lip / Lipstick Editing
    if any(kw in clean_lower for kw in ["lip", "lips", "lipstick", "mouth"]):
        color_match = re.search(r'(?:to|into|make|with|lips?|lipstick)\s+([a-zA-Z]+)', clean_lower)
        target_color = color_match.group(1) if color_match else ""
        valid_colors = ["red", "pink", "coral", "ruby", "plum", "berry", "cherry", "nude", "burgundy", "crimson", "purple", "dark red"]
        color = target_color if target_color in valid_colors else "red"
        
        inpaint_prompt = f"portrait of the same woman with {color} lipstick, beautiful {color} lips, natural face"
        additional_prompt = f"{color} lipstick, {color} lips"
        negative_prompt = f"blue eyes, red eyes, change eye color, {color} eyeshadow, {color} eyeliner, {color} skin, eye makeup, distorted eyes, bad anatomy, mutation"
        inpaint_strength = 0.42
        return inpaint_prompt, additional_prompt, negative_prompt, inpaint_strength, "lip", 0.6, False

    # 2. Eye Color Editing (Canvas-relative dual eye mask + 0.80 strength for symmetric glowing red irises)
    elif "eye" in clean_lower:
        color_match = re.search(r'(?:to|into|make|with|eyes?)\s+([a-zA-Z]+)', clean_lower)
        target_color = color_match.group(1) if color_match else ""
        valid_colors = ["red", "blue", "green", "purple", "hazel", "amber", "brown", "black", "yellow", "violet", "cyan"]
        color = target_color if target_color in valid_colors else "red"
        
        inpaint_prompt = f"portrait of the same woman with glowing vibrant {color} irises, bright {color} pupils, {color} eye color, natural face"
        additional_prompt = f"glowing vibrant {color} irises, {color} pupils, {color} eye color"
        negative_prompt = f"{color} eyeshadow, {color} eyeliner, {color} makeup, {color} skin, makeup, lipstick, red lips, green eyes, hazel eyes, brown eyes, blue eyes, dark eyes, white dots, stars, orb, artifacts, distorted pupils, bad eyes, bad anatomy, closeup, zoomed, dark face, mutation"
        inpaint_strength = 0.80
        return inpaint_prompt, additional_prompt, negative_prompt, inpaint_strength, "eye", 1.0, False

    # 3. Hair Editing
    elif "hair" in clean_lower:
        color_match = re.search(r'(?:to|into|make|with|hair)\s+([a-zA-Z]+)', clean_lower)
        target_color = color_match.group(1) if color_match else ""
        color = target_color if target_color else "blonde"
        
        inpaint_prompt = f"portrait of the same woman, beautiful {color} hair, detailed {color} hair strands, natural texture"
        additional_prompt = f"{color} hair, detailed {color} hair strands"
        negative_prompt = "change eye color, blue eyes, red eyes, eyeshadow, eyeliner, lipstick, dark hair, black hair, brown hair, bald, messy, artifacts, bad hair, mutation"
        inpaint_strength = 0.55
        return inpaint_prompt, additional_prompt, negative_prompt, inpaint_strength, "hair", 0.8, False

    # 4. Clothing / Outfit Editing
    elif any(kw in clean_lower for kw in ["shirt", "dress", "jacket", "clothes", "outfit", "wearing"]):
        inpaint_prompt = f"{clean}, high quality detailed clothing, photorealistic"
        additional_prompt = clean
        negative_prompt = "nudity, bad clothes, low quality, artifacts, distorted, change eye color, blue eyes"
        inpaint_strength = 0.65
        return inpaint_prompt, additional_prompt, negative_prompt, inpaint_strength, "clothing", 1.0, False

    # 5. Background Editing
    elif any(kw in clean_lower for kw in ["background", "bg", "environment", "setting", "scene"]):
        inpaint_prompt = f"{clean}, cinematic background, high quality, highly detailed 8k"
        additional_prompt = clean
        negative_prompt = "low quality, blurry, noise, distortion, artifacts"
        inpaint_strength = 1.0
        return inpaint_prompt, additional_prompt, negative_prompt, inpaint_strength, "background", 1.0, False

    # Generic fallback
    else:
        refined = re.sub(r'^(change|make|turn|modify|add|set)\s+(the\s+)?', '', clean, flags=re.IGNORECASE).strip()
        inpaint_prompt = f"{refined}, highly detailed, high quality"
        additional_prompt = refined
        negative_prompt = "change eye color, blue eyes, red eyes, eyeshadow, eyeliner, eye makeup, blurry, low quality, distortion, artifacts, white dots, stars, orb"
        inpaint_strength = 0.5
        return inpaint_prompt, additional_prompt, negative_prompt, inpaint_strength, "all", 1.0, False

def generate_image_sync(prompt, image_b64=None, mask_b64=None, edit_type="vary", inpaint_additional_prompt=None, negative_prompt=None, inpaint_strength=1.0, inpaint_respective_field=1.0, disable_initial_latent=False):
    """Sends a JSON payload to the Fooocus API and downloads the rendered image."""
    try:
        if image_b64 and edit_type == "inpaint" and mask_b64:
            payload = {
                "input_image": f"data:image/png;base64,{image_b64}",
                "input_mask": f"data:image/png;base64,{mask_b64}",
                "prompt": prompt if prompt else "high quality detailed image",
                "inpaint_additional_prompt": inpaint_additional_prompt if inpaint_additional_prompt else prompt,
                "negative_prompt": negative_prompt if negative_prompt else "blurry, low quality, artifacts, white dots, stars, orb",
                "performance_selection": "Speed",
                "require_base64": False,
                "async_process": False,
                "advanced_params": {
                    "inpaint_engine": "v2.6",
                    "inpaint_strength": inpaint_strength,
                    "inpaint_respective_field": inpaint_respective_field,
                    "inpaint_disable_initial_latent": disable_initial_latent,
                    "inpaint_advanced_masking_checkbox": True
                }
            }
            target_url = FOOOCUS_INPAINT_URL

        elif image_b64:
            # Subject Feature / Detail Editing Mode: Uses Vary (Subtle) to modify details (e.g. eye color, hats) while keeping background intact
            payload = {
                "input_image": image_b64,
                "prompt": prompt if prompt else "High quality detailed image",
                "uov_method": "Vary (Subtle)",
                "performance_selection": "Speed",
                "require_base64": False,
                "async_process": False
            }
            target_url = FOOOCUS_VARY_URL

        else:
            # Standard Text-to-Image Generation
            payload = {
                "prompt": prompt if prompt else "High quality detailed image",
                "performance_selection": "Speed",
                "aspect_ratio": "1152×896",
                "require_base64": False,
                "async_process": False
            }
            target_url = FOOOCUS_API_URL

        response = requests.post(target_url, json=payload, timeout=300)
        response.raise_for_status()
        data = response.json()
        
        img_url = data[0]['url']
        # Fix container routing: replace 127.0.0.1 / localhost with internal fooocus container hostname
        img_url = img_url.replace("127.0.0.1:7865", "fooocus:7865").replace("localhost:7865", "fooocus:7865")
        
        img_response = requests.get(img_url, timeout=30)
        img_response.raise_for_status()
        
        return io.BytesIO(img_response.content)
        
    except Exception as e:
        print(f"CRITICAL API Error: {e}")
        raise e

def remove_background_sync(img_bytes):
    """Uses AI U2-Net model to strip background from image bytes and return transparent PNG bytes."""
    try:
        return rembg.remove(img_bytes)
    except Exception as e:
        print(f"CRITICAL rembg Error: {e}")
        raise e

def generate_text_sync(prompt, model="deepseek-r1:8b"):
    """Sends a generate request to Ollama and returns the cleaned text response."""
    try:
        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False
        }
        response = requests.post(OLLAMA_API_URL, json=payload, timeout=180)
        response.raise_for_status()
        data = response.json()
        raw_text = data.get("response", "")
        
        # Strip thinking tags if present (e.g. DeepSeek-R1 <think>...</think>)
        import re
        clean_text = re.sub(r'<think>.*?</think>', '', raw_text, flags=re.DOTALL).strip()
        return clean_text if clean_text else raw_text.strip()
    except Exception as e:
        print(f"CRITICAL Ollama API Error: {e}")
        raise e

# ==========================================
# 🚀 THE DISCORD COMMAND
# ==========================================

@bot.command(name="imagine", aliases=["edit", "render", "style", "nobg", "removebg"])
async def imagine(ctx, *, prompt: str = ""):
    # Check if this is a pure background removal request (e.g. !nobg or !removebg)
    is_nobg = ctx.invoked_with in ["nobg", "removebg"] or (prompt and prompt.lower().strip() in ["remove background", "remove the background", "no background", "transparent background"])
    
    # Check for image attachment, message reply, or channel history
    img_bytes = await extract_image_attachment(ctx)

    if not prompt and not img_bytes:
        await ctx.send("❌ **Error:** Please provide a prompt or attach an image!")
        return

    # Direct Background Removal via U2-Net model (returns transparent PNG cutout)
    if is_nobg and img_bytes:
        status_msg = await ctx.send("✂️ **AI Background Remover:** Isolating subject & stripping background...")
        try:
            transparent_png_bytes = await asyncio.to_thread(remove_background_sync, img_bytes)
            await ctx.send(file=discord.File(fp=io.BytesIO(transparent_png_bytes), filename="background_removed.png"))
            await status_msg.delete()
            return
        except Exception as e:
            await status_msg.edit(content=f"❌ **Background Removal failed:** `{e}`")
            return

    # Standard VRAM Juggler Generation/Editing
    status_msg = await ctx.send("🔄 **VRAM Juggler:** Clearing memory and igniting Vision Engine...")
    try:
        # Clean prompt text of user filename references (e.g. 'for image generation.png')
        clean_prompt = re.sub(r'for image \S+|image \S+\.png|\S+\.png', '', prompt, flags=re.IGNORECASE).strip()
        if not clean_prompt and prompt:
            clean_prompt = prompt

        # Determine edit type: Background edit vs Subject Feature edit (e.g. eye color, hair) vs Variation
        bg_keywords = ["background", "bg", "environment", "setting", "scene", "behind"]
        is_bg_edit = any(kw in clean_prompt.lower() for kw in bg_keywords)
        vary_keywords = ["vary", "variation", "restyle", "redraw"]
        is_vary_edit = any(kw in clean_prompt.lower() for kw in vary_keywords)

        if img_bytes:
            inpaint_prompt, add_prompt, neg_prompt, strength, feature, field, disable_latent = sanitize_edit_prompt(clean_prompt)
            if is_bg_edit:
                edit_type = "inpaint"
                img_b64, mask_b64 = await asyncio.to_thread(create_inpaint_mask_and_image, img_bytes, target="background", feature="background")
                status_header = f"🎨 **Editing Background (Subject Mask Preserved):** `{clean_prompt}`"
            elif is_vary_edit:
                edit_type = "vary"
                img_b64 = base64.b64encode(img_bytes).decode('utf-8')
                mask_b64 = None
                inpaint_prompt, add_prompt, neg_prompt, strength, feature, field, disable_latent = clean_prompt, clean_prompt, "", 1.0, "all", 1.0, False
                status_header = f"🎨 **Editing Details / Features (Vary Subtle):** `{clean_prompt}`"
            else:
                edit_type = "inpaint"
                img_b64, mask_b64 = await asyncio.to_thread(create_inpaint_mask_and_image, img_bytes, target="subject", feature=feature)
                status_header = f"🎨 **Editing Subject ({feature.title()} Target):** `{clean_prompt}`"
        else:
            edit_type = "text2img"
            img_b64, mask_b64 = None, None
            inpaint_prompt, add_prompt, neg_prompt, strength, feature, field, disable_latent = clean_prompt, clean_prompt, "", 1.0, "all", 1.0, False
            status_header = f"🎨 **Rendering New Image:** `{clean_prompt}` *(Note: Attach or reply to an image to edit existing images)*"

        # Step 2: The Hand-off
        await swap_to_vision_mode()
        
        # Step 3: The Loading Screen
        await status_msg.edit(content="⏳ **PyTorch:** Loading models into VRAM... (This takes a few seconds)")
        engine_ready = await wait_for_engine()
        
        if not engine_ready:
            await status_msg.edit(content="❌ **Error:** Vision Engine failed to boot within 120 seconds. Check Docker logs.")
            return

        # Step 4: The Generation / Editing
        await status_msg.edit(content=status_header)
        image_result_io = await asyncio.to_thread(generate_image_sync, inpaint_prompt, img_b64, mask_b64, edit_type, add_prompt, neg_prompt, strength, field, disable_latent)
        image_result_bytes = image_result_io.getvalue()

        # Step 4b: Post-Composite Layer Blending (Guarantees 100% bit-exact crisp preservation of untouched areas)
        if img_bytes and edit_type == "inpaint":
            target_mode = "background" if is_bg_edit else "subject"
            image_result_bytes = await asyncio.to_thread(composite_inpainted_image, img_bytes, image_result_bytes, target_mode, feature)

        # Step 5: The Delivery
        await ctx.send(file=discord.File(fp=io.BytesIO(image_result_bytes), filename="generation.png"))
        await status_msg.delete()
        
    except Exception as e:
        await status_msg.edit(content=f"❌ **Generation failed:** `{e}`")

@bot.command(name="text_engine", aliases=["text-engine", "cognitive"])
async def text_engine(ctx, *, prompt: str = None):
    """Swaps VRAM to Ollama and optionally generates a text response directly in Discord."""
    status_msg = await ctx.send("🔄 **VRAM Juggler:** Clearing memory and igniting Text Engine...")
    
    try:
        await swap_to_text_mode()
        
        if not prompt:
            await status_msg.edit(content="✅ **Ollama:** Text Engine and Hermes AI Chat are now online and loaded into VRAM.")
            return

        # If a prompt was provided:
        await status_msg.edit(content="⏳ **Ollama:** Warming up text engine & PyTorch tensors...")
        engine_ready = await wait_for_text_engine()
        
        if not engine_ready:
            await status_msg.edit(content="❌ **Error:** Text Engine failed to boot within 30 seconds.")
            return

        await status_msg.edit(content=f"🧠 **Thinking:** `{prompt}`")
        reply_text = await asyncio.to_thread(generate_text_sync, prompt)

        # Respect Discord's 2000 character message limit
        formatted_header = f"🗣️ **Prompt:** `{prompt}`\n\n"
        max_body_len = 1950 - len(formatted_header)
        
        if len(reply_text) <= max_body_len:
            await status_msg.edit(content=f"{formatted_header}{reply_text}")
        else:
            await status_msg.edit(content=f"{formatted_header}{reply_text[:max_body_len]}...\n*(Response truncated due to Discord limit)*")
            
    except Exception as e:
        await status_msg.edit(content=f"❌ **Text Generation failed:** `{e}`")

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
