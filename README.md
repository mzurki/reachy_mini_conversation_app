---
title: DJ Reachy
emoji: 🎧
colorFrom: red
colorTo: blue
sdk: static
pinned: false
short_description: Request a song and watch DJ Reachy write, play & dance!
tags:
  - reachy_mini
  - reachy_mini_python_app
---

<div align="center">

# 🎧 DJ Reachy

**Your personal robot DJ that creates songs and dances to them**

![Reachy Mini Dance](docs/assets/reachy_mini_dance.gif)

*Tell DJ Reachy what kind of song you want, and watch as it generates music and grooves along!*

</div>

---

## What is DJ Reachy?

DJ Reachy transforms your [Reachy Mini](https://github.com/pollen-robotics/reachy_mini/) robot into an interactive music-making companion. Just talk to it, request a song, and DJ Reachy will:

1. 🎤 **Listen** to your song request through real-time voice conversation
2. 🎵 **Generate** a unique song using AI music generation  
3. 💃 **Dance** with audio-reactive movements synchronized to the beat

Powered by OpenAI's realtime API for natural conversation and ElevenLabs for music generation.

---

## Quick Start

### Prerequisites

- [Reachy Mini SDK](https://github.com/pollen-robotics/reachy_mini/) installed and daemon running
- Python 3.10+
- OpenAI API key
- ElevenLabs API key (for music generation)

### Installation

```bash
# Clone and setup
git clone <repo-url>
cd reachy_mini_conversation_app

# Create virtual environment
uv venv --python 3.12.1
source .venv/bin/activate
uv sync

# Or with pip
python -m venv .venv
source .venv/bin/activate
pip install -e .
```

### Run

```bash
dj-reachy --gradio
```

Open http://127.0.0.1:7860 and start talking! Enter your API keys in the web UI when prompted.

---

## How It Works

```
┌─────────────┐      ┌──────────────────┐      ┌─────────────┐
│     You     │◄────►│   DJ Reachy App  │◄────►│ Reachy Mini │
│  (Browser)  │      │                  │      │   Robot     │
└─────────────┘      └──────────────────┘      └─────────────┘
                              │
                    ┌─────────┴─────────┐
                    ▼                   ▼
            ┌──────────────┐   ┌──────────────┐
            │   OpenAI     │   │  ElevenLabs  │
            │ Realtime API │   │    Music     │
            └──────────────┘   └──────────────┘
```

- **OpenAI Realtime API** handles voice conversation with low-latency streaming via [fastrtc](https://github.com/freddyaboulton/fastrtc)
- **ElevenLabs** generates unique songs from your text prompts
- **Audio-reactive dancing** analyzes the music in real-time to generate synchronized robot movements
- **Gradio** provides the web interface

---

## CLI Options

| Option | Description |
|--------|-------------|
| `--gradio` | Launch web UI (required for simulation mode) |
| `--head-tracker {yolo,mediapipe}` | Enable face-tracking |
| `--no-camera` | Run without camera |
| `--wireless-version` | Use GStreamer backend for wireless Reachy Mini |
| `--debug` | Verbose logging |

### Examples

```bash
# Standard usage with web UI
dj-reachy --gradio

# With face tracking
dj-reachy --gradio --head-tracker mediapipe

# Audio-only (no camera)
dj-reachy --gradio --no-camera

# For wireless Reachy Mini
dj-reachy --gradio --wireless-version
```

---

## Optional Dependencies

Install extras based on your setup:

```bash
# Wireless Reachy Mini support
uv sync --extra reachy_mini_wireless

# Face tracking options
uv sync --extra yolo_vision      # YOLOv8 tracking
uv sync --extra mediapipe_vision # MediaPipe tracking

# Local vision processing (SmolVLM2)
uv sync --extra local_vision

# Development tools
uv sync --group dev
```

| Extra | Purpose |
|-------|---------|
| `reachy_mini_wireless` | GStreamer support for wireless robots |
| `yolo_vision` | YOLOv8-based face tracking |
| `mediapipe_vision` | Lightweight MediaPipe tracking |
| `local_vision` | On-device vision with SmolVLM2 |

---

## Development

```bash
# Install dev dependencies
uv sync --group dev

# Run linter
ruff check .

# Run tests
pytest
```

---

## License

Apache 2.0
