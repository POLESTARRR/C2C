import re

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
