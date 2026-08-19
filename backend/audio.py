"""Transcribe a video's audio when it has no captions.

Most cooking content has no uploaded captions. Shorts and Reels almost never
do, and smaller channels rarely bother. Asking the user to paste a transcript
is not a real answer, because anyone who can obtain a transcript does not need
this tool in the first place.

So when captions are missing we pull the audio track and run it through
Whisper on Groq. The audio stream alone is small, a few MB for a typical
recipe video, and no re-encoding happens, which means ffmpeg is not required.
"""

import glob
import os
import shutil
import subprocess
import tempfile
import time
from typing import Optional

WHISPER_MODEL = os.environ.get("GROQ_WHISPER_MODEL") or "whisper-large-v3"

# Groq's free tier rejects uploads above 25 MB. Audio-only for a 20 minute
# video lands well under this, so hitting it means something is wrong.
MAX_UPLOAD_BYTES = 24 * 1024 * 1024

# Long videos are handled by splitting them, so this cap exists only to stop a
# runaway download, not because length itself is a problem.
MAX_DURATION_SECONDS = 4 * 60 * 60

# How much audio goes in one request. At 32 kbps mono a minute is about 240 KB,
# so 20 minutes lands near 5 MB, comfortably inside the limit with headroom for
# a container and a slow connection.
CHUNK_SECONDS = 20 * 60

# YouTube returns 403 to yt-dlp's default client. The android client still
# serves audio streams without a PO token.
YT_PLAYER_CLIENTS = ["android", "ios", "tv"]


def _find_ffmpeg() -> Optional[str]:
    """Locate an ffmpeg binary, including the one Playwright ships.

    A plain install is not required. If Playwright has been installed for the
    test suite, its bundled build is perfectly good for stripping an audio
    track out of a video container.
    """
    # imageio-ffmpeg ships a full static build as a normal pip dependency, so
    # nothing has to be installed system wide. Playwright also bundles an
    # ffmpeg, but that one is built only for screen recording and cannot
    # demux an mp4.
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001
        pass
    return shutil.which("ffmpeg")


def shrink_audio(path: str, workdir: str) -> str:
    """Re-encode to small mono speech audio.

    YouTube often serves a short video as one combined file, so a six minute
    recipe arrives as 27 MB of 360p video when all we want is the speech.
    Whisper runs on 16 kHz mono, and dropping to that took a real example from
    27.3 MB to 1.5 MB, which also uploads far faster.

    Returns the original path unchanged if ffmpeg is unavailable.
    """
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return path

    out = os.path.join(workdir, "audio_16k.m4a")
    command = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", path,
        "-vn",                 # drop any video stream
        "-ac", "1",            # mono
        "-ar", "16000",        # 16 kHz is what Whisper wants
        "-c:a", "aac", "-b:a", "32k",
        out,
    ]
    try:
        subprocess.run(command, check=True, timeout=300,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception:  # noqa: BLE001 - fall back to the original file
        return path
    return out if os.path.exists(out) and os.path.getsize(out) > 0 else path


class AudioError(Exception):
    pass


def _humanise(exc: Exception) -> str:
    text = str(exc).lower()
    if "login" in text or "cookies" in text or "rate-limit" in text:
        return (
            "This platform wants a logged in session before it will serve the "
            "video. Instagram and TikTok usually do. Paste the transcript "
            "instead, or use a YouTube link."
        )
    if "private" in text or "unavailable" in text:
        return "That video is private or unavailable."
    if "unsupported url" in text:
        return "That link is not one we can fetch audio from."
    return f"Could not fetch the audio: {exc}"


def fetch_description(url: str) -> str:
    """Pull the video's title and description.

    Creators routinely put the full ingredient list with exact amounts in the
    description, even when they never say the numbers out loud. Ignoring it
    threw away the most reliable source of quantities we have.
    """
    try:
        import yt_dlp
    except ImportError:
        return ""

    for client in YT_PLAYER_CLIENTS:
        try:
            opts = {
                "quiet": True, "no_warnings": True, "noplaylist": True,
                "skip_download": True,
                "extractor_args": {"youtube": {"player_client": [client]}},
            }
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
            parts = [info.get("title") or "", info.get("description") or ""]
            text = "\n".join(p for p in parts if p).strip()
            if text:
                return text[:8000]
        except Exception:  # noqa: BLE001 - a bonus source, never fatal
            continue
    return ""


def download_audio(url: str, workdir: str) -> str:
    """Fetch the audio stream only. Returns the path to the downloaded file."""
    try:
        import yt_dlp
    except ImportError:
        raise AudioError(
            "yt-dlp is not installed. Run: pip install -r requirements.txt"
        )

    last_error = None
    for client in YT_PLAYER_CLIENTS:
        opts = {
            "format": "bestaudio/best",
            "outtmpl": os.path.join(workdir, "%(id)s.%(ext)s"),
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "noprogress": True,
            "extractor_args": {"youtube": {"player_client": [client]}},
        }
        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=False)
                duration = info.get("duration") or 0
                if duration > MAX_DURATION_SECONDS:
                    raise AudioError(
                        f"That video is {duration // 3600} hours long, past the "
                        f"{MAX_DURATION_SECONDS // 3600} hour limit. Paste the "
                        f"transcript instead."
                    )
                ydl.download([url])

            files = glob.glob(os.path.join(workdir, "*"))
            if files:
                return files[0]
        except AudioError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            continue

    raise AudioError(_humanise(last_error) if last_error else "No audio stream found.")


