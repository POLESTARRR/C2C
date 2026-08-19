"""The captions-first, audio-fallback chain.

Neither branch touches the network here. What matters is that the fallback
fires only when captions are missing, and that a failure in both halves
produces one clear message rather than a stack trace.
"""

import pytest

from backend import audio, transcript as T
from backend.audio import AudioError


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """fetch_transcript now also reads the video description, which is a real
    network call. Tests must not make one."""
    monkeypatch.setattr(audio, "fetch_description", lambda url: "")


def test_captions_win_when_available(monkeypatch):
    monkeypatch.setattr(T, "fetch_youtube_transcript", lambda url: "two cups of atta")
    text, source = T.fetch_transcript("https://youtu.be/abc12345678")
    assert source == "captions"
    assert text == "two cups of atta"


def test_audio_is_not_touched_when_captions_exist(monkeypatch):
    """The expensive path must stay unused on the happy path."""
    called = []
    monkeypatch.setattr(T, "fetch_youtube_transcript", lambda url: "atta")
    monkeypatch.setattr(audio, "transcribe_from_url", lambda url: called.append(url) or "x")
    T.fetch_transcript("https://youtu.be/abc12345678")
    assert called == []


def test_falls_back_to_audio_when_no_captions(monkeypatch):
    def no_captions(url):
        raise T.TranscriptFetchError("No transcript is available for this video.")

    monkeypatch.setattr(T, "fetch_youtube_transcript", no_captions)
    monkeypatch.setattr(audio, "transcribe_from_url", lambda url: "one cup of ghee")

    text, source = T.fetch_transcript("https://youtu.be/abc12345678")
    assert source == "audio"
    assert text == "one cup of ghee"


def test_both_paths_failing_gives_one_clear_message(monkeypatch):
    def no_captions(url):
        raise T.TranscriptFetchError("No transcript is available for this video.")

    def no_audio(url):
        raise AudioError("This platform wants a logged in session.")

    monkeypatch.setattr(T, "fetch_youtube_transcript", no_captions)
    monkeypatch.setattr(audio, "transcribe_from_url", no_audio)

    with pytest.raises(T.TranscriptFetchError) as excinfo:
        T.fetch_transcript("https://instagram.com/reel/xyz")

    message = str(excinfo.value)
    assert "No captions" in message
    assert "logged in session" in message


def test_oversized_upload_is_refused(tmp_path):
    """Groq rejects big uploads, so catch it before spending the round trip."""
    big = tmp_path / "big.m4a"
    big.write_bytes(b"0" * (audio.MAX_UPLOAD_BYTES + 1))
    with pytest.raises(AudioError, match="over the"):
        audio.transcribe_file(str(big))


def test_login_required_error_is_translated():
    from backend.audio import _humanise

    msg = _humanise(Exception("ERROR: Instagram said: login required, use --cookies"))
    assert "logged in session" in msg
    assert "Paste the transcript instead" in msg


# --- long audio -----------------------------------------------------------

def test_chunk_size_leaves_headroom_under_the_upload_cap():
    """20 minutes of 32 kbps mono is about 5 MB, well under the 24 MB cap.
    If either constant is ever changed, this is what catches an overrun."""
    from backend import audio

    bytes_per_second = 32_000 / 8          # 32 kbps
    projected = audio.CHUNK_SECONDS * bytes_per_second
    assert projected < audio.MAX_UPLOAD_BYTES / 2, (
        f"a {audio.CHUNK_SECONDS}s chunk projects to {projected/1024/1024:.1f} MB"
    )


def test_long_videos_are_allowed_now_that_we_split_them():
    """The old 30 minute cap existed because one request had to hold the whole
    recording. Chunking removed that reason."""
    from backend import audio

    assert audio.MAX_DURATION_SECONDS >= 2 * 60 * 60


def test_split_returns_the_original_when_ffmpeg_is_missing(monkeypatch, tmp_path):
    """No ffmpeg means no splitting, but it must not crash the request."""
    from backend import audio

    monkeypatch.setattr(audio, "_find_ffmpeg", lambda: None)
    src = tmp_path / "a.m4a"
    src.write_bytes(b"0" * 16)
    assert audio.split_audio(str(src), str(tmp_path)) == [str(src)]


def test_oversized_chunk_is_reported_clearly(monkeypatch, tmp_path):
    """If a piece still will not fit, say so rather than failing obscurely."""
    from backend import audio

    big = tmp_path / "chunk.m4a"
    big.write_bytes(b"0" * (audio.MAX_UPLOAD_BYTES + 1))
    with pytest.raises(AudioError, match="over the"):
        audio.transcribe_file(str(big))
