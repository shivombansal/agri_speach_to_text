import os
import json
import time
import tempfile
import streamlit as st
from audio_recorder_streamlit import audio_recorder
from google.cloud import speech_v2, translate_v2
from google.api_core.client_options import ClientOptions

# ── Credentials — works both locally and on Streamlit Cloud ───────────────────

def _write_credentials_from_secrets() -> str:
    """Write st.secrets GCP creds to a temp file and return its path."""
    creds = dict(st.secrets["gcp_service_account"])
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(creds, tmp)
    tmp.flush()
    return tmp.name

# Only inject if not already set (preserves local dev behaviour)
if "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ:
    if "gcp_service_account" in st.secrets:
        os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _write_credentials_from_secrets()

# ── Config ────────────────────────────────────────────────────────────────────

def _get_project_id() -> str:
    # Prefer secrets → fall back to env var (local dev)
    try:
        return st.secrets["gcp_service_account"]["project_id"]
    except (KeyError, AttributeError):
        return os.environ.get("GOOGLE_CLOUD_PROJECT", "")

PROJECT_ID = _get_project_id()
REGION     = "us-central1"
LANGUAGE   = "te-IN"   # Telugu — change to "ta-IN", "bn-IN", "kn-IN" etc.
MODEL      = "chirp_2"

CONFIDENCE_THRESHOLDS = {
    "high":   0.85,
    "medium": 0.70,
    "low":    0.40,
}

# ── Google API helpers ────────────────────────────────────────────────────────

def transcribe_audio(audio_bytes: bytes) -> dict:
    """
    Send audio bytes to Google STT V2 (Chirp 2, us-central1).
    Returns {"transcript": str, "confidence": float}
    """
    client = speech_v2.SpeechClient(
        client_options=ClientOptions(
            api_endpoint=f"{REGION}-speech.googleapis.com"
        )
    )

    config = speech_v2.RecognitionConfig(
        auto_decoding_config=speech_v2.AutoDetectDecodingConfig(),
        language_codes=[LANGUAGE],
        model=MODEL,
        features=speech_v2.RecognitionFeatures(
            enable_automatic_punctuation=True,
        ),
    )

    request = speech_v2.RecognizeRequest(
        recognizer=f"projects/{PROJECT_ID}/locations/{REGION}/recognizers/_",
        config=config,
        content=audio_bytes,
    )

    response = client.recognize(request=request)

    if not response.results:
        return {"transcript": "", "confidence": 0.0}

    best = response.results[0].alternatives[0]
    return {
        "transcript": best.transcript,
        "confidence": float(best.confidence),
    }


def translate_to_english(text: str, source_lang: str = "te") -> str:
    """Translate text using Google Cloud Translation Basic (v2) API."""
    client = translate_v2.Client()
    result = client.translate(text, source_language=source_lang, target_language="en")
    return result["translatedText"]


# ── UI helpers ────────────────────────────────────────────────────────────────

def confidence_ui(score: float) -> tuple[str, str, str]:
    """Return (label, emoji, streamlit color) for a confidence score."""
    if score >= CONFIDENCE_THRESHOLDS["high"]:
        return "High confidence", "🟢", "success"
    elif score >= CONFIDENCE_THRESHOLDS["medium"]:
        return "Medium — flagged for review", "🟡", "warning"
    elif score >= CONFIDENCE_THRESHOLDS["low"]:
        return "Low — please confirm before saving", "🟠", "warning"
    else:
        return "Very low — re-record recommended", "🔴", "error"


def check_env() -> bool:
    """Validate credentials are available from either secrets or env var."""
    ok = True
    has_secrets = "gcp_service_account" in st.secrets
    has_env     = bool(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))

    if not has_secrets and not has_env:
        st.error(
            "**GCP credentials not found.**\n\n"
            "On Streamlit Cloud: add `[gcp_service_account]` to your app Secrets.\n\n"
            "Locally: `export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json`"
        )
        ok = False
    if not PROJECT_ID:
        st.error(
            "**project_id not found.**\n\n"
            "It should be inside `[gcp_service_account]` in Secrets, "
            "or set `GOOGLE_CLOUD_PROJECT` locally."
        )
        ok = False
    return ok


# ── Page layout ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="Voice Field Notes — POC",
    page_icon="🌾",
    layout="centered",
)

st.markdown(
    """
    <h1 style='text-align:center; margin-bottom:0'>🌾 Voice Field Notes</h1>
    <p style='text-align:center; color:grey; margin-top:4px'>
        POC · Telugu (తెలుగు) Speach to English Transcription
    </p>
    """,
    unsafe_allow_html=True,
)
st.divider()

