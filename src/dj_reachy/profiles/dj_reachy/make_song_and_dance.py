import asyncio
import logging
import os
import re
import time
from typing import Any, Dict, Optional

import numpy as np
import requests

from dj_reachy.audio.music_dancer import MusicDancer
from dj_reachy.tools.core_tools import Tool, ToolDependencies

logger = logging.getLogger(__name__)

# Module-level music dancer instance (lazily initialized)
_music_dancer: Optional[MusicDancer] = None
_music_dancer_deps: Optional[ToolDependencies] = None


def _get_music_dancer(deps: ToolDependencies) -> MusicDancer:
    """Get or create the module-level MusicDancer instance."""
    global _music_dancer, _music_dancer_deps

    # Recreate if deps changed (different movement manager)
    if _music_dancer is None or _music_dancer_deps is not deps:
        _music_dancer = MusicDancer(
            set_offsets_callback=deps.movement_manager.set_music_dance_offsets,
            sample_rate=OPEN_AI_OUTPUT_SAMPLE_RATE,
            intensity=0.20,  # Default intensity (20% actual = 80% slider), can be changed via UI
        )
        _music_dancer_deps = deps
        logger.info("Created new MusicDancer instance")

    return _music_dancer


def get_dance_intensity() -> float:
    """Get current dance intensity (for UI)."""
    if _music_dancer is not None:
        return _music_dancer.intensity
    return 0.20  # Default (20% actual = 80% slider)


def set_dance_intensity(intensity: float) -> None:
    """Set dance intensity (for UI)."""
    global _music_dancer
    if _music_dancer is not None:
        _music_dancer.intensity = intensity
        logger.info("Dance intensity set to %.2f", intensity)

ELEVEN_COMPOSE_URL = "https://api.elevenlabs.io/v1/music/compose"

def _get_elevenlabs_key(deps: ToolDependencies | None = None) -> str:
    """Get ElevenLabs API key from deps, config (set via UI), or environment."""
    # First check deps (set at runtime)
    if deps is not None and getattr(deps, "elevenlabs_api_key", None):
        return deps.elevenlabs_api_key
    # Then check config (updated by UI)
    from dj_reachy.config import config
    key = config.ELEVENLABS_API_KEY or os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise RuntimeError("Missing ELEVENLABS_API_KEY. Please enter it in the web UI settings.")
    return key


def _eleven_headers(deps: ToolDependencies | None = None) -> Dict[str, str]:
    return {
        "xi-api-key": _get_elevenlabs_key(deps),
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }


def _compose_music_to_mp3(prompt: str, timeout_s: int = 180, deps: ToolDependencies | None = None) -> str:
    """
    Calls ElevenLabs Music Compose and writes returned audio/mpeg to the songs directory.
    Songs are saved permanently with timestamp and prompt-based filename.
    Note: Many ElevenLabs music endpoints do not guarantee an exact duration from a single field.
    We keep music_length_ms for dance timing, but do not assume the audio length matches.
    """
    from dj_reachy.config import config
    
    payload = {
        "prompt": prompt,
        # If your ElevenLabs plan/endpoint supports duration controls, add them here
        # after verifying exact parameter names in your account/docs.
    }

    r = requests.post(ELEVEN_COMPOSE_URL, json=payload, headers=_eleven_headers(deps), timeout=timeout_s)

    # If ElevenLabs returns JSON errors, surface them cleanly
    if r.status_code >= 400:
        ct = (r.headers.get("Content-Type") or "").lower()
        if "application/json" in ct:
            try:
                raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.json()}")
            except Exception:
                raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text}")
        raise RuntimeError(f"ElevenLabs error {r.status_code}: {r.text}")

    ct = (r.headers.get("Content-Type") or "").lower()
    if "audio" not in ct:
        # Some gateways return JSON even on 200 if something is off
        preview = r.text[:500] if r.text else "<no body>"
        raise RuntimeError(f"Expected audio response but got Content-Type={ct}. Body: {preview}")

    # Save to permanent songs directory
    songs_dir = config.SONGS_DIR
    songs_dir.mkdir(parents=True, exist_ok=True)
    
    # Create a filename from timestamp and sanitized prompt
    timestamp = int(time.time())
    safe_prompt = re.sub(r'[^a-zA-Z0-9_-]', '_', prompt[:40]).strip('_')
    filename = f"{timestamp}_{safe_prompt}.mp3"
    path = songs_dir / filename
    
    with open(path, "wb") as f:
        f.write(r.content)

    # Basic sanity check
    if os.path.getsize(path) < 1024:
        raise RuntimeError(f"Got suspiciously small MP3 file ({os.path.getsize(path)} bytes)")

    logger.info("Saved song to %s", path)
    return str(path)


