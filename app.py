import os
import json
import time
import tempfile
from datetime import datetime, timezone

import streamlit as st
import gspread
from audio_recorder_streamlit import audio_recorder
from google.cloud import speech_v2, translate_v2
from google.api_core.client_options import ClientOptions
from google.oauth2.service_account import Credentials

# ── Credentials — works both locally and on Streamlit Cloud ───────────────────

def _load_gcp_creds() -> dict:
    """Return GCP creds dict from Streamlit secrets or local JSON file."""
    if "gcp_service_account" in st.secrets:
        return dict(st.secrets["gcp_service_account"])
    path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
    if path and os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return {}

def _write_credentials_tempfile(creds: dict) -> str:
    """Write creds dict to a temp JSON file and return its path."""
    tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(creds, tmp)
    tmp.flush()
    return tmp.name

GCP_CREDS_DICT = _load_gcp_creds()

# Inject env var for Google Cloud SDK clients (STT / Translate)
if "GOOGLE_APPLICATION_CREDENTIALS" not in os.environ and GCP_CREDS_DICT:
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = _write_credentials_tempfile(GCP_CREDS_DICT)

# ── Config ────────────────────────────────────────────────────────────────────

def _get_project_id() -> str:
    try:
        return st.secrets["gcp_service_account"]["project_id"]
    except (KeyError, AttributeError):
        return GCP_CREDS_DICT.get("project_id", os.environ.get("GOOGLE_CLOUD_PROJECT", ""))

PROJECT_ID  = _get_project_id()
REGION      = "us-central1"
LANGUAGE    = "te-IN"       # Telugu
MODEL       = "chirp_2"

SHEET_ID = ""
try:
    SHEET_ID = st.secrets["SHEET_ID"]
except (KeyError, AttributeError):
    SHEET_ID = os.environ.get("SHEET_ID", "")

SHEET_HEADERS = [
    "timestamp", "tester_name", "language_code", "model", "region",
    "audio_kb", "confidence", "stt_ms", "nmt_ms", "total_ms",
    "telugu_transcript", "english_translation", "feedback", "remarks",
]

CONFIDENCE_THRESHOLDS = {
    "high":   0.85,
    "medium": 0.70,
    "low":    0.40,
}

FEEDBACK_OPTIONS = ["✅ Correct", "⚠️ Not Fully Correct", "❌ Not Correct"]

# ── Google Sheets helper ──────────────────────────────────────────────────────

@st.cache_resource(show_spinner=False)
def _get_sheet():
    """Return the first worksheet of SHEET_ID, authenticated via service account."""
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    ]
    creds = Credentials.from_service_account_info(GCP_CREDS_DICT, scopes=scopes)
    gc    = gspread.authorize(creds)
    sh    = gc.open_by_key(SHEET_ID)
    ws    = sh.sheet1

    # Write header row if A1 is empty or doesn't match expected header
    if ws.cell(1, 1).value != "timestamp":
        ws.insert_row(SHEET_HEADERS, index=1)

    return ws


def append_row(row: dict) -> None:
    """Append one result row to the Google Sheet."""
    ws = _get_sheet()
    ws.append_row([row.get(h, "") for h in SHEET_HEADERS])


def fetch_all_rows() -> list[dict]:
    """Return all data rows as a list of dicts."""
    ws   = _get_sheet()
    data = ws.get_all_records()
    return data


# ── Google API helpers ────────────────────────────────────────────────────────

def transcribe_audio(audio_bytes: bytes) -> dict:
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
    # Pass credentials explicitly for reliability
    creds = Credentials.from_service_account_info(
        GCP_CREDS_DICT,
        scopes=["https://www.googleapis.com/auth/cloud-translation"],
    )
    client = translate_v2.Client(credentials=creds)
    result = client.translate(text, source_language=source_lang, target_language="en")
    return result["translatedText"]


# ── UI helpers ────────────────────────────────────────────────────────────────

def confidence_ui(score: float) -> tuple[str, str, str]:
    if score >= CONFIDENCE_THRESHOLDS["high"]:
        return "High confidence", "🟢", "success"
    elif score >= CONFIDENCE_THRESHOLDS["medium"]:
        return "Medium — flagged for review", "🟡", "warning"
    elif score >= CONFIDENCE_THRESHOLDS["low"]:
        return "Low — please confirm before saving", "🟠", "warning"
    else:
        return "Very low — re-record recommended", "🔴", "error"


def check_env() -> bool:
    ok = True
    if not GCP_CREDS_DICT:
        st.error(
            "**GCP credentials not found.**\n\n"
            "On Streamlit Cloud: add `[gcp_service_account]` to your app Secrets.\n\n"
            "Locally: `export GOOGLE_APPLICATION_CREDENTIALS=/path/to/key.json`"
        )
        ok = False
    if not PROJECT_ID:
        st.error("**project_id not found** in credentials.")
        ok = False
    if not SHEET_ID:
        st.error(
            "**SHEET_ID not set.**\n\n"
            "Add `SHEET_ID = \"your-sheet-id\"` to Streamlit Secrets "
            "or set the `SHEET_ID` environment variable locally."
        )
        ok = False
    return ok


# ── Tab content functions ─────────────────────────────────────────────────────
# NOTE: All st.stop() calls have been replaced with early `return` inside these
# functions. This is critical — calling st.stop() inside a tab block stops ALL
# rendering in the app, including the other tabs.

