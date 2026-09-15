"""TX backends: mock (default) and supervised pi_fm_rds (no auto-start)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import time
import wave
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Dict, List, Optional


class TxBackend(ABC):
    @abstractmethod
    def start(
        self,
        frequency_mhz: float,
        audio_path: str,
        rds_ps: str,
        rds_rt: str,
        rds_pi: str,
    ) -> None:
        raise NotImplementedError

    @abstractmethod
    def stop(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def is_running(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def status(self) -> Dict[str, Any]:
        raise NotImplementedError


class MockTxBackend(TxBackend):
    """Simulates carrier without touching GPIO/RF."""

    def __init__(self) -> None:
        self._running = False
        self._meta = {}  # type: Dict[str, Any]
        self._started_at = None  # type: Optional[float]
        # Test hooks: slow/cancellable "WAV prep" without RF.
        self.prefetch_delay_s = 0.0
        self.prefetch_fail = False

    def prefetch_audio(self, audio_path: str, should_cancel=None) -> Dict[str, Any]:
        from .tx_process import StartCancelled

        if self.prefetch_fail:
            raise RuntimeError("simulated prefetch failure")
        t0 = time.time()
        while time.time() - t0 < float(self.prefetch_delay_s or 0):
            if callable(should_cancel) and should_cancel():
                raise StartCancelled("prefetch cancelled")
            time.sleep(0.05)
        return {"source_path": audio_path, "wav_path": audio_path, "converted": False}

    def clear_prefetch(self) -> None:
        return

    def start(
        self,
        frequency_mhz: float,
        audio_path: str,
        rds_ps: str,
        rds_rt: str,
        rds_pi: str,
    ) -> None:
        self.stop()
        if audio_path and not os.path.isfile(audio_path):
            raise FileNotFoundError(
                "Music file missing: {}. Choose another playlist track.".format(
                    audio_path
                )
            )
        self._running = True
        self._started_at = time.time()
        self._meta = {
            "backend": "mock",
            "frequency_mhz": frequency_mhz,
            "audio_path": audio_path,
            "wav_path": audio_path,
            "rds_ps": rds_ps,
            "rds_rt": rds_rt,
            "rds_pi": rds_pi,
            "audio_mode": "mock",
            "program": "audio",
        }

    def start_silence(
        self,
        frequency_mhz: float,
        rds_ps: str,
        rds_rt: str,
        rds_pi: str,
    ) -> None:
        """Keep simulated carrier while program is paused/stopped."""
        self._running = True
        if self._started_at is None:
            self._started_at = time.time()
        self._meta = {
            "backend": "mock",
            "frequency_mhz": frequency_mhz,
            "audio_path": "(silence)",
            "wav_path": "(silence)",
            "rds_ps": rds_ps,
            "rds_rt": rds_rt,
            "rds_pi": rds_pi,
            "audio_mode": "mock_silence",
            "program": "silence",
        }

    def stop(self) -> None:
        self._running = False
        self._started_at = None
        self._meta = {}

    def is_running(self) -> bool:
        return self._running

    def status(self) -> Dict[str, Any]:
        out = {"backend": "mock", "running": self._running}
        out.update(self._meta)
        if self._started_at:
            out["uptime_s"] = time.time() - self._started_at
        return out


_probe_cache = {"t": 0.0, "path": None, "result": None}  # type: Dict[str, Any]


def probe_real_tx_readiness(binary_path: str, deep: bool = False) -> Dict[str, Any]:
    """Check real transmitter software WITHOUT keying RF."""
    now = time.time()
    if (
        not deep
        and _probe_cache["path"] == binary_path
        and _probe_cache["result"] is not None
        and now - float(_probe_cache["t"]) < 30.0
    ):
        return dict(_probe_cache["result"])  # type: ignore[arg-type]

    ffmpeg = shutil.which("ffmpeg")
    sox = shutil.which("sox")
    exists = bool(binary_path) and os.path.isfile(binary_path)
    executable = exists and os.access(binary_path, os.X_OK)
    loads = None  # type: Optional[bool]
    probe_note = ""
    if deep and executable:
        try:
            r = subprocess.run(
                [binary_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=3,
            )
            text = (r.stderr or r.stdout or b"").decode("utf-8", errors="replace")
            loads = True
            if "Permission denied" in text or "/dev/mem" in text:
                probe_note = "Program loads; RF needs privileged start (not done here)."
            else:
                probe_note = "Program responds."
        except Exception as exc:  # noqa: BLE001
            loads = False
            probe_note = "Could not probe program: {}".format(exc)

    ready = executable and bool(ffmpeg) and (loads is not False)
    missing = []
    if not executable:
        missing.append("transmitter program")
    if not ffmpeg:
        missing.append("ffmpeg (needed to prepare MP3 for FM)")
    summary = (
        "Live FM software ready"
        if ready
        else "Not ready: missing " + (", ".join(missing) or "components")
    )
    result = {
        "ready": ready,
        "binary_path": binary_path,
        "binary_exists": exists,
        "binary_executable": executable,
        "binary_loads": loads,
        "ffmpeg": ffmpeg,
        "sox": sox,
        "mp3_pipeline": bool(ffmpeg),
        "summary": summary,
        "probe_note": probe_note,
        "rf_keyed": False,
        "audio_strategy": "seekable_temp_wav",
    }
    if not deep:
        _probe_cache["t"] = now
        _probe_cache["path"] = binary_path
        _probe_cache["result"] = result
    return dict(result)


def inspect_wav(path: str) -> Dict[str, Any]:
    """Verify a WAV is seekable PCM suitable for pi_fm_rds looping."""
    info = {
        "path": path,
        "exists": os.path.isfile(path),
        "size_bytes": os.path.getsize(path) if os.path.isfile(path) else 0,
        "seekable": False,
        "channels": None,
        "sample_rate": None,
        "sample_width": None,
        "frames": None,
        "duration_s": None,
        "ok": False,
        "error": None,
    }  # type: Dict[str, Any]
    if not info["exists"]:
        info["error"] = "missing"
        return info
    try:
        with wave.open(path, "rb") as wf:
            info["channels"] = wf.getnchannels()
            info["sample_rate"] = wf.getframerate()
            info["sample_width"] = wf.getsampwidth()
            info["frames"] = wf.getnframes()
            # Prove rewind works (the Test #2 stdin failure mode).
            if info["frames"] and info["frames"] > 0:
                wf.readframes(min(1024, info["frames"]))
                wf.rewind()
                info["seekable"] = True
            else:
                info["seekable"] = True
            rate = float(info["sample_rate"] or 0)
            frames = float(info["frames"] or 0)
            info["duration_s"] = (frames / rate) if rate else None
            info["ok"] = bool(
                info["seekable"]
                and info["channels"] in (1, 2)
                and info["sample_rate"] in (44100, 48000)
                and info["sample_width"] == 2
            )
            if not info["ok"] and info["error"] is None:
                info["error"] = "unexpected WAV format"
    except wave.Error as exc:
        info["error"] = str(exc)
    except OSError as exc:
        info["error"] = str(exc)
    return info


def wav_cache_key(audio_path: str) -> str:
    """Stable identity for a source file (path + mtime + size)."""
    st = os.stat(audio_path)
    raw = "{}:{}:{}".format(os.path.abspath(audio_path), int(st.st_mtime), st.st_size)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def wav_cache_dir(work_dir: Path) -> Path:
    return Path(work_dir) / "cache"


def is_cached_wav_path(path: Optional[str], work_dir: Path) -> bool:
    if not path:
        return False
    try:
        return Path(path).resolve().parent == wav_cache_dir(work_dir).resolve()
    except OSError:
        return False


def prepare_seekable_wav(audio_path: str, work_dir: Path) -> Dict[str, Any]:
    """Convert any supported audio to a seekable WAV file.

    pi_fm_rds tries to sf_seek() on EOF to loop; stdin pipes cannot rewind and
    terminate with "Could not rewind in audio file".

    Durable cache under work_dir/cache keyed by source identity/mtime/size so
    the same MP3 is not re-transcoded on every Play/Next/Raise.

    Returns a diagnostics dict including wav_path. Never returns '-' / stdin.
    """
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    if not os.path.isfile(audio_path):
        raise FileNotFoundError("Music file not found: {}".format(audio_path))

    ext = os.path.splitext(audio_path)[1].lower()
    result = {
        "source_path": audio_path,
        "source_basename": os.path.basename(audio_path),
        "converted": False,
        "cached": False,
        "cache_hit": False,
        "ffmpeg_ok": None,
        "ffmpeg_cmd": None,
        "wav_path": None,
        "wav": None,
        "error": None,
    }  # type: Dict[str, Any]

    if ext == ".wav":
        # Still verify; may already be suitable.
        info = inspect_wav(audio_path)
        if info.get("ok"):
            result["wav_path"] = audio_path
            result["wav"] = info
            result["converted"] = False
            return result

    cache_root = wav_cache_dir(work_dir)
    cache_root.mkdir(parents=True, exist_ok=True)
    key = wav_cache_key(audio_path)
    cached_path = cache_root / (key + ".wav")
    if cached_path.is_file():
        info = inspect_wav(str(cached_path))
        if info.get("ok"):
            result["wav_path"] = str(cached_path)
            result["wav"] = info
            result["converted"] = False
            result["cached"] = True
            result["cache_hit"] = True
            result["ffmpeg_ok"] = True
            return result
        try:
            cached_path.unlink()
        except OSError:
            pass

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("ffmpeg is required to prepare this track for FM.")

    fd, out = tempfile.mkstemp(prefix="pifm-", suffix=".wav", dir=str(work_dir))
    os.close(fd)
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        audio_path,
        "-f",
        "wav",
        "-ar",
        "44100",
        "-ac",
        "2",
        "-acodec",
        "pcm_s16le",
        out,
    ]
    result["ffmpeg_cmd"] = cmd
    try:
        subprocess.check_call(cmd, timeout=600)
        result["ffmpeg_ok"] = True
    except Exception as exc:  # noqa: BLE001
        result["ffmpeg_ok"] = False
        result["error"] = str(exc)
        try:
            os.unlink(out)
        except OSError:
            pass
        raise RuntimeError(
            "ffmpeg failed converting {!r}: {}".format(
                os.path.basename(audio_path), exc
            )
        )

    info = inspect_wav(out)
    result["wav"] = info
    if not info.get("ok"):
        try:
            os.unlink(out)
        except OSError:
            pass
        raise RuntimeError(
            "Converted WAV not acceptable for FM: {}".format(info.get("error"))
        )

    # Promote into durable cache; fall back to temp path if rename fails.
    try:
        os.replace(out, str(cached_path))
        result["wav_path"] = str(cached_path)
        result["cached"] = True
        result["cache_hit"] = False
    except OSError:
        result["wav_path"] = out
        result["cached"] = False
        result["cache_hit"] = False
    result["converted"] = True
    return result


def ensure_silence_wav(path: Path, duration_s: int = 30) -> Path:
    """Create a short stereo 44.1 kHz silence WAV that pi_fm_rds can loop forever.

    A+ disk-friendly: ~5 MB for 30s. pi_fm_rds sf_seek()s to 0 at EOF, so pause
    hold is indefinite without a huge silence file.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        info = inspect_wav(str(path))
        if info.get("ok") and info.get("seekable"):
            return path
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Cannot create silence asset: ffmpeg missing.")
    subprocess.check_call(
        [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=44100:cl=stereo",
            "-t",
            str(int(duration_s)),
            "-c:a",
            "pcm_s16le",
            str(path),
        ],
        timeout=120,
    )
    info = inspect_wav(str(path))
    if not info.get("ok"):
        raise RuntimeError("Silence WAV failed validation: {}".format(info.get("error")))
    return path


