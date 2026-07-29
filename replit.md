# Transkrip App

Django web application for generating academic transcripts (transkrip akademik) for an Indonesian university. Connects to **Neo Feeder PDDIKTI** to fetch student data, matches it against curricula, and exports transcripts as Excel files.

## Stack

- **Backend**: Django 5.2, SQLite
- **Python packages**: django, openpyxl, python-dotenv, requests
- **Templates**: Django server-side HTML templates

## How to Run

The workflow `Transkrip App (Django)` runs `bash artifacts/transkrip-app/run.sh`, which:
1. Loads `.env` if present
2. Runs database migrations
3. Creates a default admin superuser (`admin` / `admin123`) if not yet created
4. Starts Django dev server on `$PORT`

## Default Login

- **Username**: `admin`
- **Password**: `admin123`

## Environment Variables

Configure these in Replit Secrets or in `artifacts/transkrip-app/.env` (see `.env.example`):

| Variable | Description |
|---|---|
| `FEEDER_HOST` | IP/hostname of the Neo Feeder PDDIKTI server |
| `FEEDER_USERNAME` | Neo Feeder username |
| `FEEDER_PASSWORD` | Neo Feeder password |
| `SECRET_KEY` | Django secret key (use a strong random value in production) |
| `DEFAULT_NAMA_DEKAN` | Default dean name printed on transcripts |
| `DEBUG` | `True` for development, `False` for production |

The app runs without Feeder credentials — you just won't be able to generate transcripts until they're configured.

## User Preferences
