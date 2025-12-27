import asyncio
import logging
import os
import tempfile
import time
from typing import Any, Dict, Optional

import requests

from reachy_mini.utils import create_head_pose
from reachy_mini_conversation_app.dance_emotion_moves import GotoQueueMove
from reachy_mini_conversation_app.tools.core_tools import Tool, ToolDependencies


logger = logging.getLogger(__name__)

AIML_POST_URL = "https://api.aimlapi.com/v2/generate/audio"
AIML_GET_URL = "https://api.aimlapi.com/v2/generate/audio"


def _aiml_headers() -> Dict[str, str]:
    key = os.environ.get("AIMLAPI_KEY")
    if not key:
        raise RuntimeError("Missing AIMLAPI_KEY env var")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _start_job(prompt: str, length_ms: int) -> str:
    payload = {"model": "elevenlabs/eleven_music", "prompt": prompt, "music_length_ms": length_ms}
    r = requests.post(AIML_POST_URL, json=payload, headers=_aiml_headers(), timeout=30)
    r.raise_for_status()
    j = r.json()
    job_id = j.get("id")
    if not job_id:
        raise RuntimeError(f"Unexpected AIMLAPI response (no id): {j}")
    return job_id


def _poll_job(job_id: str, timeout_s: int, poll_s: float) -> str:
    t0 = time.time()
    while time.time() - t0 < timeout_s:
        r = requests.get(AIML_GET_URL, params={"generation_id": job_id}, headers=_aiml_headers(), timeout=30)
        r.raise_for_status()
        j = r.json()

        status = j.get("status")
        if status == "completed":
            url = (j.get("audio_file") or {}).get("url")
            if url:
                return url

        if status in {"failed", "error"}:
            raise RuntimeError(f"AIMLAPI job failed: {j}")

        time.sleep(poll_s)

    raise TimeoutError("Music generation timed out")


def _download_mp3(url: str) -> str:
    fd, path = tempfile.mkstemp(prefix="reachy_song_", suffix=".mp3")
    os.close(fd)
    with requests.get(url, stream=True, timeout=90) as r:
        r.raise_for_status()
        with open(path, "wb") as f:
            for chunk in r.iter_content(chunk_size=1024 * 256):
                if chunk:
                    f.write(chunk)
    return path


async def _play_mp3(path: str) -> None:
    """
    Play MP3 on Reachy using mpg123 and force output to the working USB device (card 0).
    This avoids ALSA default-device issues.
    """
    cmd = ["mpg123", "-a", "hw:0,0", "-q", path]
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"mpg123 failed with code {proc.returncode}")


def _queue_simple_dance(deps: ToolDependencies, duration_s: float) -> None:
    deps.movement_manager.clear_move_queue()

    current_head_pose = deps.reachy_mini.get_current_head_pose()
    head_joints, antenna_joints = deps.reachy_mini.get_current_joint_positions()

    current_body_yaw = head_joints[0]
    a1, a2 = antenna_joints[0], antenna_joints[1]

    yaw_amp = 0.35
    pitch_amp = 0.22
    body_amp = 0.22
    ant_amp = 0.30

    step = 0.45
    steps = max(1, int(duration_s / step))

    last_pose = current_head_pose
    last_body = current_body_yaw
    last_ant = (a1, a2)

    for i in range(steps):
        phase = i % 4

        if phase == 0:
            target_pose = create_head_pose(0, 0, 0, pitch_amp, 0, 0, degrees=False)
            target_body = current_body_yaw
            target_ant = (a1 + ant_amp, a2 - ant_amp)
        elif phase == 1:
            target_pose = create_head_pose(0, 0, 0, 0, 0, yaw_amp, degrees=False)
            target_body = current_body_yaw + body_amp
            target_ant = (a1 - ant_amp, a2 + ant_amp)
        elif phase == 2:
            target_pose = create_head_pose(0, 0, 0, -pitch_amp * 0.6, 0, 0, degrees=False)
            target_body = current_body_yaw
            target_ant = (a1 + ant_amp * 0.5, a2 + ant_amp * 0.5)
        else:
            target_pose = create_head_pose(0, 0, 0, 0, 0, -yaw_amp, degrees=False)
            target_body = current_body_yaw - body_amp
            target_ant = (a1 - ant_amp * 0.5, a2 - ant_amp * 0.5)

        deps.movement_manager.queue_move(
            GotoQueueMove(
                target_head_pose=target_pose,
                start_head_pose=last_pose,
                target_antennas=target_ant,
                start_antennas=last_ant,
                target_body_yaw=target_body,
                start_body_yaw=last_body,
                duration=step,
            )
        )

        last_pose = target_pose
        last_body = target_body
        last_ant = target_ant

    center_pose = create_head_pose(0, 0, 0, 0, 0, 0, degrees=False)
    deps.movement_manager.queue_move(
        GotoQueueMove(
            target_head_pose=center_pose,
            start_head_pose=last_pose,
            target_antennas=(a1, a2),
            start_antennas=last_ant,
            target_body_yaw=current_body_yaw,
            start_body_yaw=last_body,
            duration=1.2,
        )
    )

    deps.movement_manager.set_moving_state(duration_s + 1.2)


class MakeSongAndDance(Tool):
    name = "make_song_and_dance"
    description = "Generate a song from a short text prompt, then play it on Reachy and dance."
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {"type": "string", "description": "Short description of the song you want."},
            "music_length_ms": {"type": "integer", "minimum": 10000, "maximum": 180000},
        },
        "required": ["prompt"],
    }

    async def __call__(self, deps: ToolDependencies, **kwargs: Any) -> Dict[str, Any]:
        prompt = (kwargs.get("prompt") or "").strip()
        if not prompt:
            return {"status": "empty prompt"}

        default_len = int(os.environ.get("ELEVEN_MUSIC_LENGTH_MS", "30000"))
        music_length_ms = int(kwargs.get("music_length_ms", default_len))
        poll_s = float(os.environ.get("AIMLAPI_POLL_S", "3"))
        timeout_s = int(os.environ.get("AIMLAPI_TIMEOUT_S", "240"))

        logger.info("Tool call: make_song_and_dance")

        job_id = await asyncio.to_thread(_start_job, prompt, music_length_ms)
        audio_url = await asyncio.to_thread(_poll_job, job_id, timeout_s, poll_s)
        mp3_path = await asyncio.to_thread(_download_mp3, audio_url)

        duration_s = music_length_ms / 1000.0
        _queue_simple_dance(deps, duration_s)

        try:
            await _play_mp3(mp3_path)
        finally:
            try:
                os.remove(mp3_path)
            except OSError:
                pass

        return {"status": "ok", "generation_id": job_id, "music_length_ms": music_length_ms}