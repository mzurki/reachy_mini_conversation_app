"""Bidirectional local audio stream with optional settings UI.

In headless mode, there is no Gradio UI. If the OpenAI API key is not
available via environment/.env, we expose a minimal settings page via the
Reachy Mini Apps settings server to let non-technical users enter it.

The settings UI is served from this package's ``static/`` folder and offers a
single password field to set ``OPENAI_API_KEY``. Once set, we persist it to the
app instance's ``.env`` file (if available) and proceed to start streaming.
"""

import os
import sys
import time
import asyncio
import logging
from typing import List, Optional
from pathlib import Path

from fastrtc import AdditionalOutputs, audio_to_float32
from scipy.signal import resample

from reachy_mini import ReachyMini
from reachy_mini.media.media_manager import MediaBackend
from dj_reachy.config import config
from dj_reachy.openai_realtime import OpenaiRealtimeHandler


try:
    # FastAPI is provided by the Reachy Mini Apps runtime
    from fastapi import FastAPI, Response, Request
    from pydantic import BaseModel
    from fastapi.responses import FileResponse, JSONResponse
    from starlette.staticfiles import StaticFiles
except Exception:  # pragma: no cover - only loaded when settings_app is used
    FastAPI = object  # type: ignore
    FileResponse = object  # type: ignore
    JSONResponse = object  # type: ignore
    StaticFiles = object  # type: ignore
    BaseModel = object  # type: ignore


logger = logging.getLogger(__name__)


