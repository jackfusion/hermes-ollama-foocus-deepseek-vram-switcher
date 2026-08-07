import discord
from discord.ext import commands
import requests
import asyncio
import os
import io
import time
import base64
import re
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

def generate_image_sync(prompt, image_b64=None, cn_type="CPDS"):
    """Sends a JSON payload to the Fooocus API and downloads the rendered image."""
    try:
        payload = {
            "prompt": prompt if prompt else "High quality detailed image",
            "performance_selection": "Speed",
            "aspect_ratio": "1152×896",
            "require_base64": False,
            "async_process": False
        }
        
        if image_b64:
            if cn_type == "ImagePrompt":
                payload["image_prompts"] = [
                    {
                        "cn_img": f"data:image/png;base64,{image_b64}",
                        "cn_stop": 0.6,
                        "cn_weight": 0.8,
                        "cn_type": "ImagePrompt"
                    }
                ]
            else:
                # Direct Image Editing Mode:
                # 1. Disable Fooocus V2 prompt expansion so SDXL stays 100% faithful to original image
                # 2. Dual ControlNet (PyraCanny Edge + CPDS Depth) for 1:1 subject retention
                payload["style_selections"] = []
                payload["image_prompts"] = [
                    {
                        "cn_img": f"data:image/png;base64,{image_b64}",
                        "cn_stop": 0.9,
                        "cn_weight": 1.0,
                        "cn_type": "PyraCanny"
                    },
                    {
                        "cn_img": f"data:image/png;base64,{image_b64}",
                        "cn_stop": 0.9,
                        "cn_weight": 1.0,
                        "cn_type": "CPDS"
                    }
                ]
        
        response = requests.post(FOOOCUS_API_URL, json=payload, timeout=300)
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

@bot.command(name="imagine", aliases=["edit", "render", "style"])
async def imagine(ctx, *, prompt: str = ""):
    # Step 1: Claim the request
    status_msg = await ctx.send("🔄 **VRAM Juggler:** Clearing memory and igniting Vision Engine...")
    
    try:
        # Clean prompt text of user filename references (e.g. 'for image generation.png')
        clean_prompt = re.sub(r'for image \S+|image \S+\.png|\S+\.png', '', prompt, flags=re.IGNORECASE).strip()
        if not clean_prompt and prompt:
            clean_prompt = prompt

        # Check for image attachment, message reply, or channel history
        img_bytes = await extract_image_attachment(ctx)
        img_b64 = base64.b64encode(img_bytes).decode('utf-8') if img_bytes else None

        if not clean_prompt and not img_b64:
            await status_msg.edit(content="❌ **Error:** Please provide a prompt or attach an image to edit!")
            return

        # Choose ControlNet type: 'CPDS' preserves exact structure/pose during !edit, 'ImagePrompt' transfers style
        cn_type = "ImagePrompt" if ctx.invoked_with == "style" else "CPDS"

        # Step 2: The Hand-off
        await swap_to_vision_mode()
        
        # Step 3: The Loading Screen
        await status_msg.edit(content="⏳ **PyTorch:** Loading models into VRAM... (This takes a few seconds)")
        engine_ready = await wait_for_engine()
        
        if not engine_ready:
            await status_msg.edit(content="❌ **Error:** Vision Engine failed to boot within 120 seconds. Check Docker logs.")
            return

        # Step 4: The Generation / Editing
        if img_b64:
            await status_msg.edit(content=f"🎨 **Editing Image (Subject & Structure Preserved):** `{clean_prompt if clean_prompt else 'Image Edit'}`")
        else:
            await status_msg.edit(content=f"🎨 **Rendering New Image:** `{clean_prompt}` *(Note: Attach or reply to an image to edit existing images)*")

        image_result_bytes = await asyncio.to_thread(generate_image_sync, clean_prompt, img_b64, cn_type)
        
        # Step 5: The Delivery
        await ctx.send(file=discord.File(fp=image_result_bytes, filename="generation.png"))
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