def build_pi_fm_command(
    binary_path: str,
    frequency_mhz: float,
    wav_path: str,
    rds_ps: str,
    rds_rt: str,
    rds_pi: str,
) -> List[str]:
    if not wav_path or wav_path == "-":
        raise ValueError("pi_fm_rds requires a seekable WAV path; stdin '-' is forbidden")
    if not os.path.isfile(wav_path):
        raise FileNotFoundError("WAV missing for TX command: {}".format(wav_path))
    return [
        "sudo",
        binary_path,
        "-freq",
        str(frequency_mhz),
        "-pi",
        rds_pi,
        "-ps",
        (rds_ps or "piFM")[:8],
        "-rt",
        (rds_rt or "")[:64],
        "-audio",
        wav_path,
    ]


from .tx_process import (
    DuplicateTransmitterError,
    OwnedTxProcess,
    count_transmitters,
    ensure_no_transmitters,
    fake_tx_command,
    list_transmitter_processes,
)


def kill_all_transmitters() -> Dict[str, Any]:
    """Idempotent emergency clear of every TX-like worker."""
    # Prefer non-sudo first (fake backend / unit tests). Escalate only if needed.
    try:
        result = ensure_no_transmitters(allow_clean=True, use_sudo=False)
        if not result.get("clear"):
            result = ensure_no_transmitters(allow_clean=True, use_sudo=True)
        result["remaining"] = list_transmitter_processes()
        result["clear"] = count_transmitters() == 0
        return result
    except DuplicateTransmitterError as exc:
        return {
            "clear": False,
            "remaining": list_transmitter_processes(),
            "error": str(exc),
        }


