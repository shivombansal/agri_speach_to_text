# 🌾 Voice Field Notes — POC

> Tamil speech → English text, built for field agents with low digital literacy.  
> Google Cloud STT Chirp 2 · Cloud Translation v2 · Streamlit

---

## What this does

A field agent presses a mic button, speaks a farm observation in Tamil, and gets back:

- A Tamil transcript with punctuation
- An English translation
- A confidence score to flag uncertain recordings

No typing. No keyboard. No English required.

---

## Architecture

```
Browser mic  →  audio_recorder_streamlit
     ↓
Streamlit (Python)
     ↓                          ↓
Google STT V2              Google Translate v2
Chirp 2 · us-central1     Basic (REST)
     ↓                          ↓
Tamil transcript  +  English translation  →  UI review screen
```

---

## Supported languages

Change the `LANGUAGE` constant in `app.py` line ~20:

| Language | Code    |
|----------|---------|
| Tamil    | `ta-IN` |
| Bengali  | `bn-IN` |
| Kannada  | `kn-IN` |
| Hindi    | `hi-IN` |

---

## Confidence thresholds

| Score     | Badge | Action                    |
|-----------|-------|---------------------------|
| ≥ 85%     | 🟢    | Auto-accept, save enabled |
| 70–84%    | 🟡    | Flagged for review        |
| 40–69%    | 🟠    | Confirm before saving     |
| < 40%     | 🔴    | Re-record recommended     |

---

## Local setup

### 1. Clone and install

```bash
git clone https://github.com/your-org/voice-field-notes.git
cd voice-field-notes

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

pip install -r requirements.txt
```

### 2. GCP prerequisites

Enable both APIs in your GCP project:

- [Cloud Speech-to-Text API](https://console.cloud.google.com/apis/library/speech.googleapis.com)
- [Cloud Translation API](https://console.cloud.google.com/apis/library/translate.googleapis.com)

Create a service account with these roles:
- `roles/speech.client`
- `roles/cloudtranslate.user`

Download the JSON key file.

### 3. Set environment variables

```bash
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/your-key.json
export GOOGLE_CLOUD_PROJECT=your-project-id
```

Add to `~/.zshrc` or `~/.bashrc` to persist across sessions.

### 4. Run

```bash
streamlit run app.py
```

Opens at http://localhost:8501

---

## Streamlit Cloud deployment

No credentials go into Git. Everything lives in Streamlit's Secrets Manager.

### 1. Convert your JSON key to TOML

In **Streamlit Cloud → your app → Settings → Secrets**, paste:

```toml
[gcp_service_account]
type = "service_account"
project_id = "your-project-id"
private_key_id = "abc123"
private_key = "-----BEGIN RSA PRIVATE KEY-----\nMIIE...\n-----END RSA PRIVATE KEY-----\n"
client_email = "your-sa@your-project.iam.gserviceaccount.com"
client_id = "123456789"
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
```

Copy every field from your JSON key exactly. The `private_key` newlines must stay as `\n`.

### 2. Deploy

```
GitHub (no secrets) → Streamlit Cloud → connect repo → paste secrets → Deploy
```

The app detects whether it's running locally (reads from env var) or on Streamlit Cloud (reads from secrets) automatically — no code changes needed.

---

## Files

```
voice-field-notes/
├── app.py              ← entire application
├── requirements.txt
├── .gitignore          ← must exclude *.json and .env
└── README.md
```

### requirements.txt

```
streamlit
audio-recorder-streamlit
google-cloud-speech
google-cloud-translate
python-dotenv
```

### .gitignore

```
*.json
.env
.venv/
__pycache__/
```

---

## Test plan

Run 20 clips across three types before signing off on the POC:

**Type A — Simple observations**
> "இன்று பருத்தி நன்றாக வளர்கிறது"
> *(Today the cotton is growing well)*

**Type B — Agricultural vocabulary**
> "இரண்டு ஏக்கரில் DAP உரம் 50 கிலோ போட்டோம்"
> *(Applied 50 kg DAP fertiliser on 2 acres)*

**Type C — Noisy / natural speech**
Speak with background noise, filler words, mid-sentence pauses.

Log for each clip: transcript accuracy · confidence score · round-trip time.

---

## Known POC limitations

| Limitation | Phase 1 fix |
|---|---|
| No database write (Save shows a toast only) | Connect to Postgres / Firestore |
| No multi-tenant auth | Add Google / OTP login |
| Language hardcoded in source | Move to per-user profile setting |
| No fallback if Google APIs fail | Add retry + offline queue |
| Audio processed in `us-central1` (not India) | Switch to Chirp 3 on `asia-south1` when word confidence is supported |
| Audio not persisted anywhere | Intentional for POC — add GCS bucket in production if audit trail needed |

---

## Model notes

| | Chirp 2 (current) | Chirp 3 |
|---|---|---|
| Region | `us-central1` | `asia-south1` |
| Confidence score | ✅ Real value | ❌ Always returns 0 |
| Accuracy (agri vocab) | 🟡 Good | ✅ Better |
| Data residency | 🇺🇸 US | 🇮🇳 India |

Switch to Chirp 3 for production once Google adds confidence support for it in `asia-south1`. Change `MODEL` and `REGION` constants in `app.py` — nothing else needs to change.

---

## License

Internal POC — not for distribution.