def render_record_tab():
    # Tester name — required before anything else
    tester_name = st.text_input(
        "Your name",
        placeholder="Enter your name before recording",
        key="tester_name",
    )
    if not tester_name.strip():
        st.info("👆 Please enter your name above to get started.")
        return  # ← was st.stop()

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
        return  # ← was st.stop()

    st.audio(audio_bytes, format="audio/wav")
    st.caption(f"Audio captured — {len(audio_bytes) / 1024:.1f} KB")

    MIN_BYTES = 8_000
    if len(audio_bytes) < MIN_BYTES:
        st.warning("Recording is too short. Please hold the button for at least 1 second and try again.")
        return  # ← was st.stop()

    # ── Processing ────────────────────────────────────────────────────────────

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
            return  # ← was st.stop()
        stt_elapsed = time.perf_counter() - stt_start

    if not stt_result["transcript"]:
        st.error(
            "Google could not detect any speech in this recording.\n\n"
            "Try again in a quieter environment, or speak closer to the microphone."
        )
        return  # ← was st.stop()

    with st.spinner("Translating to English..."):
        nmt_start = time.perf_counter()
        try:
            english_text = translate_to_english(stt_result["transcript"])
        except Exception as exc:
            st.error(f"Translation failed: {exc}")
            with st.expander("Full traceback"):
                st.exception(exc)
            return  # ← was st.stop()
        nmt_elapsed = time.perf_counter() - nmt_start

    total_elapsed = time.perf_counter() - overall_start

    # ── Results ───────────────────────────────────────────────────────────────

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

    # ── Feedback ──────────────────────────────────────────────────────────────

    st.divider()
    st.subheader("Step 4 — Rate this result")

    feedback = st.radio(
        "How accurate was the transcription + translation?",
        options=FEEDBACK_OPTIONS,
        index=None,         # no default — forces a conscious choice
        horizontal=True,
        key="feedback_radio",
    )

    remarks = st.text_area(
        "Personal remarks (required)",
        placeholder="Describe any errors, what was wrong, or confirm it was perfect...",
        height=100,
        key="remarks_input",
    )

    # ── Save button ───────────────────────────────────────────────────────────

    st.divider()

    can_save = bool(feedback) and bool(remarks.strip())

    col_save, col_retry = st.columns([1, 1])

    with col_save:
        if not can_save:
            st.button(
                "💾 Save to database",
                use_container_width=True,
                disabled=True,
                type="primary",
            )
            if not feedback:
                st.caption("⬆️ Select a feedback option to enable save.")
            elif not remarks.strip():
                st.caption("⬆️ Add your personal remarks to enable save.")
        else:
            if st.button("💾 Save to database", use_container_width=True, type="primary"):
                row = {
                    "timestamp":           datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
                    "tester_name":         tester_name.strip(),
                    "language_code":       LANGUAGE,
                    "model":               MODEL,
                    "region":              REGION,
                    "audio_kb":            round(len(audio_bytes) / 1024, 1),
                    "confidence":          round(confidence, 4),
                    "stt_ms":              round(stt_elapsed * 1000),
                    "nmt_ms":              round(nmt_elapsed * 1000),
                    "total_ms":            round(total_elapsed * 1000),
                    "telugu_transcript":   stt_result["transcript"],
                    "english_translation": english_text,
                    "feedback":            feedback,
                    "remarks":             remarks.strip(),
                }
                try:
                    with st.spinner("Saving to Google Sheets..."):
                        append_row(row)
                    st.success("✅ Saved! Switch to the **📊 Submissions** tab to see all results.")
                except Exception as exc:
                    st.error(f"Save failed: {exc}")
                    with st.expander("Full traceback"):
                        st.exception(exc)

    with col_retry:
        if st.button("🔄 Re-record", use_container_width=True):
            st.rerun()

    # ── Debug ─────────────────────────────────────────────────────────────────

    with st.expander("🔧 Debug info"):
        st.json(
            {
                "language_code":    LANGUAGE,
                "model":            MODEL,
                "region":           REGION,
                "project_id":       PROJECT_ID,
                "audio_bytes":      len(audio_bytes),
                "audio_kb":         round(len(audio_bytes) / 1024, 1),
                "confidence":       round(confidence, 4),
                "stt_ms":           round(stt_elapsed * 1000),
                "nmt_ms":           round(nmt_elapsed * 1000),
                "total_ms":         round(total_elapsed * 1000),
                "raw_transcript":   stt_result["transcript"],
                "raw_translation":  english_text,
            }
        )


def render_submissions_tab():
    st.subheader("All submissions")
    st.caption("Live view of the shared Google Sheet. Click Refresh to see new entries.")

    if st.button("🔄 Refresh", key="refresh_submissions"):
        st.cache_resource.clear()
        st.rerun()

    if not SHEET_ID:
        st.warning("SHEET_ID is not configured — cannot load submissions.")
        return

    try:
        with st.spinner("Loading submissions..."):
            rows = fetch_all_rows()

        if not rows:
            st.info("No submissions yet. Record and save a note to see it here.")
        else:
            st.success(f"{len(rows)} submission{'s' if len(rows) != 1 else ''} so far.")
            st.dataframe(
                rows,
                use_container_width=True,
                column_order=SHEET_HEADERS,
            )
    except Exception as exc:
        st.error(f"Could not load submissions: {exc}")
        with st.expander("Full traceback"):
            st.exception(exc)


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
        POC · Telugu (తెలుగు) Speech to English Transcription
    </p>
    """,
    unsafe_allow_html=True,
)
st.divider()

if not check_env():
    st.stop()  # Safe here — we're at the top level, not inside a tab

# ── Tabs ──────────────────────────────────────────────────────────────────────

tab_record, tab_submissions = st.tabs(["🎙️ Record & Review", "📊 Submissions"])

with tab_record:
    render_record_tab()

with tab_submissions:
    render_submissions_tab()

st.divider()
st.caption(
    "POC only · No audio stored · Results logged to Google Sheets"
)
