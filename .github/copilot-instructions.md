# Project instructions for this repository

## Repository overview
- This repository contains a FastAPI-based video analysis app in the app folder and a separate C# downloader project in the downloader folder.
- The Python app handles upload, video processing, scene extraction, analysis, and music recommendation.
- The downloader project is a separate .NET solution and should be treated as a distinct subsystem.

## Key folders
- app/: main FastAPI app, analysis pipeline, and API entrypoints
- downloader/: C# downloader solution and related projects
- tests/: unit tests for the Python app
- outputs/, frames/, uploads/: generated or runtime artifacts
- static/: frontend assets served by the FastAPI app

## Environment and prerequisites
- Use the project virtual environment if present.
- Ensure ffmpeg and ffprobe are available on PATH.
- Set the required API environment variables before running the app, including MISTRAL_API_KEY and MISTRAL_API_BASE_URL.

## Common commands
- Run unit tests:
  - Windows: .\.venv\Scripts\python.exe -m unittest discover -s tests -v
  - Cross-platform: python -m unittest discover -s tests -v
- Start the API server:
  - uvicorn app.main:app --reload --port 8000
- If you change the C# downloader code, build the solution with:
  - dotnet build downloader/YoutubeDownloader.slnx

## Working conventions
- Prefer minimal, targeted changes that preserve existing interfaces.
- Keep Python changes aligned with the existing FastAPI app structure in app/.
- If you change behavior, update or add tests in tests/ when practical.
- Avoid introducing new dependencies unless the change clearly justifies them.
- Treat generated content under outputs/, frames/, and uploads/ as runtime artifacts rather than source of truth.

## Verification expectations
- Before declaring a change complete, run the relevant tests or the smallest direct verification command that proves the behavior.
- For Python changes, prefer the unit test suite first.
- For API or pipeline changes, verify the relevant endpoint or processing path when possible.