class FakeProcessTxBackend(TxBackend):
    """Real subprocess lifecycle without RF — labeled pifm-fake-tx-hold."""

    def __init__(self, work_dir: Optional[Path] = None) -> None:
        self.work_dir = Path(work_dir) if work_dir else Path("/tmp/pifm-fake-tx")
        self.work_dir.mkdir(parents=True, exist_ok=True)
        self._owned = OwnedTxProcess(use_sudo_kill=False)
        self._meta = {}  # type: Dict[str, Any]
        self._started_at = None  # type: Optional[float]
        self._marker = self.work_dir / "fake-tx.pid"
        self.prefetch_delay_s = 0.0
        self.prefetch_fail = False

    def prefetch_audio(self, audio_path: str, should_cancel=None) -> Dict[str, Any]:
        from .tx_process import StartCancelled

        if self.prefetch_fail:
            raise RuntimeError("simulated prefetch failure")
        t0 = time.time()
        while time.time() - t0 < float(self.prefetch_delay_s or 0):
            if callable(should_cancel) and should_cancel():
                raise StartCancelled("prefetch cancelled")
            time.sleep(0.05)
        return {"source_path": audio_path, "wav_path": audio_path, "converted": False}

    def clear_prefetch(self) -> None:
        return

    def start(
        self,
        frequency_mhz: float,
        audio_path: str,
        rds_ps: str,
        rds_rt: str,
        rds_pi: str,
    ) -> None:
        self.stop()
        if audio_path and audio_path != "(silence)" and not os.path.isfile(audio_path):
            raise FileNotFoundError("Music file missing: {}".format(audio_path))
        spawn = self._owned.spawn(fake_tx_command(str(self._marker)))
        time.sleep(0.15)
        if not self._owned.is_running():
            raise RuntimeError("fake TX exited immediately")
        if count_transmitters() > 1:
            self.stop()
            raise DuplicateTransmitterError("fake TX duplicate after start")
        self._started_at = time.time()
        self._meta = {
            "backend": "fake",
            "frequency_mhz": frequency_mhz,
            "audio_path": audio_path,
            "wav_path": audio_path,
            "rds_ps": rds_ps,
            "rds_rt": rds_rt,
            "rds_pi": rds_pi,
            "pid": spawn.get("worker_pid"),
            "launcher_pid": spawn.get("launcher_pid"),
            "worker_pid": spawn.get("worker_pid"),
            "audio_mode": "fake_process",
            "program": "audio",
            "started_at": self._started_at,
            "ownership": "OwnedTxProcess",
        }

    def start_silence(
        self,
        frequency_mhz: float,
        rds_ps: str,
        rds_rt: str,
        rds_pi: str,
    ) -> None:
        self.start(frequency_mhz, "(silence)", rds_ps, rds_rt, rds_pi)
        self._meta["program"] = "silence"
        self._meta["audio_mode"] = "fake_silence"

    def stop(self) -> None:
        audit = self._owned.terminate()
        if self._meta is not None:
            self._meta["stop_audit"] = audit
            self._meta["stopped_at"] = time.time()
        self._started_at = None
        try:
            if self._marker.is_file():
                self._marker.unlink()
        except OSError:
            pass

    def is_running(self) -> bool:
        return self._owned.is_running()

    def status(self) -> Dict[str, Any]:
        running = self.is_running()
        out = {"backend": "fake", "running": running}
        out.update(self._meta)
        if running and self._started_at:
            out["uptime_s"] = time.time() - self._started_at
        # Do not re-scan /proc here — Controller.status() already reconciles.
        return out


