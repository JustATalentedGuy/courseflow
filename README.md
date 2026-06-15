# CourseFlow

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

CourseFlow turns a YouTube playlist into a structured, searchable, and reviewable
course. Paste a playlist URL and the system builds transcripts, detailed Markdown
notes, course exports, adaptive quizzes, spaced-repetition cards, semantic search,
and optional generated diagrams.

It is designed for learners who want the convenience of video courses without
losing the benefits of active recall, organized notes, and long-term review.

## Table of Contents

- [The Problem](#the-problem)
- [Who It Is For](#who-it-is-for)
- [What CourseFlow Does](#what-courseflow-does)
- [Feature Overview](#feature-overview)
- [How to Use CourseFlow Effectively](#how-to-use-courseflow-effectively)
- [Screenshots](#screenshots)
- [Architecture](#architecture)
- [System Design Concepts](#system-design-concepts)
- [Technology Stack](#technology-stack)
- [Project Structure](#project-structure)
- [Local Setup with Docker](#local-setup-with-docker)
- [Credential Configuration](#credential-configuration)
- [Running and Verifying the Application](#running-and-verifying-the-application)
- [Developer Setup](#developer-setup)
- [Testing](#testing)
- [API Overview](#api-overview)
- [Data, Privacy, and Security](#data-privacy-and-security)
- [Operations and Troubleshooting](#operations-and-troubleshooting)
- [AWS Production Deployment](#aws-production-deployment)
- [Claude Desktop MCP Integration](#claude-desktop-mcp-integration)
- [Current Scope](#current-scope)

## The Problem

YouTube contains complete university courses, certification tracks, engineering
lectures, and professional tutorials. The content is excellent, but the default
learning experience is passive:

- Videos are difficult to search after watching them.
- Notes are usually manual, inconsistent, and scattered.
- Important ideas are forgotten without active recall.
- Long playlists are hard to track and review.
- Rewatching an entire lesson is inefficient when only one concept is unclear.
- AI processing can fail midway when free-tier API limits are reached.
- Generated text often references a diagram without providing the visual.

CourseFlow treats a playlist as a durable learning project instead of a queue of
videos. It processes each lesson independently, preserves completed work across
restarts and rate limits, and creates several ways to learn from the same source:
reading, searching, quizzing, reviewing, and exporting.

## Who It Is For

CourseFlow is useful for:

- Students following university lectures or exam-preparation playlists.
- Software engineers studying system design, cloud, databases, DSA, or ML.
- Certification learners working through AWS, Azure, GCP, or security courses.
- Self-taught learners who need structure and accountability.
- Educators who want searchable notes and question prompts from public lessons.
- Developers interested in a practical AI pipeline with queues, quotas, vector
  search, durable jobs, and object storage.

The application currently supports YouTube playlists and individual YouTube
videos. It is a self-hosted system intended for personal or small-team use.

## What CourseFlow Does

The primary workflow is:

1. A user registers and submits a YouTube playlist or video URL.
2. CourseFlow reads playlist metadata without downloading every video.
3. Each video is placed on a background processing queue.
4. YouTube captions are used when available.
5. Videos without usable captions fall back to Groq Whisper transcription.
6. The transcript is cleaned, validated, chunked, and saved.
7. Groq generates structured Markdown notes from durable chunks.
8. Notes are embedded locally and indexed in PostgreSQL with pgvector.
9. The user can read, search, quiz, review, enrich, and export the course.

Processing is resumable. A worker restart or API rate limit does not force the
system to repeat already completed note or Whisper chunks.

## Feature Overview

### Course Ingestion and Tracking

- Accepts YouTube playlist, playlist-video, short-link, and single-video URLs.
- Uses `yt-dlp` flat extraction for efficient metadata ingestion.
- Preserves playlist order and skips deleted or unavailable entries.
- Deduplicates repeated YouTube video IDs.
- Tracks pending, processing, rate-limited, deferred, completed, and failed videos.
- Automatically dispatches background processing after course creation.
- Shows course progress and the next scheduled retry.
- Allows failed videos to be retried.
- Keeps every user isolated from other users' courses and artifacts.

### Transcript Extraction

- Prefers manually authored English YouTube captions.
- Falls back to generated English captions, then other available languages.
- Uses Groq `whisper-large-v3-turbo` when captions are unavailable.
- Downloads audio only when Whisper is required.
- Uses duration-aware download timeouts for large videos.
- Splits oversized audio at practical boundaries with overlap.
- Accounts for Groq's minimum billable duration per audio chunk.
- Removes filler words, caption artifacts, music annotations, and broken spacing.
- Stores a normalized transcript before note-generation quota checks.

### Durable AI Notes

- Uses Llama 4 Scout for normal automatic generation.
- Supports explicit high-quality regeneration with Llama 3.3 70B.
- Produces structured Markdown, summaries, headings, and key concepts.
- Splits long transcripts into durable generation chunks.
- Stores prompt fingerprints, output, model, usage, state, and retry time.
- Skips completed chunks after restarts or rate-limit responses.
- Tracks prompt, completion, cached, charged, and total token usage.
- Provides manual-assist copy/paste generation when external processing is not
  appropriate or available.

### Header-Aware Groq Scheduling

- Reads Groq rate-limit response headers from chat and audio requests.
- Tracks requests per minute/day, tokens per minute/day, and audio seconds.
- Uses organization-wide quotas because Groq limits are organization scoped.
- Reserves capacity atomically in Redis before concurrent workers send requests.
- Reconciles reservations with actual usage and authoritative server headers.
- Releases reservations for rejected `429` requests without charging local usage.
- Uses exact reset and `Retry-After` information for scheduling.
- Distinguishes short rate-limit waits from daily deferrals.
- Reconstructs Redis counters from a durable PostgreSQL usage ledger.
- Disables hidden SDK retries so Celery owns retry timing and durability.
- Supports optional Groq Batch overflow, disabled by default because it may be
  billed separately.

### Semantic Search

- Creates local 384-dimensional embeddings with `all-MiniLM-L6-v2`.
- Stores vectors in PostgreSQL using pgvector.
- Uses an HNSW cosine index for efficient similarity search.
- Searches across all of a user's notes or within one course.
- Returns lesson titles, matching text, similarity scores, and timestamped
  YouTube links.
- Provides section-heading autocomplete.

### Adaptive Quiz

- Uses a LangGraph state machine for Socratic quizzes.
- Supports quick drill, full review, and weak-spot modes.
- Grounds questions in relevant course notes.
- Adapts difficulty based on the learner's answers.
- Probes weak answers more deeply instead of immediately moving on.
- Saves active sessions in Redis and completed sessions in PostgreSQL.
- Tracks weak concepts across attempts.
- Uses semantic evaluation fallback when an LLM is unavailable.

### Spaced Repetition and Study Planning

- Seeds concept cards from generated notes and quiz outcomes.
- Uses the SM-2 scheduling algorithm.
- Maintains an immutable review history.
- Shows cards due today, retention, and review streaks.
- Supports manual card reviews.
- Builds an exam-date plan with daily capacity and overload reporting.
- Prioritizes overdue and difficult material.

### Diagram Enrichment

Diagram generation is optional and never changes course completion status.

- Discovers `{{DIAGRAM: ...}}` markers in canonical note Markdown.
- Uses transcript and section context to expand brief markers into detailed specs.
- Uses Mermaid for precise architecture, flow, sequence, and relationship diagrams.
- Uses Cloudflare Workers AI FLUX.2 Klein for illustrative content.
- Runs Mermaid in a private, restricted renderer service.
- Rejects unsafe Mermaid directives, external links, oversized sources, and
  excessive node counts.
- Stores generated images privately in MinIO under user-scoped immutable paths.
- Keeps source notes immutable and materializes generated assets at read/export time.
- Provides per-diagram prompt editing, mode selection, retry, regeneration, and
  removal.
- Shows readable caption callouts while a diagram is pending or unavailable.
- Uses a dedicated low-concurrency queue so diagram work does not block transcripts.
- Applies an independent Cloudflare daily budget with no paid overflow.

Cloudflare credentials are optional. Structured Mermaid diagrams still work
without them; only illustrative image generation becomes unavailable.

### Export

- Exports individual lessons as Markdown or PDF.
- Exports a completed course as one ordered Markdown or PDF document.
- Includes a linked table of contents and one numbered chapter per lesson.
- Removes duplicate lesson-title headings during course assembly.
- Embeds completed diagrams directly in Markdown exports.
- Uses refreshed private image URLs while rendering PDFs.
- Inserts readable callouts for unfinished diagrams instead of raw markers.
- Exports course concepts as an Anki `.apkg` deck.
- Rejects course-note export until every video is completed and has notes.

### Dashboard and User Experience

- JWT authentication with access and refresh tokens.
- Protected routes and automatic access-token refresh.
- Course library, progress monitoring, and status details.
- Markdown viewer with GFM tables, code blocks, and table of contents.
- Transcript, notes, diagrams, quizzes, exports, and retry controls.
- Dashboard retention, streak, recent activity, and quiz trends.
- Responsive React interface.

## How to Use CourseFlow Effectively

### Recommended Learning Loop

1. Start with a focused playlist rather than importing an entire channel.
2. Let CourseFlow finish transcription and notes generation in the background.
3. Read each lesson's summary before or immediately after watching the video.
4. Use timestamped search results to revisit only the relevant part of a lesson.
5. Run a quick drill after a study session.
6. Use weak-spot mode when CourseFlow identifies repeated mistakes.
7. Complete the cards due each day instead of reviewing everything at once.
8. Set an exam date to reveal whether the remaining review load is realistic.
9. Generate diagrams only for courses where visuals materially improve learning.
10. Export completed notes for offline study, annotation, or archival.

### Managing API Quotas

- Keep `GROQ_BATCH_ENABLED=false` unless paid Batch processing is intentional.
- Leave `GROQ_DAILY_RESERVE_PERCENT=0` to use the configured full allowance.
- Change the configured limits if the values shown in your Groq account differ.
- Do not restart or manually retry a rate-limited course repeatedly. CourseFlow has
  already scheduled the blocked chunk for the advertised reset time.
- Generate diagrams course by course. Illustrative generation has a separate daily
  budget and is intentionally explicit.
- Prefer normal generation first, then use high-quality regeneration only for
  lessons that need it.

### Choosing Diagram Modes

- Use `structured` for architectures, sequences, data flows, comparisons, trees,
  state transitions, and relationships.
- Use `illustrative` for conceptual scenes, metaphors, or visual explanations that
  do not require exact labels and arrows.
- Edit the prompt before regenerating when labels, entities, direction, or emphasis
  are wrong.

## Screenshots

### Youtube Link

<a href="screenshots/youtube_link.png">
  <img src="screenshots/youtube_link.png" width="700" />
</a>

### List of Courses

<a href="screenshots/list_of_courses.png">
  <img src="screenshots/list_of_courses.png" width="700" />
</a>

### Course Page

<a href="screenshots/course_page.png">
  <img src="screenshots/course_page.png" width="700" />
</a>

### List of Videos

<a href="screenshots/videos_with_statuses.png">
  <img src="screenshots/videos_with_statuses.png" width="700" />
</a>

### Video Page

<a href="screenshots/video_page.png">
  <img src="screenshots/video_page.png" width="700" />
</a>

### Quiz

<a href="screenshots/quiz_start.png">
  <img src="screenshots/quiz_start.png" width="700" />
</a>

### Quiz Question

<a href="screenshots/quiz_question.png">
  <img src="screenshots/quiz_question.png" width="700" />
</a>

### Search

<a href="screenshots/search_feature.png">
  <img src="screenshots/search_feature.png" width="700" />
</a>

### Notes PDF

<a href="screenshots/complete_course_pdf.png">
  <img src="screenshots/complete_course_pdf.png" width="700" />
</a>

### Notes with Generated Image

<a href="screenshots/generated_images.png">
  <img src="screenshots/generated_images.png" width="700" />
</a>

### Dashboard

<a href="screenshots/dashboard.png">
  <img src="screenshots/dashboard.png" width="700" />
</a>

## Architecture

```mermaid
flowchart TB
    User["Browser / React UI"] --> API["FastAPI REST API"]
    API --> Postgres["PostgreSQL + pgvector"]
    API --> Redis["Redis"]
    API --> MinIO["MinIO object storage"]

    API --> GeneralQueue["Celery default queue"]
    API --> DiagramQueue["Celery diagrams queue"]
    Beat["Celery Beat"] --> GeneralQueue
    Beat --> DiagramQueue

    GeneralQueue --> Worker["Transcript and notes workers"]
    DiagramQueue --> DiagramWorker["Low-concurrency diagram worker"]

    Worker --> YouTube["YouTube captions / yt-dlp"]
    Worker --> Groq["Groq LLM and Whisper APIs"]
    Worker --> Postgres
    Worker --> Redis

    DiagramWorker --> Groq
    DiagramWorker --> Mermaid["Private Mermaid renderer"]
    DiagramWorker --> Cloudflare["Cloudflare Workers AI"]
    DiagramWorker --> MinIO
    DiagramWorker --> Postgres
    DiagramWorker --> Redis
```

### Processing Lifecycle

```mermaid
flowchart LR
    Submit["Submit playlist"] --> Ingest["Create course and videos"]
    Ingest --> Queue["Queue videos in playlist order"]
    Queue --> Captions{"Captions available?"}
    Captions -->|Yes| Normalize["Normalize transcript"]
    Captions -->|No| Whisper["Durable Whisper chunks"]
    Whisper --> Normalize
    Normalize --> Notes["Durable note chunks"]
    Notes --> Embed["Local embeddings"]
    Embed --> Complete["Video completed"]
    Complete --> Learn["Read, search, quiz, review, export"]
    Complete --> Optional["Optional diagram enrichment"]
```

## System Design Concepts

CourseFlow intentionally demonstrates production-oriented system design patterns.

### Asynchronous Work Queues

Long-running transcription, LLM, embedding, PDF, and image operations do not block
HTTP requests. FastAPI creates work and returns quickly; Celery workers process it
independently through Redis-backed queues.

### Workload Isolation

General course processing and diagram rendering use different queues and
concurrency settings. Expensive image work cannot occupy transcript workers or
delay the core learning pipeline.

### Durable Checkpointing

Note-generation chunks, Whisper chunks, batch jobs, diagram assets, and usage
events are stored in PostgreSQL. A process restart resumes from persisted state
instead of repeating the entire video.

### Idempotency

Stable fingerprints and uniqueness constraints prevent duplicate chunks and
assets. Repeating a course diagram request discovers existing completed work and
queues only pending or retryable diagrams.

### Backpressure and Distributed Rate Limiting

Redis Lua scripts atomically reserve API capacity across workers. The scheduler
combines local rolling windows with server-provided headers, blocks requests until
the correct reset, and classifies short and daily exhaustion differently.

### Cache Plus Durable Source of Truth

Redis provides fast counters, reservations, session state, and retry gates.
PostgreSQL stores durable usage events. If Redis is lost, daily quota counters can
be reconstructed from the ledger.

### Eventual Consistency

Course processing is asynchronous. The frontend polls status while workers advance
videos and diagrams through durable states. The course remains usable while
optional enrichment continues.

### Tiered Fallbacks

- Captions before paid or limited transcription.
- Automatic Scout notes before explicit 70B regeneration.
- Mermaid before raster generation for precise technical visuals.
- LLM quiz evaluation with a local semantic fallback.
- Manual assist when external generation is undesirable.

### Retrieval-Augmented Generation

Notes are split into semantically searchable chunks. Quiz questions retrieve
relevant course content so prompts remain grounded in the learner's actual lesson.

### Vector Indexing

pgvector stores normalized embeddings, and an HNSW cosine index provides scalable
nearest-neighbor retrieval without a separate vector database.

### Immutable Source and Materialized Views

Canonical note Markdown is preserved in `source_markdown`. Diagram state and image
objects are separate. Display and export paths materialize the latest valid assets
without destructively rewriting original notes.

### Object Storage and Presigned Access

Generated images live in MinIO rather than database blobs. Objects use
user/video/asset/revision paths, and private display access uses short-lived
presigned URLs.

### State Machines

Videos and diagrams use explicit state transitions. LangGraph models the quiz
conversation as a state machine, while SM-2 models long-term review scheduling.

### Multi-Tenant Isolation

User-owned database rows carry `user_id`, service queries enforce ownership, and
object paths begin with the user UUID. Cross-user access is rejected.

### Failure Classification

Temporary network/5xx failures use bounded retries. Rate limits schedule work for a
known reset. Permanent validation or provider errors fail clearly instead of
creating retry storms.

## Technology Stack

### Frontend

- React 18 and TypeScript
- Vite
- React Router
- TanStack Query
- Tailwind CSS
- React Markdown with GFM
- Recharts
- Vitest, Testing Library, and MSW

### Backend

- Python 3.12
- FastAPI and Pydantic
- Async SQLAlchemy and asyncpg
- Alembic migrations
- Celery and Celery Beat
- PostgreSQL 16 with pgvector
- Redis 7
- MinIO
- Groq Python SDK
- LangGraph and LangChain Groq
- sentence-transformers
- yt-dlp, youtube-transcript-api, pydub, and ffmpeg
- markdown2, WeasyPrint, and genanki

### Diagram Renderer

- Node.js
- Express
- Mermaid CLI
- Chromium/Puppeteer
- Sharp

### External Services

- Groq for LLM notes, diagram specifications, quiz assistance, and Whisper
- Cloudflare Workers AI for optional illustrative diagrams
- YouTube for source metadata, captions, and fallback audio

## Project Structure

```text
courseflow/
|-- .github/workflows/ci.yml
|-- backend/
|   |-- app/
|   |   |-- api/v1/          # REST routes
|   |   |-- core/            # config, auth, logging, exceptions
|   |   |-- db/              # SQLAlchemy and Alembic
|   |   |-- models/          # persistent entities
|   |   |-- schemas/         # API and service contracts
|   |   |-- services/        # application logic
|   |   |-- tasks/           # Celery jobs
|   |   `-- workers/         # Celery configuration
|   `-- tests/
|-- diagram-renderer/        # isolated Mermaid-to-PNG service
|-- frontend/
|   `-- src/
|       |-- api/             # typed API clients
|       |-- auth/            # authentication context
|       |-- components/
|       |-- pages/
|       |-- test/
|       `-- types/
|-- .env.example
`-- docker-compose.yml
```

## Local Setup with Docker

Docker Compose is the recommended setup because it includes PostgreSQL, pgvector,
Redis, MinIO, migrations, backend workers, the diagram renderer, and the frontend.

### Prerequisites

- Git
- Docker Desktop or Docker Engine with Compose v2
- At least 8 GB of available RAM; 12 GB is more comfortable for builds and local
  embedding model use
- Free host ports: `5173`, `8000`, `5432`, `6379`, `9000`, and `9001`
- A Groq API key for live AI generation
- Optional Cloudflare credentials for illustrative diagrams

### 1. Clone the Repository

```bash
git clone <your-repository-url>
cd courseflow
```

### 2. Create the Environment File

Linux/macOS:

```bash
cp .env.example .env
```

PowerShell:

```powershell
Copy-Item .env.example .env
```

The real `.env` file is ignored by Git. Do not put credentials in
`.env.example`.

### 3. Generate a Secret Key

Use either command:

```bash
openssl rand -hex 32
```

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

Set the result as `SECRET_KEY` in `.env`.

### 4. Configure Required Credentials

At minimum, set:

```dotenv
SECRET_KEY=<long-random-secret>
GROQ_API_KEY=<your-groq-api-key>
```

For any non-local deployment, also replace the default PostgreSQL and MinIO
passwords.

### 5. Optionally Configure Cloudflare

To enable illustrative diagrams:

```dotenv
CLOUDFLARE_ACCOUNT_ID=<your-cloudflare-account-id>
CLOUDFLARE_API_TOKEN=<token-with-workers-ai-access>
```

Leave both values empty if you only want Mermaid diagrams.

### 6. Build and Start the Stack

```bash
docker compose up -d --build
```

The first build is substantial because the backend installs CPU PyTorch,
sentence-transformers, ffmpeg, and PDF-rendering dependencies.

### 7. Watch Startup

```bash
docker compose ps
docker compose logs -f backend worker diagram-worker
```

Wait for:

- PostgreSQL, Redis, MinIO, backend, and diagram renderer to become healthy.
- The migration container to exit successfully.
- Both Celery workers to report `ready`.

### 8. Open the Application

- Application: [http://localhost:5173](http://localhost:5173)
- API health: [http://localhost:8000/health](http://localhost:8000/health)
- API documentation: [http://localhost:8000/docs](http://localhost:8000/docs)
- MinIO console: [http://localhost:9001](http://localhost:9001)

Register a user, submit a YouTube playlist, and monitor its progress from the
course page.

## Credential Configuration

### Groq

1. Create or sign in to a Groq account.
2. Create an API key in the Groq console.
3. Add it to `.env` as `GROQ_API_KEY`.
4. Review the limits shown for your organization.
5. Adjust the `GROQ_*` values in `.env` if your account differs from the defaults.
6. Recreate backend services after changing credentials:

```bash
docker compose up -d --force-recreate backend worker diagram-worker beat
```

The configured values are local safety limits. Response headers remain
authoritative when Groq reports current remaining capacity and reset durations.

### Cloudflare Workers AI

1. Open your Cloudflare dashboard and copy the Account ID.
2. Create an API token authorized to run Workers AI inference for that account.
3. Set `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`.
4. Keep `CLOUDFLARE_DAILY_NEURON_BUDGET` below your desired daily allowance.
5. Recreate the backend and diagram worker.

The default image model is:

```dotenv
CLOUDFLARE_IMAGE_MODEL=@cf/black-forest-labs/flux-2-klein-4b
```

CourseFlow will not automatically generate illustrative diagrams for every course.
The user must explicitly start diagram generation.

### Important Environment Variables

| Variable | Purpose | Default |
|---|---|---|
| `POSTGRES_DB` | PostgreSQL database | `courseflow` |
| `POSTGRES_USER` | PostgreSQL user | `courseflow` |
| `POSTGRES_PASSWORD` | PostgreSQL password | `password` |
| `MINIO_ROOT_USER` | MinIO administrator | `minioadmin` |
| `MINIO_ROOT_PASSWORD` | MinIO administrator password | `minioadmin` |
| `SECRET_KEY` | JWT signing key | Must be replaced |
| `GROQ_API_KEY` | Groq authentication | Empty |
| `GROQ_BATCH_ENABLED` | Allow optional paid Batch overflow | `false` |
| `GROQ_DAILY_RESERVE_PERCENT` | Preserve part of daily token allowance | `0` |
| `YOUTUBE_PROXY_URL` | Rotating residential HTTP proxy for cloud deployments | Empty |
| `CLOUDFLARE_ACCOUNT_ID` | Workers AI account | Empty |
| `CLOUDFLARE_API_TOKEN` | Workers AI token | Empty |
| `CLOUDFLARE_DAILY_NEURON_BUDGET` | Local image-generation budget | `8000` |
| `CORS_ORIGINS` | Allowed frontend origins | `http://localhost:5173` |
| `VITE_API_URL` | Browser-visible API URL | `http://localhost:8000` |

See [.env.example](.env.example) for the complete configuration.

## Running and Verifying the Application

### Service Status

```bash
docker compose ps
```

### Health Checks

```bash
curl http://localhost:8000/health
curl -I http://localhost:5173
```

Expected API response:

```json
{"status":"ok"}
```

### Worker Health and Queue Isolation

```bash
docker compose exec backend \
  celery -A app.workers.celery_app inspect ping --timeout 10

docker compose exec backend \
  celery -A app.workers.celery_app inspect active_queues --timeout 10
```

One worker should consume the `celery` queue and the diagram worker should consume
the `diagrams` queue.

### Stop the Application

```bash
docker compose down
```

To remove local databases, Redis state, MinIO objects, and installed frontend
container dependencies:

```bash
docker compose down -v
```

This permanently deletes local CourseFlow data.

## Developer Setup

Docker remains necessary for PostgreSQL, Redis, and MinIO, but the backend and
frontend can be run directly for faster development.

### Backend

Create a Python 3.12 virtual environment:

PowerShell:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1
python -m pip install -r requirements.txt
```

Linux/macOS:

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.5.1
python -m pip install -r requirements.txt
```

Start infrastructure and run migrations:

```bash
docker compose up -d postgres redis minio minio-init diagram-renderer
python -m alembic upgrade head
```

Run the API:

```bash
python -m uvicorn app.main:app --reload
```

Run workers in separate terminals:

```bash
celery -A app.workers.celery_app.celery_app worker --loglevel=info --concurrency=2
```

```bash
celery -A app.workers.celery_app.celery_app worker \
  --loglevel=info --concurrency=1 -Q diagrams
```

```bash
celery -A app.workers.celery_app.celery_app beat --loglevel=info
```

Local backend processes need environment values that point to host services, for
example `localhost:5432`, `localhost:6379`, and `localhost:9000`. The root Compose
file automatically uses Docker service names inside containers.

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

### Diagram Renderer

The renderer is easiest to run through Docker because its image includes Chromium.
For direct Node development:

```bash
cd diagram-renderer
npm ci
npm start
```

A compatible Chromium executable must be installed and exposed through
`PUPPETEER_EXECUTABLE_PATH`.

## Testing

### Backend

```bash
docker compose exec backend pytest -q
```

The suite uses a separate test database and Redis database. Tests must never point
at the development database.

### Frontend

```bash
docker compose exec frontend npm test
docker compose exec frontend npm run type-check
docker compose exec frontend npm run build
```

### Diagram Renderer Audit

```bash
docker compose run --rm diagram-renderer npm audit --omit=dev
```

### Full Build

```bash
docker compose build
```

At the time of this README update, the verified baseline is:

- 134 backend tests passing
- 16 frontend tests passing
- Frontend type checking passing
- Frontend production build passing
- Docker Compose stack healthy

## API Overview

All application APIs are under `/api/v1`. Interactive OpenAPI documentation is
available at `/docs`.

| Area | Main endpoints |
|---|---|
| Authentication | `/auth/register`, `/auth/login`, `/auth/refresh`, `/auth/logout`, `/auth/me` |
| Courses | `/courses`, `/courses/{id}`, `/courses/{id}/status`, `/courses/{id}/notes` |
| Course exports | `/courses/{id}/export/anki`, `/courses/{id}/export/notes/markdown`, `/courses/{id}/export/notes/pdf` |
| Videos | `/videos/{id}`, `/videos/{id}/transcript`, `/videos/{id}/notes`, `/videos/{id}/retry` |
| Lesson exports | `/videos/{id}/export/markdown`, `/videos/{id}/export/pdf` |
| Manual assist | `/videos/{id}/manual-prompt`, `/videos/{id}/manual-notes` |
| Diagrams | `/courses/{id}/diagrams/generate`, `/courses/{id}/diagrams/status`, `/videos/{id}/diagrams` |
| Diagram edits | `/diagrams/{id}/regenerate`, `/diagrams/{id}` |
| Search | `/search`, `/search/suggest` |
| Quiz | `/quiz/start`, `/quiz/answer`, `/quiz/sessions/{video_id}`, `/quiz/weak-concepts` |
| Review | `/srs/due-today`, `/srs/cards`, `/srs/review`, `/srs/exam-plan`, `/srs/stats` |
| Quota | `/quota/usage` |

## Data, Privacy, and Security

- CourseFlow stores user accounts, course metadata, transcripts, notes, learning
  history, usage ledgers, and generated asset references in PostgreSQL.
- Generated images are stored in the configured private MinIO bucket.
- Redis stores task transport data, quota reservations, refresh-token state, active
  quizzes, and short-lived coordination data.
- Passwords are hashed with bcrypt.
- Access and refresh tokens have separate purposes and expiry windows.
- Login attempts are rate limited.
- User-owned service queries are scoped by `user_id`.
- Private MinIO objects are exposed through one-hour presigned URLs.
- Production startup rejects default or short secrets and wildcard CORS settings.
- Logs avoid transcript text and credentials.

Never commit `.env`, API keys, database dumps, generated object data, or access
tokens. Rotate a credential immediately if it is accidentally exposed.

## Operations and Troubleshooting

### A Course Is Waiting

Check the course status and quota page. `rate_limited` means a short reset is
pending; `deferred` means a daily dimension is exhausted. The scheduler will retry
automatically.

```bash
docker compose logs -f worker
```

### A Large Video Cannot Download Audio

CourseFlow calculates an audio-download timeout from video duration. The defaults
range from 120 to 900 seconds:

```dotenv
YOUTUBE_AUDIO_DOWNLOAD_MIN_TIMEOUT_SECONDS=120
YOUTUBE_AUDIO_DOWNLOAD_MAX_TIMEOUT_SECONDS=900
YOUTUBE_AUDIO_DOWNLOAD_TIMEOUT_SECONDS_PER_MINUTE=6
```

Increase the maximum only when the host connection or very long videos require it.

### YouTube Blocks the Cloud Server

YouTube commonly blocks AWS and other datacenter IP ranges for captions and audio
with `RequestBlocked` or "Sign in to confirm you're not a bot." CourseFlow routes
playlist metadata, captions, and audio through the same optional proxy:

```dotenv
YOUTUBE_PROXY_URL=http://username:password@proxy-host:port
```

Use a rotating residential HTTP proxy. Do not use a personal YouTube account's
cookies: they are brittle across IP addresses and can put the account at risk.
Keep the proxy URL only in `.env` or `.env.production`, never in Git. Recreate the
backend and workers after changing it:

```bash
docker compose --env-file .env.production -f docker-compose.production.yml \
  up -d --force-recreate backend worker
```

### Illustrative Diagrams Report `provider_unavailable`

Confirm both Cloudflare variables are set and recreate the backend/diagram worker:

```bash
docker compose up -d --force-recreate backend diagram-worker
docker compose logs -f diagram-worker
```

Mermaid diagrams do not require Cloudflare.

### Mermaid Rendering Fails

```bash
docker compose ps diagram-renderer
docker compose logs diagram-renderer
```

The renderer intentionally rejects unsafe or excessively complex sources. Edit the
diagram prompt or regenerate it.

### The Frontend Cannot Reach the API

Confirm:

```dotenv
VITE_API_URL=http://localhost:8000
CORS_ORIGINS=["http://localhost:5173"]
```

Then recreate the frontend and backend.

### Reset the Development Installation

```bash
docker compose down -v
docker compose up -d --build
```

This deletes all local application data.

## AWS Production Deployment

The repository includes the production artifacts described by the deployment
guide:

- `docker-compose.production.yml` runs PostgreSQL, Redis, migrations, the API,
  general and diagram workers, Celery Beat, the Mermaid renderer, and a static
  nginx frontend.
- `frontend/Dockerfile.production` builds the React bundle once and serves it
  through nginx instead of Vite's development server.
- `deploy/Caddyfile.example` routes HTTPS traffic to the API and frontend.
- `deploy/provision-aws.ps1` creates the tagged EC2, networking, Elastic IP,
  private S3, IAM role, and optional budget.
- `deploy/configure-github-oidc.ps1` gives only this repository's `main` branch
  permission to temporarily admit its current Actions runner to SSH.
- `deploy/configure-cloudwatch-dashboard.ps1` creates the CPU, memory, disk, and
  application-error dashboard from the tagged EC2 instance.
- `deploy/cloudwatch-agent.json` collects Docker logs and host CPU, memory, and
  disk metrics.
- `.github/workflows/deploy.yml` performs migration-gated SSH deployments.

### 1. Save Deployment Secrets Locally

```powershell
Copy-Item .env.deploy.example .env.deploy
```

Set the DuckDNS subdomain, DuckDNS token, and budget email in `.env.deploy`.
This file is ignored by Git.

### 2. Provision AWS

```powershell
.\deploy\provision-aws.ps1 -BillingEmail "you@example.com"
```

The script uses the `courseflow` AWS CLI profile, `ap-south-1`, the current
public IP, a `t4g.small` Ubuntu 24.04 ARM instance, 30 GB gp3 storage, and the
required `project=courseflow` and `env=demo` tags. It stores the new private SSH
key at `%USERPROFILE%\.ssh\courseflow-key.pem`.

### 3. Point DuckDNS to the Elastic IP

```powershell
.\deploy\update-duckdns.ps1 -IpAddress "<elastic-ip>"
```

### 4. Bootstrap and Configure the Server

Copy and run `deploy/bootstrap-ubuntu.sh` on the instance. Clone this repository
to `~/courseflow`, copy `.env.production.example` to `.env.production`, and set:

```dotenv
COURSEFLOW_DOMAIN=<subdomain>.duckdns.org
CORS_ORIGINS=["https://<subdomain>.duckdns.org"]
VITE_API_URL=https://<subdomain>.duckdns.org
AWS_S3_BUCKET=<bucket-returned-by-provisioning>
POSTGRES_PASSWORD=<strong-random-password>
SECRET_KEY=<openssl-rand-hex-32-output>
GROQ_API_KEY=<key>
```

Do not add AWS access keys. Containers obtain S3 credentials from the EC2 IAM
role.

Copy the configured Caddyfile to `/etc/caddy/Caddyfile`, restart Caddy, then run:

```bash
docker compose --env-file .env.production -f docker-compose.production.yml build
docker compose --env-file .env.production -f docker-compose.production.yml run --rm migrate
docker compose --env-file .env.production -f docker-compose.production.yml up -d
```

Install the CloudWatch agent configuration from
`deploy/cloudwatch-agent.json`. Verify the application over HTTPS, API health,
private S3 access, worker queues, and automatic restart behavior.

Create or refresh the CloudWatch dashboard:

```powershell
.\deploy\configure-cloudwatch-dashboard.ps1
```

![CourseFlow CloudWatch dashboard](docs/images/cloudwatch-dashboard.png)

### 5. Configure GitHub Actions

The production security group keeps SSH restricted to individual `/32`
addresses. A GitHub-hosted runner therefore needs short-lived permission to add
its own address for the duration of a deployment. Configure the repository-bound
OIDC role without storing AWS access keys:

```powershell
.\deploy\configure-github-oidc.ps1
```

Set the two repository variables printed by the script:

```powershell
gh variable set AWS_DEPLOY_ROLE_ARN --body "<role-arn>"
gh variable set AWS_SECURITY_GROUP_ID --body "<security-group-id>"
```

Also set `EC2_HOST` and `EC2_SSH_KEY` as repository secrets. On every push to
`main`, the workflow exchanges GitHub's OIDC token for short-lived AWS
credentials, permits only the active runner's IP on port 22, deploys, and
removes that rule in an `always()` cleanup step.

## Claude Desktop MCP Integration

CourseFlow includes a local read-only stdio MCP server with three tools:

- `list_my_courses`
- `search_my_courses`
- `ask_my_courses`

The server runs on the laptop and connects to the deployed PostgreSQL database.
Every query is scoped to `COURSEFLOW_MCP_USER_ID`. The answer tool retrieves
course excerpts first and returns timestamped sources.

Create its ignored environment file:

```powershell
Copy-Item backend\.env.mcp.example backend\.env.mcp
```

Set the deployed database URL, the UUID of the CourseFlow user to expose, and
the Groq key. Keep PostgreSQL port `5432` restricted to the current public IP in
`courseflow-sg`.

Merge `deploy/claude_desktop_config.example.json` into:

```text
%APPDATA%\Claude\claude_desktop_config.json
```

Replace the example paths with the absolute paths to the backend virtual
environment's `python.exe` and `deploy\run_mcp.py`, restart Claude Desktop, and
invoke `list_my_courses`. On startup, the MCP server reports
a targeted diagnostic when EC2 is stopped or the current IP is no longer
allowed by the security group.

## License

MIT