class LocalStream:
    """LocalStream using Reachy Mini's recorder/player."""

    def __init__(
        self,
        handler: OpenaiRealtimeHandler,
        robot: ReachyMini,
        *,
        settings_app: Optional[FastAPI] = None,
        instance_path: Optional[str] = None,
    ):
        """Initialize the stream with an OpenAI realtime handler and pipelines.

        - ``settings_app``: the Reachy Mini Apps FastAPI to attach settings endpoints.
        - ``instance_path``: directory where per-instance ``.env`` should be stored.
        """
        self.handler = handler
        self._robot = robot
        self._stop_event = asyncio.Event()
        self._tasks: List[asyncio.Task[None]] = []
        # Allow the handler to flush the player queue when appropriate.
        self.handler._clear_queue = self.clear_audio_queue
        self._settings_app: Optional[FastAPI] = settings_app
        self._instance_path: Optional[str] = instance_path
        self._settings_initialized = False
        self._asyncio_loop = None

    # ---- Settings UI (only when API key is missing) ----
    def _read_env_lines(self, env_path: Path) -> list[str]:
        """Load env file contents or a template as a list of lines."""
        inst = env_path.parent
        try:
            if env_path.exists():
                try:
                    return env_path.read_text(encoding="utf-8").splitlines()
                except Exception:
                    return []
            template_text = None
            ex = inst / ".env.example"
            if ex.exists():
                try:
                    template_text = ex.read_text(encoding="utf-8")
                except Exception:
                    template_text = None
            if template_text is None:
                try:
                    cwd_example = Path.cwd() / ".env.example"
                    if cwd_example.exists():
                        template_text = cwd_example.read_text(encoding="utf-8")
                except Exception:
                    template_text = None
            if template_text is None:
                packaged = Path(__file__).parent / ".env.example"
                if packaged.exists():
                    try:
                        template_text = packaged.read_text(encoding="utf-8")
                    except Exception:
                        template_text = None
            return template_text.splitlines() if template_text else []
        except Exception:
            return []

    def _persist_api_key(self, key: str) -> None:
        """Persist API key to environment and instance ``.env`` if possible.

        Behavior:
        - Always sets ``OPENAI_API_KEY`` in process env and in-memory config.
        - Writes/updates ``<instance_path>/.env``:
          * If ``.env`` exists, replaces/append OPENAI_API_KEY line.
          * Else, copies template from ``<instance_path>/.env.example`` when present,
            otherwise falls back to the packaged template
            ``dj_reachy/.env.example``.
          * Ensures the resulting file contains the full template plus the key.
        - Loads the written ``.env`` into the current process environment.
        """
        k = (key or "").strip()
        if not k:
            return
        # Update live process env and config so consumers see it immediately
        try:
            os.environ["OPENAI_API_KEY"] = k
        except Exception:  # best-effort
            pass
        try:
            config.OPENAI_API_KEY = k
        except Exception:
            pass

        if not self._instance_path:
            return
        try:
            inst = Path(self._instance_path)
            env_path = inst / ".env"
            lines = self._read_env_lines(env_path)
            replaced = False
            for i, ln in enumerate(lines):
                if ln.strip().startswith("OPENAI_API_KEY="):
                    lines[i] = f"OPENAI_API_KEY={k}"
                    replaced = True
                    break
            if not replaced:
                lines.append(f"OPENAI_API_KEY={k}")
            final_text = "\n".join(lines) + "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Persisted OPENAI_API_KEY to %s", env_path)

            # Load the newly written .env into this process to ensure downstream imports see it
            try:
                from dotenv import load_dotenv

                load_dotenv(dotenv_path=str(env_path), override=True)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to persist OPENAI_API_KEY: %s", e)

    def _persist_elevenlabs_key(self, key: str) -> None:
        """Persist ElevenLabs API key to environment and instance .env if possible."""
        k = (key or "").strip()
        # Update live process env
        try:
            if k:
                os.environ["ELEVENLABS_API_KEY"] = k
            else:
                os.environ.pop("ELEVENLABS_API_KEY", None)
        except Exception:
            pass
        # Also update deps if handler has them
        try:
            if hasattr(self.handler, "deps") and self.handler.deps is not None:
                self.handler.deps.elevenlabs_api_key = k if k else None
        except Exception:
            pass

        if not self._instance_path:
            return
        try:
            inst = Path(self._instance_path)
            env_path = inst / ".env"
            lines = self._read_env_lines(env_path)
            replaced = False
            for i, ln in enumerate(list(lines)):
                if ln.strip().startswith("ELEVENLABS_API_KEY="):
                    if k:
                        lines[i] = f"ELEVENLABS_API_KEY={k}"
                    else:
                        lines.pop(i)
                    replaced = True
                    break
            if k and not replaced:
                lines.append(f"ELEVENLABS_API_KEY={k}")
            if not k and not env_path.exists():
                return
            final_text = "\n".join(lines) + "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Persisted ELEVENLABS_API_KEY to %s", env_path)

            try:
                from dotenv import load_dotenv
                load_dotenv(dotenv_path=str(env_path), override=True)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to persist ELEVENLABS_API_KEY: %s", e)

    def _persist_personality(self, profile: Optional[str]) -> None:
        """Persist the startup personality to the instance .env and config."""
        selection = (profile or "").strip() or None
        try:
            from dj_reachy.config import set_custom_profile

            set_custom_profile(selection)
        except Exception:
            pass

        if not self._instance_path:
            return
        try:
            env_path = Path(self._instance_path) / ".env"
            lines = self._read_env_lines(env_path)
            replaced = False
            for i, ln in enumerate(list(lines)):
                if ln.strip().startswith("REACHY_MINI_CUSTOM_PROFILE="):
                    if selection:
                        lines[i] = f"REACHY_MINI_CUSTOM_PROFILE={selection}"
                    else:
                        lines.pop(i)
                    replaced = True
                    break
            if selection and not replaced:
                lines.append(f"REACHY_MINI_CUSTOM_PROFILE={selection}")
            if selection is None and not env_path.exists():
                return
            final_text = "\n".join(lines) + "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Persisted startup personality to %s", env_path)
            try:
                from dotenv import load_dotenv

                load_dotenv(dotenv_path=str(env_path), override=True)
            except Exception:
                pass
        except Exception as e:
            logger.warning("Failed to persist REACHY_MINI_CUSTOM_PROFILE: %s", e)

    def _read_persisted_personality(self) -> Optional[str]:
        """Read persisted startup personality from instance .env (if any)."""
        if not self._instance_path:
            return None
        env_path = Path(self._instance_path) / ".env"
        try:
            if env_path.exists():
                for ln in env_path.read_text(encoding="utf-8").splitlines():
                    if ln.strip().startswith("REACHY_MINI_CUSTOM_PROFILE="):
                        _, _, val = ln.partition("=")
                        v = val.strip()
                        return v or None
        except Exception:
            pass
        return None

    def _persist_dance_intensity(self, intensity: float) -> None:
        """Persist dance intensity to instance .env."""
        if not self._instance_path:
            return
        try:
            env_path = Path(self._instance_path) / ".env"
            lines = self._read_env_lines(env_path)
            replaced = False
            for i, ln in enumerate(list(lines)):
                if ln.strip().startswith("DANCE_INTENSITY="):
                    lines[i] = f"DANCE_INTENSITY={intensity:.2f}"
                    replaced = True
                    break
            if not replaced:
                lines.append(f"DANCE_INTENSITY={intensity:.2f}")
            final_text = "\n".join(lines) + "\n"
            env_path.write_text(final_text, encoding="utf-8")
            logger.info("Persisted dance intensity %.2f to %s", intensity, env_path)
        except Exception as e:
            logger.warning("Failed to persist dance intensity: %s", e)

    def _read_persisted_dance_intensity(self) -> float:
        """Read persisted dance intensity from instance .env (if any)."""
        if not self._instance_path:
            return 0.20  # Default (20% actual = 80% slider)
        env_path = Path(self._instance_path) / ".env"
        try:
            if env_path.exists():
                for ln in env_path.read_text(encoding="utf-8").splitlines():
                    if ln.strip().startswith("DANCE_INTENSITY="):
                        _, _, val = ln.partition("=")
                        return float(val.strip())
        except Exception:
            pass
        return 0.20  # Default (20% actual = 80% slider)

    def _init_settings_ui_if_needed(self) -> None:
        """Attach minimal settings UI to the settings app.

        Always mounts the UI when a settings_app is provided so that users
        see a confirmation message even if the API key is already configured.
        """
        if self._settings_initialized:
            return
        if self._settings_app is None:
            return

        static_dir = Path(__file__).parent / "static"
        index_file = static_dir / "index.html"

        if hasattr(self._settings_app, "mount"):
            try:
                # Serve /static/* assets
                self._settings_app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
            except Exception:
                pass

        class ApiKeyPayload(BaseModel):
            openai_api_key: str

        class ElevenLabsKeyPayload(BaseModel):
            elevenlabs_api_key: str

        # GET / -> index.html
        @self._settings_app.get("/")
        def _root() -> FileResponse:
            return FileResponse(str(index_file))

        # GET /favicon.ico -> optional, avoid noisy 404s on some browsers
        @self._settings_app.get("/favicon.ico")
        def _favicon() -> Response:
            return Response(status_code=204)

        # GET /status -> whether key is set
        @self._settings_app.get("/status")
        def _status() -> JSONResponse:
            has_key = bool(config.OPENAI_API_KEY and str(config.OPENAI_API_KEY).strip())
            return JSONResponse({"has_key": has_key})

        # GET /ready -> whether backend finished loading tools
        @self._settings_app.get("/ready")
        def _ready() -> JSONResponse:
            try:
                mod = sys.modules.get("dj_reachy.tools.core_tools")
                ready = bool(getattr(mod, "_TOOLS_INITIALIZED", False)) if mod else False
            except Exception:
                ready = False
            return JSONResponse({"ready": ready})

        # POST /openai_api_key -> set/persist key
        @self._settings_app.post("/openai_api_key")
        def _set_key(payload: ApiKeyPayload) -> JSONResponse:
            key = (payload.openai_api_key or "").strip()
            if not key:
                return JSONResponse({"ok": False, "error": "empty_key"}, status_code=400)
            self._persist_api_key(key)
            return JSONResponse({"ok": True})

        # POST /validate_api_key -> validate key without persisting it
        @self._settings_app.post("/validate_api_key")
        async def _validate_key(payload: ApiKeyPayload) -> JSONResponse:
            key = (payload.openai_api_key or "").strip()
            if not key:
                return JSONResponse({"valid": False, "error": "empty_key"}, status_code=400)

            # Try to validate by checking if we can fetch the models
            try:
                import httpx

                headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
                async with httpx.AsyncClient(timeout=10.0) as client:
                    response = await client.get("https://api.openai.com/v1/models", headers=headers)
                    if response.status_code == 200:
                        return JSONResponse({"valid": True})
                    elif response.status_code == 401:
                        return JSONResponse({"valid": False, "error": "invalid_api_key"}, status_code=401)
                    else:
                        return JSONResponse(
                            {"valid": False, "error": "validation_failed"}, status_code=response.status_code
                        )
            except Exception as e:
                logger.warning(f"API key validation failed: {e}")
                return JSONResponse({"valid": False, "error": "validation_error"}, status_code=500)

        # GET /elevenlabs_status -> whether ElevenLabs key is set
        @self._settings_app.get("/elevenlabs_status")
        def _elevenlabs_status() -> JSONResponse:
            has_key = bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())
            return JSONResponse({"has_key": has_key})

        # POST /elevenlabs_api_key -> set/persist ElevenLabs key
        @self._settings_app.post("/elevenlabs_api_key")
        async def _set_elevenlabs_key(request: "Request") -> JSONResponse:
            # Accept raw JSON to avoid Pydantic validation issues
            try:
                body = await request.json()
                key = (body.get("elevenlabs_api_key") or "").strip()
            except Exception:
                key = ""
            if not key:
                return JSONResponse({"ok": False, "error": "empty_key"}, status_code=400)
            self._persist_elevenlabs_key(key)
            return JSONResponse({"ok": True})

        # DELETE /elevenlabs_api_key -> clear ElevenLabs key
        @self._settings_app.delete("/elevenlabs_api_key")
        def _clear_elevenlabs_key() -> JSONResponse:
            self._persist_elevenlabs_key("")
            return JSONResponse({"ok": True})

        # ---- Songs Management Endpoints ----
        
        def _get_songs_metadata() -> dict:
            """Load songs metadata from JSON file."""
            meta_path = config.SONGS_DIR / "metadata.json"
            if meta_path.exists():
                try:
                    import json
                    return json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            return {}
        
        def _save_songs_metadata(metadata: dict) -> None:
            """Save songs metadata to JSON file."""
            import json
            meta_path = config.SONGS_DIR / "metadata.json"
            config.SONGS_DIR.mkdir(parents=True, exist_ok=True)
            meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
        
        # GET /songs -> list all saved songs
        @self._settings_app.get("/songs")
        def _list_songs() -> JSONResponse:
            songs = []
            songs_dir = config.SONGS_DIR
            metadata = _get_songs_metadata()
            if songs_dir.exists():
                for f in sorted(songs_dir.glob("*.mp3"), key=lambda x: x.stat().st_mtime, reverse=True):
                    stat = f.stat()
                    # Parse filename to extract timestamp and prompt
                    name = f.stem
                    parts = name.split("_", 1)
                    timestamp = int(parts[0]) if parts[0].isdigit() else 0
                    default_title = parts[1].replace("_", " ") if len(parts) > 1 else name
                    # Use custom title from metadata if available
                    song_meta = metadata.get(f.name, {})
                    title = song_meta.get("title", default_title)
                    songs.append({
                        "filename": f.name,
                        "title": title,
                        "prompt": default_title,  # Original prompt for reference
                        "timestamp": timestamp,
                        "size": stat.st_size,
                    })
            return JSONResponse({"songs": songs})

        # GET /songs/{filename} -> serve song file for playback
        @self._settings_app.get("/songs/{filename}")
        def _get_song(filename: str) -> FileResponse:
            song_path = config.SONGS_DIR / filename
            if song_path.exists() and song_path.suffix == ".mp3":
                return FileResponse(
                    str(song_path),
                    media_type="audio/mpeg",
                    filename=filename,
                )
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

        # DELETE /songs/{filename} -> delete a specific song
        @self._settings_app.delete("/songs/{filename}")
        def _delete_song(filename: str) -> JSONResponse:
            song_path = config.SONGS_DIR / filename
            if song_path.exists() and song_path.suffix == ".mp3":
                try:
                    song_path.unlink()
                    # Also remove from metadata
                    metadata = _get_songs_metadata()
                    if filename in metadata:
                        del metadata[filename]
                        _save_songs_metadata(metadata)
                    logger.info("Deleted song: %s", filename)
                    return JSONResponse({"ok": True})
                except Exception as e:
                    logger.warning("Failed to delete song %s: %s", filename, e)
                    return JSONResponse({"ok": False, "error": str(e)}, status_code=500)
            return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)

        # PATCH /songs/{filename} -> update song metadata (title)
        @self._settings_app.patch("/songs/{filename}")
        async def _update_song(filename: str, request: "Request") -> JSONResponse:
            song_path = config.SONGS_DIR / filename
            if not song_path.exists() or song_path.suffix != ".mp3":
                return JSONResponse({"ok": False, "error": "not_found"}, status_code=404)
            
            try:
                body = await request.json()
                title = body.get("title", "").strip()
                if not title:
                    return JSONResponse({"ok": False, "error": "empty_title"}, status_code=400)
                
                metadata = _get_songs_metadata()
                if filename not in metadata:
                    metadata[filename] = {}
                metadata[filename]["title"] = title
                _save_songs_metadata(metadata)
                
                logger.info("Updated song title: %s -> %s", filename, title)
                return JSONResponse({"ok": True, "title": title})
            except Exception as e:
                logger.warning("Failed to update song %s: %s", filename, e)
                return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

        # ---- Dance Settings Endpoints ----

        # GET /dance_settings -> get current dance intensity
        @self._settings_app.get("/dance_settings")
        def _get_dance_settings() -> JSONResponse:
            try:
                from dj_reachy.profiles.dj_reachy.make_song_and_dance import get_dance_intensity
                intensity = get_dance_intensity()
                # Clamp to valid range (0-25% actual = 0-100% on slider)
                intensity = max(0.0, min(0.25, intensity))
            except Exception:
                intensity = 0.20  # Default (80% on slider = 20% actual)
            return JSONResponse({"intensity": intensity})

        # POST /dance_settings -> set dance intensity and persist
        @self._settings_app.post("/dance_settings")
        async def _set_dance_settings(request: "Request") -> JSONResponse:
            try:
                body = await request.json()
                intensity = body.get("intensity")
                if intensity is None:
                    return JSONResponse({"ok": False, "error": "missing_intensity"}, status_code=400)
                
                intensity = float(intensity)
                if not (0.0 <= intensity <= 0.25):
                    return JSONResponse({"ok": False, "error": "intensity_out_of_range"}, status_code=400)
                
                # Apply intensity immediately
                try:
                    from dj_reachy.profiles.dj_reachy.make_song_and_dance import set_dance_intensity
                    set_dance_intensity(intensity)
                except Exception as e:
                    logger.warning("Failed to set dance intensity in memory: %s", e)
                
                # Persist to .env
                self._persist_dance_intensity(intensity)
                
                return JSONResponse({"ok": True, "intensity": intensity})
            except ValueError:
                return JSONResponse({"ok": False, "error": "invalid_intensity"}, status_code=400)
            except Exception as e:
                logger.warning("Failed to set dance settings: %s", e)
                return JSONResponse({"ok": False, "error": str(e)}, status_code=500)

        self._settings_initialized = True

    def launch(self) -> None:
        """Start the recorder/player and run the async processing loops.

        If the OpenAI key is missing, expose a tiny settings UI via the
        Reachy Mini settings server to collect it before starting streams.
        """
        self._stop_event.clear()

        # Try to load an existing instance .env first (covers subsequent runs)
        if self._instance_path:
            try:
                from dotenv import load_dotenv

                from dj_reachy.config import set_custom_profile

                env_path = Path(self._instance_path) / ".env"
                if env_path.exists():
                    load_dotenv(dotenv_path=str(env_path), override=True)
                    # Update config with newly loaded values
                    new_key = os.getenv("OPENAI_API_KEY", "").strip()
                    if new_key:
                        try:
                            config.OPENAI_API_KEY = new_key
                        except Exception:
                            pass
                    new_profile = os.getenv("REACHY_MINI_CUSTOM_PROFILE")
                    if new_profile is not None:
                        try:
                            set_custom_profile(new_profile.strip() or None)
                        except Exception:
                            pass
                    # Load persisted dance intensity
                    dance_intensity_str = os.getenv("DANCE_INTENSITY", "").strip()
                    if dance_intensity_str:
                        try:
                            from dj_reachy.profiles.dj_reachy.make_song_and_dance import set_dance_intensity
                            set_dance_intensity(float(dance_intensity_str))
                            logger.info("Loaded dance intensity: %s", dance_intensity_str)
                        except Exception:
                            pass
            except Exception:
                pass

        # If key is still missing, try to download one from HuggingFace
        if not (config.OPENAI_API_KEY and str(config.OPENAI_API_KEY).strip()):
            logger.info("OPENAI_API_KEY not set, attempting to download from HuggingFace...")
            try:
                from gradio_client import Client
                client = Client("HuggingFaceM4/gradium_setup", verbose=False)
                key, status = client.predict(api_name="/claim_b_key")
                if key and key.strip():
                    logger.info("Successfully downloaded API key from HuggingFace")
                    # Persist it immediately
                    self._persist_api_key(key)
            except Exception as e:
                logger.warning(f"Failed to download API key from HuggingFace: {e}")

        # Always expose settings UI if a settings app is available
        # (do this AFTER loading/downloading the key so status endpoint sees the right value)
        self._init_settings_ui_if_needed()

        # If key is still missing -> wait until provided via the settings UI
        if not (config.OPENAI_API_KEY and str(config.OPENAI_API_KEY).strip()):
            logger.warning("OPENAI_API_KEY not found. Open the app settings page to enter it.")
            # Poll until the key becomes available (set via the settings UI)
            try:
                while not (config.OPENAI_API_KEY and str(config.OPENAI_API_KEY).strip()):
                    time.sleep(0.2)
            except KeyboardInterrupt:
                logger.info("Interrupted while waiting for API key.")
                return

        # Start media after key is set/available
        self._robot.media.start_recording()
        self._robot.media.start_playing()
        time.sleep(1)  # give some time to the pipelines to start

        async def runner() -> None:
            # Capture loop for cross-thread personality actions
            loop = asyncio.get_running_loop()
            self._asyncio_loop = loop  # type: ignore[assignment]
            # Personality routes removed - using fixed dj_reachy profile
            self._tasks = [
                asyncio.create_task(self.handler.start_up(), name="openai-handler"),
                asyncio.create_task(self.record_loop(), name="stream-record-loop"),
                asyncio.create_task(self.play_loop(), name="stream-play-loop"),
            ]
            try:
                await asyncio.gather(*self._tasks)
            except asyncio.CancelledError:
                logger.info("Tasks cancelled during shutdown")
            finally:
                # Ensure handler connection is closed
                await self.handler.shutdown()

        asyncio.run(runner())

    def close(self) -> None:
        """Stop the stream and underlying media pipelines.

        This method:
        - Stops audio recording and playback first
        - Sets the stop event to signal async loops to terminate
        - Cancels all pending async tasks (openai-handler, record-loop, play-loop)
        """
        logger.info("Stopping LocalStream...")

        # Stop media pipelines FIRST before cancelling async tasks
        # This ensures clean shutdown before PortAudio cleanup
        try:
            self._robot.media.stop_recording()
        except Exception as e:
            logger.debug(f"Error stopping recording (may already be stopped): {e}")

        try:
            self._robot.media.stop_playing()
        except Exception as e:
            logger.debug(f"Error stopping playback (may already be stopped): {e}")

        # Now signal async loops to stop
        self._stop_event.set()

        # Cancel all running tasks
        for task in self._tasks:
            if not task.done():
                task.cancel()

    def clear_audio_queue(self) -> None:
        """Flush the player's appsrc to drop any queued audio immediately."""
        logger.info("User intervention: flushing player queue")
        if self._robot.media.backend == MediaBackend.GSTREAMER:
            # Directly flush gstreamer audio pipe
            self._robot.media.audio.clear_player()
        elif self._robot.media.backend == MediaBackend.DEFAULT or self._robot.media.backend == MediaBackend.DEFAULT_NO_VIDEO:
            self._robot.media.audio.clear_output_buffer()
        self.handler.output_queue = asyncio.Queue()

    async def record_loop(self) -> None:
        """Read mic frames from the recorder and forward them to the handler."""
        input_sample_rate = self._robot.media.get_input_audio_samplerate()
        logger.debug(f"Audio recording started at {input_sample_rate} Hz")

        while not self._stop_event.is_set():
            audio_frame = self._robot.media.get_audio_sample()
            if audio_frame is not None:
                await self.handler.receive((input_sample_rate, audio_frame))
            await asyncio.sleep(0)  # avoid busy loop

    async def play_loop(self) -> None:
        """Fetch outputs from the handler: log text and play audio frames."""
        while not self._stop_event.is_set():
            handler_output = await self.handler.emit()

            if isinstance(handler_output, AdditionalOutputs):
                for msg in handler_output.args:
                    content = msg.get("content", "")
                    if isinstance(content, str):
                        logger.info(
                            "role=%s content=%s",
                            msg.get("role"),
                            content if len(content) < 500 else content[:500] + "…",
                        )

            elif isinstance(handler_output, tuple):
                input_sample_rate, audio_data = handler_output
                output_sample_rate = self._robot.media.get_output_audio_samplerate()

                # Reshape if needed
                if audio_data.ndim == 2:
                    # Scipy channels last convention
                    if audio_data.shape[1] > audio_data.shape[0]:
                        audio_data = audio_data.T
                    # Multiple channels -> Mono channel
                    if audio_data.shape[1] > 1:
                        audio_data = audio_data[:, 0]

                # Cast if needed
                audio_frame = audio_to_float32(audio_data)

                # Resample if needed
                if input_sample_rate != output_sample_rate:
                    audio_frame = resample(
                        audio_frame,
                        int(len(audio_frame) * output_sample_rate / input_sample_rate),
                    )

                self._robot.media.push_audio_sample(audio_frame)

            else:
                logger.debug("Ignoring output type=%s", type(handler_output).__name__)

            await asyncio.sleep(0)  # yield to event loop
