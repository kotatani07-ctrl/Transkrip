# Transkrip & Ijazah

Aplikasi web Django untuk menghasilkan transkrip akademik mahasiswa secara otomatis. Data mahasiswa diambil dari **Neo Feeder PDDIKTI**, dicocokkan dengan kurikulum yang tersimpan, lalu diekspor sebagai file Excel siap cetak.

## Stack

- **Backend**: Python 3.11, Django 5.2
- **Database**: SQLite (`artifacts/transkrip-app/db.sqlite3`)
- **Export**: openpyxl
- **Integrasi**: Neo Feeder PDDIKTI (HTTP/JSON, read-only)

## Cara menjalankan

Gunakan workflow **Start application** (sudah terkonfigurasi).

Perintahnya:
```
cd artifacts/transkrip-app && bash run.sh
```

Server berjalan di port `8000`. `run.sh` otomatis menjalankan migrasi dan membuat superuser default.

## Akun default

| Username | Password  |
|----------|-----------|
| `admin`  | `admin123` |

> **Ganti password sebelum digunakan di lingkungan publik.**
> `python manage.py changepassword admin`

## Environment variables (Replit Secrets)

| Key                  | Keterangan                                              |
|----------------------|---------------------------------------------------------|
| `SECRET_KEY`         | Django secret key — sudah di-set otomatis               |
| `DEBUG`              | `True` (development)                                    |
| `FEEDER_HOST`        | IP/hostname server Neo Feeder kampus                    |
| `FEEDER_USERNAME`    | Username Neo Feeder                                     |
| `FEEDER_PASSWORD`    | Password Neo Feeder                                     |
| `DEFAULT_NAMA_DEKAN` | Nama dekan default di transkrip                         |

## Struktur proyek

```
artifacts/transkrip-app/    ← root aplikasi Django
├── core/                   ← app utama (models, views, admin)
├── transkrip_project/      ← settings, urls, wsgi
├── templates/              ← HTML templates
├── static/                 ← CSS/JS statis
├── run.sh                  ← entrypoint (migrate + runserver)
└── requirements.txt
```

## User preferences

- Pertahankan struktur dan stack yang sudah ada; jangan migrasi ke framework lain tanpa diminta.
