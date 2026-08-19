import re
from typing import Optional

from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api._errors import (
    NoTranscriptFound,
    TranscriptsDisabled,
    VideoUnavailable,
    IpBlocked,
    RequestBlocked,
)


class TranscriptFetchError(Exception):
    pass


def _extract_video_id(url: str) -> str:
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11}).*",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    raise TranscriptFetchError(f"Could not extract a video ID from URL: {url}")


def fetch_transcript(url: str) -> tuple[str, str]:
    """Get a transcript for a video URL, however we can.

    Returns (text, source) where source is "captions" or "audio".

    Captions are tried first because they are instant and free. Most cooking
    content has none, especially Shorts and Reels, so we fall back to pulling
    the audio track and running Whisper over it. Without that fallback the URL
    input only works for the minority of videos that happen to be captioned.
    """
    from .audio import fetch_description

    # The description usually carries the ingredient list with exact amounts,
    # so it is worth having alongside whatever was spoken.
    description = fetch_description(url)

    def combine(spoken: str) -> str:
        if not description:
            return spoken
        return f"{spoken}\n\n--- VIDEO DESCRIPTION ---\n{description}"

    caption_error: Optional[str] = None
    try:
        return combine(fetch_youtube_transcript(url)), "captions"
    except TranscriptFetchError as exc:
        caption_error = str(exc)

    from .audio import AudioError, transcribe_from_url

    try:
        return combine(transcribe_from_url(url)), "audio"
    except AudioError as exc:
        raise TranscriptFetchError(
            f"No captions on that video ({caption_error}) and the audio could "
            f"not be transcribed either. {exc}"
        )


def fetch_youtube_transcript(url: str) -> str:
    video_id = _extract_video_id(url)
    try:
        fetched = YouTubeTranscriptApi().fetch(video_id)
    except TranscriptsDisabled:
        raise TranscriptFetchError("Transcripts are disabled for this video.")
    except NoTranscriptFound:
        raise TranscriptFetchError("No transcript is available for this video.")
    except VideoUnavailable:
        raise TranscriptFetchError("This video is unavailable (private or removed).")
    except (IpBlocked, RequestBlocked):
        raise TranscriptFetchError(
            "YouTube is blocking transcript requests from this network right now."
        )
    except Exception as exc:
        raise TranscriptFetchError(f"Failed to fetch transcript: {exc}")

    return " ".join(snippet.text for snippet in fetched)


def clean_transcript(text: str) -> str:
    text = re.sub(r"\[\d{1,2}:\d{2}(:\d{2})?\]", " ", text)
    text = re.sub(r"^\s*[A-Za-z ]{2,20}:\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"\s+", " ", text)
    return text.strip()