if not check_env():
    st.stop()

# ── Recording section ─────────────────────────────────────────────────────────

st.subheader("Step 1 — Record your note")
st.caption(
    "Click the mic button to **start** recording. Click again to **stop**. "
    "Speak in Telugu. Max 60 seconds."
)

audio_bytes = audio_recorder(
    text="",
    recording_color="#e53935",
    neutral_color="#1e88e5",
    icon_name="microphone",
    icon_size="4x",
    pause_threshold=60.0,
    sample_rate=16_000,
)

if not audio_bytes:
    st.info("👆 Press the microphone button above and speak your field note in Telugu.")
    st.stop()

st.audio(audio_bytes, format="audio/wav")
st.caption(f"Audio captured — {len(audio_bytes) / 1024:.1f} KB")

MIN_BYTES = 8_000
if len(audio_bytes) < MIN_BYTES:
    st.warning("Recording is too short. Please hold the button for at least 1 second and try again.")
    st.stop()

# ── Processing section ────────────────────────────────────────────────────────

st.divider()
st.subheader("Step 2 — Transcription & Translation")

overall_start = time.perf_counter()

with st.spinner("Transcribing with Google Chirp 2..."):
    stt_start = time.perf_counter()
    try:
        stt_result = transcribe_audio(audio_bytes)
    except Exception as exc:
        st.error(f"STT failed: {exc}")
        with st.expander("Full traceback"):
            st.exception(exc)
        st.stop()
    stt_elapsed = time.perf_counter() - stt_start

if not stt_result["transcript"]:
    st.error(
        "Google could not detect any speech in this recording.\n\n"
        "Try again in a quieter environment, or speak closer to the microphone."
    )
    st.stop()

with st.spinner("Translating to English..."):
    nmt_start = time.perf_counter()
    try:
        english_text = translate_to_english(stt_result["transcript"])
    except Exception as exc:
        st.error(f"Translation failed: {exc}")
        with st.expander("Full traceback"):
            st.exception(exc)
        st.stop()
    nmt_elapsed = time.perf_counter() - nmt_start

total_elapsed = time.perf_counter() - overall_start

# ── Results section ───────────────────────────────────────────────────────────

st.divider()
st.subheader("Step 3 — Review your note")

confidence = stt_result["confidence"]
label, emoji, alert_type = confidence_ui(confidence)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Confidence",  f"{confidence:.0%}")
m2.metric("STT",         f"{stt_elapsed:.2f}s")
m3.metric("Translation", f"{nmt_elapsed:.2f}s")
m4.metric("Total",       f"{total_elapsed:.2f}s")

if alert_type == "success":
    st.success(f"{emoji} {label}")
elif alert_type == "warning":
    st.warning(f"{emoji} {label}")
else:
    st.error(f"{emoji} {label}")

st.markdown("#### 📝 Telugu Transcript")
st.text_area(
    label="telugu",
    value=stt_result["transcript"],
    height=110,
    label_visibility="collapsed",
    key="telugu_out",
)

st.markdown("#### 🌐 English Translation")
st.text_area(
    label="english",
    value=english_text,
    height=110,
    label_visibility="collapsed",
    key="english_out",
)

col_save, col_retry = st.columns([1, 1])
with col_save:
    if confidence >= CONFIDENCE_THRESHOLDS["low"]:
        if st.button("✅ Confirm & Save note", use_container_width=True, type="primary"):
            st.success("Note saved! (In production this writes to the database.)")
    else:
        st.button("✅ Confirm & Save note", use_container_width=True, disabled=True)
        st.caption("Confidence too low to save. Please re-record.")

with col_retry:
    if st.button("🔄 Re-record", use_container_width=True):
        st.rerun()

# ── Debug expander ────────────────────────────────────────────────────────────

with st.expander("🔧 Debug info"):
    st.json(
        {
            "language_code":   LANGUAGE,
            "model":           MODEL,
            "region":          REGION,
            "project_id":      PROJECT_ID,
            "audio_bytes":     len(audio_bytes),
            "audio_kb":        round(len(audio_bytes) / 1024, 1),
            "confidence":      round(confidence, 4),
            "stt_ms":          round(stt_elapsed * 1000),
            "nmt_ms":          round(nmt_elapsed * 1000),
            "total_ms":        round(total_elapsed * 1000),
            "raw_transcript":  stt_result["transcript"],
            "raw_translation": english_text,
        }
    )

st.divider()
