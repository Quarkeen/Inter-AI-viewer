# AI Interviewer

An interview application built with React, TypeScript and Tailwind on the frontend, and FastAPI, LangGraph and SQLite on the backend.

The active implementation now lives directly in this project. Make future changes in `frontend/` and `backend/`. The original `migrated-codebase/` is preserved as a migration snapshot and is not used by the running application.

## Project layout

```text
backend/                 API, agents, scheduling, reports and backend tests
  data/                  Current database, workflow checkpoints, vectors and models
frontend/                React UI, local vision assets and frontend tests
docs/                    Implementation and migration notes
samples/
  job_descriptions/      Test job descriptions and M.Tech DSAI admissions description
  resumes/               Test resumes, including the M.Tech DSAI sample
  archives/              Original sample bundles
output/                  Optional exported reports and other generated artifacts
backend/run.py           Backend launcher with NVIDIA library-path setup
requirements.txt         Includes backend/requirements.txt
migrated-codebase/       Preserved copy of the implementation before relocation
```

## Run locally

Use Python 3.12 and Node.js 22. The existing `.venv` and frontend dependencies have been retained on this machine. For a fresh checkout:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
npm ci --prefix frontend
```

Configure `backend/.env` using `backend/.env.example` if it does not already exist. It contains the Groq and invitation email settings. `frontend/.env` contains only `VITE_API_BASE_URL`; use `frontend/.env.example` for a fresh installation. Keep server credentials in the backend environment.

Start the API in one terminal, from the project root:

```bash
.venv/bin/python backend/run.py
```

Start the UI in another terminal:

```bash
npm --prefix frontend run dev -- --host localhost --port 5173 --strictPort
```

Open http://localhost:5173. The API is at http://localhost:8000 and its health endpoint is `/api/health`. Invitation links use `APP_BASE_URL`; `FRONTEND_ORIGIN` controls the allowed frontend origin. Both default to `http://localhost:5173`.

The backend launcher works independently of your shell's current directory and sets up the NVIDIA library paths used by local transcription. Use `backend/run.py` so the backend's existing local module imports resolve correctly.

## Main workflow

1. Create a role or admissions opening, then submit a resume.
2. Evaluate applications, generate a shortlist and record HR approval through the checkpointed LangGraph workflow.
3. Select an interview template, schedule a candidate and explicitly send the invitation.
4. The candidate completes device checks, mandatory eye calibration and interview instructions.
5. The interview agent asks main/follow-up questions. Faster Whisper transcribes locally; Chroma retrieves resume/JD context.
6. Generate the interview report and review answers, scores, timestamped warning snapshots and the HR decision.

Gaze warnings allow five seconds of reliable look-away before a reminder. They remain for human review and do not consume the fullscreen/face-absence termination allowance. See [gaze monitoring](docs/GAZE_MONITORING.md) for implementation limits.

## Checks

Run the backend tests from the project root:

```bash
.venv/bin/python -m unittest discover -s backend/tests
```

Run the frontend checks:

```bash
npm --prefix frontend test
npm --prefix frontend run build
```

Tests use temporary databases and mocked model calls. Production builds regenerate the local gaze worker. Runtime databases, credentials, model caches and installed dependencies are excluded from Git; preserve `backend/data/` separately when backing up or deploying this installation.

See [migration notes](docs/MIGRATION.md) for the data relocation and legacy backup. Sample JDs and resumes are indexed in [samples/README.md](samples/README.md).
