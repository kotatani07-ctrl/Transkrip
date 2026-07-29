# Transkrip & Ijazah

Aplikasi web Django untuk generate transkrip akademik mahasiswa dari Neo Feeder PDDIKTI.

## Stack

- **Backend**: Django 5.2 (Python)
- **Database**: SQLite (`artifacts/transkrip-app/db.sqlite3`)
- **Frontend**: Template HTML + Tailwind CSS + Vite (dibangun sebagai static files)
- **External**: Neo Feeder PDDIKTI (server lokal kampus, port 3003)

## Cara Menjalankan

Workflow: **artifacts/transkrip-app: web**
Script: `bash /home/runner/workspace/artifacts/transkrip-app/run.sh`

`run.sh` otomatis menjalankan migrasi DB, membuat superuser default, lalu menjalankan Django dev server.

## Environment Variables / Secrets

| Key | Jenis | Keterangan |
|---|---|---|
| `SECRET_KEY` | Secret | Django secret key |
| `DEBUG` | Env Var | `True` untuk development |
| `ALLOWED_HOSTS` | Env Var | Host yang diizinkan |
| `FEEDER_HOST` | Secret | IP server Neo Feeder kampus |
| `FEEDER_USERNAME` | Secret | Username Neo Feeder |
| `FEEDER_PASSWORD` | Secret | Password Neo Feeder |
| `DEFAULT_NAMA_DEKAN` | Env Var | Nama dekan default di transkrip |

## Akun Default

- **Username**: `admin`
- **Password**: `admin123`
- Ganti password via: `python manage.py changepassword admin`

## Struktur Aplikasi

```
artifacts/transkrip-app/
├── core/               # Django app utama (models, views, services)
├── transkrip_project/  # Settings & URL config Django
├── templates/          # HTML templates
├── static/             # Static files (CSS, JS)
├── media/              # Upload files (template Excel) — tidak di-git
├── db.sqlite3          # Database SQLite
├── requirements.txt    # Python dependencies
└── run.sh              # Entry point
```

## Fitur Utama

- **Generate Single**: Buat transkrip satu mahasiswa berdasarkan NIM
- **Generate Batch**: Upload daftar NIM, generate massal
- **Riwayat**: Log semua generate dengan filter
- **Django Admin** (`/admin/`): Setup kurikulum, template Excel, predikat kelulusan

## Catatan

- Neo Feeder harus terhubung ke jaringan intranet/VPN kampus
- Aplikasi tetap bisa diakses tanpa Feeder; hanya fitur generate yang tidak aktif
- Token Feeder di-cache in-memory, auto-refresh 5 menit sebelum expired