def split_audio(path: str, workdir: str) -> list[str]:
    """Cut a long recording into chunks that each fit inside the upload limit.

    Groq caps a single request at 25 MB, which is a limit on one request rather
    than on how much audio we can handle. A three hour video is transcribed by
    sending it in pieces and joining the text back together.

    Returns [path] unchanged when ffmpeg is missing or the split fails.
    """
    ffmpeg = _find_ffmpeg()
    if not ffmpeg:
        return [path]

    pattern = os.path.join(workdir, "chunk_%03d.m4a")
    command = [
        ffmpeg, "-y", "-loglevel", "error",
        "-i", path,
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "aac", "-b:a", "32k",
        "-f", "segment", "-segment_time", str(CHUNK_SECONDS),
        "-reset_timestamps", "1",
        pattern,
    ]
    try:
        subprocess.run(command, check=True, timeout=1800,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    except Exception:  # noqa: BLE001
        return [path]

    chunks = sorted(glob.glob(os.path.join(workdir, "chunk_*.m4a")))
    return chunks or [path]


def transcribe_file(path: str) -> str:
    """Send one audio file to Whisper on Groq and return the text."""
    size = os.path.getsize(path)
    if size > MAX_UPLOAD_BYTES:
        raise AudioError(
            f"This piece of audio is {size / 1024 / 1024:.0f} MB, over the "
            f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB limit for a single request."
        )

    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise AudioError("GROQ_API_KEY is not set. Add it to your .env file.")

    from groq import Groq

    try:
        client = Groq(api_key=api_key, timeout=180.0)
        with open(path, "rb") as handle:
            result = client.audio.transcriptions.create(
                file=(os.path.basename(path), handle.read()),
                model=WHISPER_MODEL,
                response_format="text",
            )
    except Exception as exc:  # noqa: BLE001
        name = type(exc).__name__
        if "RateLimit" in name:
            raise AudioError("Groq rate limit reached. Wait a few seconds and try again.")
        raise AudioError(f"Transcription failed ({name}): {exc}")

    text = result if isinstance(result, str) else getattr(result, "text", "")
    if not text.strip():
        raise AudioError("The audio produced an empty transcript.")
    return text.strip()


def transcribe_from_url(url: str) -> str:
    """Download a video's audio and transcribe it. Cleans up after itself."""
    workdir = tempfile.mkdtemp(prefix="clip2cart_")
    try:
        started = time.perf_counter()
        path = download_audio(url, workdir)

        # Strip the video and drop to speech audio. YouTube frequently serves a
        # short clip as one combined file, so this is usually a tenfold saving
        # and it makes the upload much quicker.
        path = shrink_audio(path, workdir)

        if os.path.getsize(path) <= MAX_UPLOAD_BYTES:
            pieces = [path]
        else:
            # Still too big, so it is a genuinely long recording. Send it in
            # parts rather than refusing it.
            pieces = split_audio(path, workdir)

        texts = []
        for piece in pieces:
            if os.path.getsize(piece) > MAX_UPLOAD_BYTES:
                raise AudioError(
                    "This audio cannot be split small enough to upload. "
                    "Try a shorter video."
                )
            texts.append(transcribe_file(piece))

        _LAST_TIMING["seconds"] = round(time.perf_counter() - started, 1)
        _LAST_TIMING["chunks"] = len(pieces)
        return " ".join(t for t in texts if t).strip()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


_LAST_TIMING = {"seconds": 0.0, "chunks": 1}
