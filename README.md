# 🚀 Hermes VRAM Orchestrator & AI Creative Stack

An enterprise-grade Docker orchestration stack that dynamically juggles GPU VRAM between heavy PyTorch image generation (**Fooocus SDXL**) and local LLM inference (**Ollama** + **Hermes AI Agent**).

---

## 🏗️ Architecture Overview

```
                      ┌───────────────────────────┐
                      │    Discord User Interface │
                      └─────────────┬─────────────┘
                                    │
                  ┌─────────────────┴─────────────────┐
                  │                                   │
         !imagine │                                   │ !text_engine
                  ▼                                   ▼
    ┌───────────────────────────┐           ┌───────────────────────────┐
    │  Hermes Creative Director │           │     Hermes AI Agent       │
    │   (services/discord_bot)  │           │       (agent_hermes)      │
    └─────────────┬─────────────┘           └─────────────┬─────────────┘
                  │                                       │
                  │   Unix Socket (/var/run/docker.sock) │
                  ├───────────────────┬───────────────────┘
                  │                   │
                  ▼                   ▼
    ┌───────────────────────────┐   ┌───────────────────────────┐
    │   Fooocus Vision Engine   │   │   Ollama Text Engine      │
    │ (enterprise-fooocus-1)    │   │   (inference_ollama)      │
    └───────────────────────────┘   └───────────────────────────┘
```

---

## 🛠️ Key Features

- **⚡ Dynamic VRAM Juggler**: Communicates directly with the Docker daemon via `/var/run/docker.sock` to stop and start GPU containers dynamically, eliminating Out-Of-Memory (OOM) errors.
- **🎨 Fooocus Vision Engine**: Generates high-quality Stable Diffusion images via REST API upon `!imagine <prompt>`.
- **🧠 Local LLM Inference**: Powered by Ollama (`deepseek-r1:8b` / `hermes3:8b`).
- **💬 Dual Response Modes**:
  - Direct Discord response when running `!text_engine <prompt>` (cleans internal `<think>` tags and displays response inline).
  - Conversational AI chat in Discord via `agent_hermes`.
- **🌐 Network Security**: Public DNS fallbacks (`1.1.1.1`, `8.8.8.8`) and exposed Web Dashboard on port `8383`.

---

## 📋 Requirements & Prerequisites

- Linux system with NVIDIA GPU (e.g. RTX 3080/4070/4080/5070)
- NVIDIA Drivers + [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)
- Docker & Docker Compose v2+
- Two Discord Bot Tokens created via the Discord Developer Portal.

---

## 🤖 Discord Developer Portal Setup Guide

Follow these steps to create and configure your two Discord bots:

### Step 1: Create Applications
1. Go to the [Discord Developer Portal](https://discord.com/developers/applications).
2. Click **New Application** at the top right.
3. Name your first bot (e.g., `Hermes Creative Director`) and click **Create**.
4. Repeat the process to create your second bot (e.g., `Hermes AI Agent`).

---

### Step 2: Retrieve Bot Tokens
1. Open your application page (`Hermes Creative Director`).
2. Navigate to the **Bot** tab on the left sidebar.
3. Click **Reset Token**, confirm, and copy the generated Bot Token.
   - Save this to `YOUR_DISCORD_BOT_TOKEN_HERMES_CREATIVE_DIRECTOR` in `fooocus-stack/.env` and `services/.env`.
4. Open your second application page (`Hermes AI Agent`), navigate to the **Bot** tab, click **Reset Token**, and copy the token.
   - Save this to `HERMES_DISCORD_TOKEN` in root `.env`.

---

### Step 3: Enable Privileged Gateway Intents (CRITICAL ⚠️)
For **BOTH** applications:
1. On the **Bot** tab, scroll down to **Privileged Gateway Intents**.
2. **MUST ENABLE**:
   - ✅ **Message Content Intent** (Required for bots to read `!imagine`, `!text_engine`, and user chat messages).
   - ✅ **Server Members Intent** (Recommended).

---

### Step 4: Generate Invite URLs & Add Bots to Server
For **BOTH** applications:
1. Navigate to **OAuth2** -> **URL Generator** on the left menu.
2. Under **Scopes**, select `bot`.
3. Under **Bot Permissions**, select:
   - ✅ `Send Messages`
   - ✅ `Read Message History`
   - ✅ `Attach Files`
   - ✅ `Embed Links`
   - ✅ `Use Slash Commands`
4. Copy the generated URL at the bottom, paste it into a browser tab, select your Discord server, and click **Authorize**.

---

### Step 5: Find Your Discord User ID (for `DISCORD_ALLOWED_USERS`)
1. In Discord Desktop or Web, go to **User Settings** -> **Advanced** -> Enable **Developer Mode**.
2. Right-click your profile/avatar in any server or channel -> Click **Copy User ID**.
3. Save this ID to `DISCORD_ALLOWED_USERS` in root `.env`.

---

## 🚀 Quick Start Deployment Guide

### Step 1: Create Shared Docker Network
Create the external Docker bridge network required for inter-container communication:
```bash
docker network create agent_net
```

---

### Step 2: Configure Environment Files
Copy the template `.env.example` files to `.env`:

```bash
cp .env.example .env
cp fooocus-stack/.env.example fooocus-stack/.env
cp services/.env.example services/.env
```

Edit root `.env`:
```env
HERMES_DISCORD_TOKEN=your_hermes_chat_bot_token_here
DISCORD_ALLOWED_USERS=your_discord_user_id
DOCKER_APPDATA=/mnt/media/docker/appdata
MEAGLEYS_PROJECT_DIR=/mnt/media/meagleys_shirts_n_stuff
HOST_IP=10.0.2.201
```

Edit `fooocus-stack/.env` and `services/.env`:
```env
YOUR_DISCORD_BOT_TOKEN_HERMES_CREATIVE_DIRECTOR=your_creative_director_bot_token_here
```

---

### Step 3: Start Docker Compose Stacks

1. **Launch Ollama Inference Engine**:
   ```bash
   docker compose -f ollama.yml up -d
   ```

2. **Launch Hermes Agent Stack**:
   ```bash
   docker compose -f hermes.yml up -d
   ```

3. **Launch Fooocus & Creative Director Bot Stack**:
   ```bash
   docker compose -f fooocus-stack/docker-compose.yml up -d --build
   ```

---

### Step 4: Configure Hermes Agent Persistent Storage (`docker exec`)

Hermes Agent maintains its active token inside its persistent volume (`/opt/data/.env`). Run the following `docker exec` command to ensure the active container reads your valid token:

```bash
docker exec agent_hermes python3 -c "
with open('/opt/data/.env', 'w') as f:
    f.write('DISCORD_BOT_TOKEN=YOUR_HERMES_DISCORD_TOKEN\n')
    f.write('DISCORD_ALLOWED_USERS=YOUR_DISCORD_USER_ID\n')
"

# Restart Hermes Agent to apply the update
docker restart agent_hermes
```

---

## 🎮 Discord Command Reference

| Command | Description |
| :--- | :--- |
| `!imagine <prompt>` | Swaps VRAM to Vision Engine, renders Stable Diffusion image via Fooocus, and uploads it to Discord. |
| `!text_engine` | Swaps VRAM to Ollama + Hermes Agent and brings the AI Chat Bot online. |
| `!text_engine <prompt>` | Swaps VRAM, queries `deepseek-r1:8b` via Ollama REST API, strips `<think>` tags, and displays the response directly in Discord. |
| `!text-engine <prompt>` | Alias for `!text_engine`. |
| `!cognitive <prompt>` | Alias for `!text_engine`. |

---

## 🌐 Web Dashboard Access

The Hermes Web Dashboard is exposed locally at:
```
http://<YOUR_HOST_IP>:9119
```

---

## 📁 Repository Directory Structure

```
hermes-vram-orchestrator/
├── .env.example
├── .gitignore
├── README.md
├── hermes.yml
├── ollama.yml
├── fooocus-stack/
│   ├── .env.example
│   ├── docker-compose.yml
│   └── Dockerfile
└── services/
    ├── .env.example
    ├── Dockerfile.bot
    └── discord_bot.py
```