async def _run(cmd: list[str]) -> tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out_b, err_b = await proc.communicate()
    msg = (err_b or out_b).decode("utf-8", errors="replace")
    return proc.returncode, msg


OPEN_AI_OUTPUT_SAMPLE_RATE = 24000  # must match openai_realtime.py

# Chunk size: 960 samples @ 24kHz = 40ms of audio
CHUNK_SAMPLES = 960
CHUNK_BYTES = CHUNK_SAMPLES * 2  # int16 = 2 bytes per sample
CHUNK_DURATION_S = CHUNK_SAMPLES / OPEN_AI_OUTPUT_SAMPLE_RATE  # 0.04 seconds


async def _play_mp3(path: str, deps: ToolDependencies) -> None:
    """Play MP3 by streaming decoded PCM through the fastrtc output queue.

    This avoids ALSA device contention by routing audio through the same
    pipeline that already owns the speaker.

    Audio is streamed in real-time (with rate limiting) to prevent flooding
    the output queue and to keep the OpenAI websocket connection alive.

    Also feeds audio to MusicDancer for real-time audio-reactive dancing.
    """
    handler = getattr(deps, "openai_realtime_handler", None)
    if handler is None:
        raise RuntimeError("deps.openai_realtime_handler is not set. Wire it during app startup.")

    # Get or create music dancer
    dancer = _get_music_dancer(deps)

    # Mute assistant audio deltas during song playback
    handler.mute_output_audio()

    # Decode MP3 to raw signed 16-bit PCM mono 24kHz and stream into the existing output queue.
    # mpg123 outputs 16-bit signed little-endian PCM on stdout when using -s.
    cmd = ["mpg123", "-q", "-s", "--mono", "--rate", str(OPEN_AI_OUTPUT_SAMPLE_RATE), path]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        assert proc.stdout is not None

        # Start the music dancer - dancing begins with first audio chunk
        dancer.start()
        logger.info("Started audio-reactive dancing")

        # Track timing for real-time playback
        start_time = asyncio.get_event_loop().time()
        chunks_sent = 0

        while True:
            data = await proc.stdout.read(CHUNK_BYTES)
            if not data:
                break
            # Ensure even number of bytes for int16
            if len(data) % 2 == 1:
                data = data[:-1]
                if not data:
                    continue

            pcm = np.frombuffer(data, dtype=np.int16).reshape(1, -1)

            # Feed audio to dancer for real-time movement generation
            dancer.feed(pcm.flatten())

            await handler.output_queue.put((OPEN_AI_OUTPUT_SAMPLE_RATE, pcm))
            chunks_sent += 1

            # Update activity time to prevent idle signals during song playback
            handler.last_activity_time = asyncio.get_event_loop().time()

            # Rate limit: wait until we're caught up to real-time
            # This prevents flooding the queue and keeps the system responsive
            expected_time = start_time + (chunks_sent * CHUNK_DURATION_S)
            now = asyncio.get_event_loop().time()
            sleep_time = expected_time - now
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        rc = await proc.wait()
        if rc != 0:
            err = b""
            try:
                assert proc.stderr is not None
                err = await proc.stderr.read()
            except Exception:
                pass
            raise RuntimeError(f"mpg123 decode-to-stdout failed rc={rc} stderr={err.decode(errors='ignore')[:800]}")
    finally:
        # Stop dancer with fade-out (smoothly returns to neutral over ~0.5s)
        logger.info("Song ended, stopping dancer with fade-out")
        await asyncio.to_thread(dancer.stop)
        handler.unmute_output_audio()