class PiFmRdsBackend(TxBackend):
    """Supervises pi_fm_rds using seekable WAV + OwnedTxProcess."""

    def __init__(
        self,
        binary_path: str,
        log_dir: Optional[Path] = None,
        work_dir: Optional[Path] = None,
        silence_wav: Optional[Path] = None,
    ) -> None:
        self.binary_path = binary_path
        self.log_dir = Path(log_dir) if log_dir else Path("/tmp")
        self.work_dir = Path(work_dir) if work_dir else self.log_dir / "wav"
        self.silence_wav = Path(silence_wav) if silence_wav else None
        self._owned = OwnedTxProcess(use_sudo_kill=True)
        self._meta = {}  # type: Dict[str, Any]
        self._temp_wav = None  # type: Optional[str]
        self._stderr_path = None  # type: Optional[Path]
        self._started_at = None  # type: Optional[float]
        self._prefetch = None  # type: Optional[Dict[str, Any]]

    def prefetch_audio(self, audio_path: str, should_cancel=None) -> Dict[str, Any]:
        """MP3→WAV conversion outside the controller lock (Pi A+ friendly)."""
        from .tx_process import StartCancelled

        if not os.path.isfile(audio_path):
            raise FileNotFoundError(
                "Music file not found: {}".format(os.path.basename(audio_path))
            )
        if callable(should_cancel) and should_cancel():
            raise StartCancelled("prefetch cancelled before convert")
        prep = prepare_seekable_wav(audio_path, self.work_dir)
        if callable(should_cancel) and should_cancel():
            # Convert finished but start was cancelled — drop non-cached temp only.
            if prep.get("converted") and prep.get("wav_path") and not prep.get("cached"):
                try:
                    os.unlink(str(prep["wav_path"]))
                except OSError:
                    pass
            raise StartCancelled("prefetch cancelled after convert")
        self._prefetch = prep
        return prep

    def clear_prefetch(self) -> None:
        prep = self._prefetch
        self._prefetch = None
        if not prep:
            return
        # Never delete durable cache entries — only ephemeral temp converts.
        if prep.get("cached"):
            return
        if not prep.get("converted"):
            return
        path = prep.get("wav_path")
        if path and os.path.isfile(path) and not is_cached_wav_path(path, self.work_dir):
            try:
                os.unlink(path)
            except OSError:
                pass

    def warm_cache(self, audio_path: str, should_cancel=None) -> Dict[str, Any]:
        """Opportunistically prepare a track into the durable WAV cache.

        Does not touch the active prefetch slot used for the current start.
        """
        from .tx_process import StartCancelled

        if not audio_path or not os.path.isfile(audio_path):
            return {"ok": False, "error": "missing"}
        if callable(should_cancel) and should_cancel():
            raise StartCancelled("warm cache cancelled")
        prep = prepare_seekable_wav(audio_path, self.work_dir)
        return {
            "ok": True,
            "cache_hit": bool(prep.get("cache_hit")),
            "wav_path": prep.get("wav_path"),
            "source_path": audio_path,
        }

    def start(
        self,
        frequency_mhz: float,
        audio_path: str,
        rds_ps: str,
        rds_rt: str,
        rds_pi: str,
    ) -> None:
        # Detach prefetch before stop() so clear_prefetch cannot delete it.
        prep = None  # type: Optional[Dict[str, Any]]
        if (
            self._prefetch
            and self._prefetch.get("source_path") == audio_path
            and self._prefetch.get("wav_path")
        ):
            prep = self._prefetch
            self._prefetch = None

        self.stop()
        if not os.path.isfile(self.binary_path):
            raise FileNotFoundError(
                "FM transmitter program not found. Path: {}".format(self.binary_path)
            )
        if not os.path.isfile(audio_path):
            raise FileNotFoundError(
                "Music file not found: {}".format(os.path.basename(audio_path))
            )

        if prep is None:
            prep = prepare_seekable_wav(audio_path, self.work_dir)
        wav_path = str(prep["wav_path"])
        # Only track ephemeral temps for cleanup; durable cache must survive stop().
        if prep.get("converted") and not prep.get("cached"):
            self._temp_wav = wav_path
        else:
            self._temp_wav = None

        cmd = build_pi_fm_command(
            self.binary_path, frequency_mhz, wav_path, rds_ps, rds_rt, rds_pi
        )
        if "-audio" in cmd and cmd[cmd.index("-audio") + 1] == "-":
            raise RuntimeError("Refusing stdin audio path (Test #2 defect)")

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self._stderr_path = self.log_dir / "pi_fm_rds.stderr.log"
        wav_info = prep.get("wav") or {}
        with open(str(self._stderr_path), "ab") as err_fh:
            err_fh.write(
                (
                    "\n--- start {} freq={} src={!r} wav={!r} "
                    "duration_s={} seekable={} converted={} ---\n"
                    "cmd: {}\n"
                )
                .format(
                    time.strftime("%Y-%m-%dT%H:%M:%S"),
                    frequency_mhz,
                    audio_path,
                    wav_path,
                    wav_info.get("duration_s"),
                    wav_info.get("seekable"),
                    prep.get("converted"),
                    " ".join(cmd),
                )
                .encode("utf-8")
            )

        spawn = self._owned.spawn(cmd, stderr_path=self._stderr_path)
        time.sleep(0.4)
        if not self._owned.is_running():
            err = b""
            try:
                err = self._stderr_path.read_bytes()[-800:]
            except OSError:
                pass
            self.stop()
            raise RuntimeError(
                "FM transmitter stopped immediately: {}".format(
                    err.decode("utf-8", errors="replace")
                )
            )
        if count_transmitters() > 1:
            self.stop()
            raise DuplicateTransmitterError("more than one TX after start")

        self._started_at = time.time()
        self._meta = {
            "backend": "pi_fm_rds",
            "frequency_mhz": frequency_mhz,
            "audio_path": audio_path,
            "wav_path": wav_path,
            "wav_duration_s": wav_info.get("duration_s"),
            "wav_seekable": wav_info.get("seekable"),
            "wav_converted": prep.get("converted"),
            "ffmpeg_ok": prep.get("ffmpeg_ok"),
            "rds_ps": rds_ps,
            "rds_rt": rds_rt,
            "rds_pi": rds_pi,
            "pid": spawn.get("worker_pid"),
            "launcher_pid": spawn.get("launcher_pid"),
            "worker_pid": spawn.get("worker_pid"),
            "stderr_log": str(self._stderr_path),
            "audio_mode": "seekable_wav",
            "program": "audio",
            "started_at": self._started_at,
            "cmd": cmd,
            "ownership": "OwnedTxProcess+sudo_kill_worker",
            "process_tree": "python -> sudo -> pi_fm_rds(worker); stop kills worker PID then launcher",
        }

    def start_silence(
        self,
        frequency_mhz: float,
        rds_ps: str,
        rds_rt: str,
        rds_pi: str,
    ) -> None:
        silence = self.silence_wav
        if silence is None:
            silence = self.work_dir / "silence_loop.wav"
        silence = ensure_silence_wav(Path(silence), duration_s=30)
        self.start(frequency_mhz, str(silence), rds_ps, rds_rt, rds_pi)
        self._meta["program"] = "silence"
        self._meta["audio_path"] = str(silence)
        self._meta["audio_mode"] = "silence_loop"

    def stop(self) -> None:
        self.clear_prefetch()
        audit = self._owned.terminate()
        stopped_at = time.time()
        if self._meta is not None:
            self._meta["stopped_at"] = stopped_at
            self._meta["stop_audit"] = audit
            if self._started_at:
                self._meta["ran_s"] = stopped_at - self._started_at
        if self._temp_wav and os.path.isfile(self._temp_wav):
            try:
                os.unlink(self._temp_wav)
            except OSError:
                pass
        self._temp_wav = None
        self._started_at = None

    def is_running(self) -> bool:
        return self._owned.is_running()

    def status(self) -> Dict[str, Any]:
        running = self.is_running()
        out = {"backend": "pi_fm_rds", "running": running}
        out.update(self._meta)
        if running and self._started_at:
            out["uptime_s"] = time.time() - self._started_at
        # Controller.status() owns OS TX reconcile — avoid a second ps scan.
        return out


def build_backend(
    name: str,
    pi_fm_rds_path: str,
    log_dir: Optional[Path] = None,
    work_dir: Optional[Path] = None,
    silence_wav: Optional[Path] = None,
) -> TxBackend:
    if name == "pi_fm_rds":
        return PiFmRdsBackend(
            pi_fm_rds_path,
            log_dir=log_dir,
            work_dir=work_dir,
            silence_wav=silence_wav,
        )
    if name == "fake":
        return FakeProcessTxBackend(work_dir=(work_dir or Path("/tmp")) / "fake-tx")
    return MockTxBackend()
