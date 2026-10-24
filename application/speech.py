import os
import wave
from functools import lru_cache
from io import BytesIO

from database import DATABASE_PATH


def validate_audio(audio):
    if not audio or len(audio) > 12 * 1024 * 1024:
        raise ValueError("Record an answer no longer than three minutes.")
    try:
        with wave.open(BytesIO(audio), "rb") as recording:
            duration = recording.getnframes() / recording.getframerate()
    except (wave.Error, EOFError, ZeroDivisionError):
        raise ValueError("The recording could not be read. Please record your answer again.") from None
    if not 0.3 <= duration <= 180:
        raise ValueError("Record between one second and three minutes of speech.")
    return duration


@lru_cache(maxsize=1)
def load_transcriber():
    import ctranslate2
    from faster_whisper import WhisperModel

    try:
        device = "cuda" if ctranslate2.get_cuda_device_count() > 0 else "cpu"
    except RuntimeError:
        device = "cpu"
    download_root = str(DATABASE_PATH.parent / "models" / "whisper")
    try:
        return WhisperModel(
            "small.en", device=device, compute_type="float16" if device == "cuda" else "int8",
            cpu_threads=min(4, os.cpu_count() or 2), num_workers=1,
            download_root=download_root,
        )
    except (RuntimeError, ValueError):
        if device == "cuda":
            return WhisperModel("small.en", device="cpu", compute_type="int8", cpu_threads=min(4, os.cpu_count() or 2), num_workers=1, download_root=download_root)
        raise


def transcribe_answer(audio):
    validate_audio(audio)
    model = load_transcriber()
    segments, _ = model.transcribe(
        BytesIO(audio), language="en", beam_size=5, vad_filter=True,
        condition_on_previous_text=False, no_speech_threshold=0.6,
    )
    text = " ".join(segment.text.strip() for segment in segments).strip()
    if not text:
        raise ValueError("No speech was detected. Please record your answer again.")
    return text