async def _trigger_generation_started(deps: ToolDependencies) -> None:
    """Trigger the assistant to announce that song generation has started. Waits for completion."""
    handler = deps.openai_realtime_handler
    if handler is None:
        logger.warning("No handler available for generation started announcement")
        return
    
    logger.info("Triggering generation started announcement...")
    await handler.trigger_response_and_wait(
        system_message="[SYSTEM: Song generation has started. Tell the user IN ENGLISH!]",
        response_instructions="Tell the user that you're now generating their song and it usually takes about 1 minute. Be brief and enthusiastic! Keep it to one short sentence. SPEAK ENGLISH ONLY.",
        timeout=10.0,
    )


async def _trigger_song_ready(deps: ToolDependencies) -> None:
    """Trigger the assistant to announce the song is ready. Waits for completion before playing."""
    handler = deps.openai_realtime_handler
    if handler is None:
        logger.warning("No handler available for song ready announcement")
        return
    
    logger.info("Triggering song ready announcement...")
    await handler.trigger_response_and_wait(
        system_message="[SYSTEM: The song is ready! Announce it IN ENGLISH before it plays.]",
        response_instructions="Excitedly announce that the song is ready and you're about to play it now! Keep it brief - one short excited sentence like 'Your song is ready, here it comes!' SPEAK ENGLISH ONLY.",
        timeout=10.0,
    )


async def _trigger_post_song_feedback(deps: ToolDependencies) -> None:
    """Trigger the assistant to ask for feedback after the song finishes. Waits for completion."""
    handler = deps.openai_realtime_handler
    if handler is None:
        logger.warning("No handler available for post-song feedback")
        return
    
    logger.info("Triggering post-song feedback prompt...")
    await handler.trigger_response_and_wait(
        system_message="[SYSTEM: The song just finished playing. Ask the user for their feedback IN ENGLISH!]",
        response_instructions="The song just finished playing. Ask the user what they thought of it and if they'd like another one. Be enthusiastic but brief! Do NOT generate another song without explicit request. SPEAK ENGLISH ONLY.",
        timeout=15.0,
    )


class MakeSongAndDance(Tool):
    name = "make_song_and_dance"
    description = "Generate a song from a short text prompt, then play it on Reachy and dance."
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Short description of the song you want."},
            "music_length_ms": {"type": "integer", "minimum": 10000, "maximum": 50000},
        },
        "required": ["prompt"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        prompt = (kwargs.get("prompt") or "").strip()
        if not prompt:
            return {"status": "empty prompt"}

        from dj_reachy.config import config

        # Compose call timeout
        compose_timeout_s = config.ELEVEN_MUSIC_TIMEOUT_S

        logger.info("Tool call: make_song_and_dance (ElevenLabs compose)")

        # Trigger: Announce generation started
        await _trigger_generation_started(deps)

        mp3_path = await asyncio.to_thread(_compose_music_to_mp3, prompt, compose_timeout_s, deps)

        # Trigger: Announce song is ready before playing
        await _trigger_song_ready(deps)

        # Play MP3 with real-time audio-reactive dancing
        # Dancing starts automatically when playback begins and fades out when it ends
        await _play_mp3(mp3_path, deps)
        logger.info("Song saved permanently at %s", mp3_path)
        
        # Trigger post-song feedback prompt
        await _trigger_post_song_feedback(deps)

        return {"status": "ok", "provider": "elevenlabs", "song_path": mp3_path}
