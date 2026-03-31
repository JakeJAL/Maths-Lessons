# AI Slide Agent

An AI-powered Google Slides presentation builder for UK maths teachers. Upload a template PowerPoint, describe the lesson you need, and the system generates curriculum-aligned slides with properly formatted equations, worked examples, and practice questions — all in the template's design.

## How It Works

1. **Sign in** with your Google account — presentations are created in your own Google Drive
2. **Upload** a reference PowerPoint (.pptx) as a template
3. **Describe** the lesson you want (e.g. "Year 9 multi-step equations")
4. The system generates maths content, reviews it for accuracy, maps it to your template's layout, and creates the presentation
5. **Preview** the slides live in the browser and request changes via chat

## Architecture

```
User → Flask App → Orchestrator Agent (Gemini 2.0 Flash)
                        ├── search_curriculum_content (RAG / ChromaDB)
                        ├── generate_maths_content (Gemini 2.5 Flash)
                        ├── review_maths_content (Gemini 2.0 Flash)
                        ├── build_slide_replacements (Gemini 2.5 Flash)
                        └── create_presentation (Google Slides API)
```

### Agents
- **Orchestrator** — coordinates the workflow, handles chat, calls tools
- **Maths Agent** — generates curriculum-aligned questions, worked examples, and practice problems
- **Review Agent** — verifies mathematical correctness and pedagogical quality
- **Slide Builder** — maps generated content to template slide shapes by role

### Key Features
- **Template-aware replacement** — preserves banners, formatting, and layout from uploaded PowerPoints
- **Role-based shape matching** — equations replace equations, answers replace answers, labels replace labels
- **RAG curriculum search** — 410 indexed lessons from Oak National Academy provide reference material
- **Per-user Google OAuth** — each user's presentations go to their own Drive
- **Live preview** — embedded Google Slides viewer in the chat interface
- **Speaker notes** — solutions and answers automatically added for teacher reference

## Project Structure

```
├── app.py                  # Flask entry point, routes, file upload
├── tools.py                # Tool executor — bridges agent calls to APIs
├── state.py                # In-memory session state
│
├── agents/
│   ├── base.py             # Shared API calling, model config
│   ├── maths.py            # Maths content generator + reviewer
│   ├── slide_builder.py    # Maps content to template shapes (batched)
│   └── orchestrator.py     # Main agent loop, tool definitions, system prompt
│
├── services/
│   ├── auth.py             # Per-user Google OAuth web flow
│   ├── slides_api.py       # Google Slides/Drive API wrapper
│   └── rag.py              # ChromaDB curriculum search + indexing
│
├── templates/
│   ├── index.html          # Chat UI with side-by-side slides preview
│   └── login.html          # Google sign-in page
│
├── example slides/         # Oak National Academy curriculum content (410 lessons)
├── Dockerfile              # Production container config
└── deploy.sh               # Cloud Run deployment script
```

## Setup

### Prerequisites
- Python 3.12+
- Google Cloud project with Slides API and Drive API enabled
- Google OAuth 2.0 Web Application credentials
- OpenRouter API key
- Unsplash API key (for image search)

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API keys

# Allow OAuth over HTTP for local dev
export OAUTHLIB_INSECURE_TRANSPORT=1

# Run
python app.py
```

Visit `http://localhost:5000`

### Environment Variables

| Variable | Description |
|---|---|
| `OPENROUTER_API_KEY` | OpenRouter API key for LLM access |
| `GOOGLE_CLIENT_ID` | Google OAuth Web Application client ID |
| `GOOGLE_CLIENT_SECRET` | Google OAuth client secret |
| `GOOGLE_PROJECT_ID` | Google Cloud project ID |
| `UNSPLASH_ACCESS_KEY` | Unsplash API key for image search |
| `FLASK_SECRET_KEY` | Random string for Flask session encryption |
| `OAUTH_REDIRECT_URI` | OAuth callback URL (e.g. `http://localhost:5000/auth/callback`) |

### Deployment (Google Cloud Run)

1. Push to GitHub
2. In Cloud Run console: Create Service → Continuously deploy from repository
3. Set environment variables in the Cloud Run configuration
4. After deploy, add the Cloud Run URL + `/auth/callback` to your Google OAuth redirect URIs
5. Update `OAUTH_REDIRECT_URI` env var with the Cloud Run URL and redeploy

## Tech Stack

- **Backend**: Flask, Gunicorn
- **LLMs**: Google Gemini 2.0 Flash / 2.5 Flash via OpenRouter
- **APIs**: Google Slides API, Google Drive API, Unsplash API
- **RAG**: ChromaDB with default ONNX embeddings
- **Auth**: Google OAuth 2.0 (web server flow)
- **Deployment**: Docker, Google Cloud Run
