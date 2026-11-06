"""Short-lived readiness receipts. Browser reports are not identity attestation."""
import array
import hashlib
import math
import secrets
import sqlite3
import sys
import wave
from contextlib import closing
from datetime import datetime, timedelta
from io import BytesIO

from PIL import Image, ImageStat
from database import DATABASE_PATH
from scheduling import _timestamp, utc_now

POLICY_VERSION = "setup-v1"


def issue_receipt(token, checks, frame, audio, database_path=DATABASE_PATH):
    from interview_store import _invitation

    required = ("speaker_confirmed", "playback_confirmed", "rules_accepted")
    if checks.get("policy_version") != POLICY_VERSION or any(checks.get(key) is not True for key in required):
        raise ValueError("Complete the audio checks and acknowledge the interview rules.")
    if type(checks.get("face_count")) is not int or checks["face_count"] != 1 or checks.get("screen_surface") != "monitor":
        raise ValueError("Keep one face visible and share your entire screen before continuing.")
    if not frame or len(frame) > 250_000 or not audio or len(audio) > 400_000:
        raise ValueError("The setup samples are missing or too large. Repeat the checks.")
    try:
        with Image.open(BytesIO(frame)) as image:
            if image.format != "JPEG" or not 100 <= image.width <= 640 or not 100 <= image.height <= 480:
                raise ValueError("Invalid camera sample.")
            light = ImageStat.Stat(image.convert("L")).mean[0]
            if light < 40:
                raise ValueError("Your camera image is too dark. Improve your lighting and retry.")
        with wave.open(BytesIO(audio), "rb") as recording:
            if recording.getnchannels() != 1 or recording.getsampwidth() != 2 or recording.getframerate() != 16000:
                raise ValueError("Use a mono 16 kHz microphone test recording.")
            duration = recording.getnframes() / 16000
            if not 1 <= duration <= 8:
                raise ValueError("Record between one and eight seconds for the microphone check.")
            samples = array.array('h', recording.readframes(recording.getnframes()))
            if len(samples) != recording.getnframes():
                raise ValueError("The microphone sample is incomplete. Record it again.")
            if sys.byteorder != 'little':
                samples.byteswap()
            rms = math.sqrt(sum(value * value for value in samples) / max(1, len(samples))) / 32768
            if rms < .003:
                raise ValueError("The test recording is silent. Select another microphone or speak more clearly.")
    except (OSError, EOFError, wave.Error, Image.DecompressionBombError):
        raise ValueError("The setup samples could not be read. Repeat the checks.") from None
    now = utc_now()
    receipt = secrets.token_urlsafe(32)
    with closing(sqlite3.connect(database_path)) as connection, connection:
        connection.row_factory = sqlite3.Row
        invitation = _invitation(connection, token)
        connection.execute(
            "INSERT INTO interview_preflight(receipt_sha256,invitation_sha256,interview_id,policy_version,expires_at) VALUES(?,?,?,?,?)",
            (hashlib.sha256(receipt.encode()).hexdigest(), invitation['token_sha256'], invitation['interview_id'], POLICY_VERSION,
             _timestamp(now + timedelta(minutes=10))),
        )
    return {"receipt": receipt, "expires_in_seconds": 600}


def consume_receipt(connection, receipt, invitation_hash):
    if not receipt or not 20 <= len(receipt) <= 200:
        raise ValueError("Complete the interview system check before starting.")
    row = connection.execute(
        "SELECT expires_at,consumed_at,policy_version FROM interview_preflight WHERE receipt_sha256=? AND invitation_sha256=?",
        (hashlib.sha256(receipt.encode()).hexdigest(), invitation_hash),
    ).fetchone()
    if not row or row[1] or row[2] != POLICY_VERSION or datetime.fromisoformat(row[0]) <= utc_now():
        raise ValueError("Your system check has expired or is no longer valid. Run it again.")
    connection.execute("UPDATE interview_preflight SET consumed_at=? WHERE receipt_sha256=?",
                       (_timestamp(utc_now()), hashlib.sha256(receipt.encode()).hexdigest()))
