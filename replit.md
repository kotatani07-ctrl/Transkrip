# Sistem Otomasi Transkrip & Ijazah

Aplikasi web Django internal untuk generate Transkrip Akademik mahasiswa secara otomatis dari data Neo Feeder PDDIKTI. Output file Excel siap cetak, read-only terhadap Neo Feeder.

## Run & Operate

- `bash artifacts/transkrip-app/run.sh` — jalankan Django dev server (port 8000)
- Login default: **admin / admin123** (ganti segera via Admin Panel)
- Admin Panel: `/admin/`

## Stack

- Python 3.11 + Django 5.2
- Database: SQLite (`artifacts/transkrip-app/db.sqlite3`)
- Excel: openpyxl 3.1
- HTTP client: requests (Neo Feeder)
- Frontend: Django Templates + Bootstrap 5.3

## Konfigurasi Neo Feeder

Salin `.env.example` ke `.env` di dalam `artifacts/transkrip-app/`:

```
FEEDER_HOST=192.168.1.100          # IP server Neo Feeder
FEEDER_USERNAME=username_feeder
FEEDER_PASSWORD=password_feeder
SECRET_KEY=ganti-dengan-key-aman
DEFAULT_NAMA_DEKAN=Dr. Nama Dekan, M.T.
```

## Where things live

```
artifacts/transkrip-app/
├── manage.py
├── run.sh                        # Entry point workflow
├── .env.example                  # Template konfigurasi
├── transkrip_project/            # Django project settings, urls, wsgi
├── core/
│   ├── models.py                 # Semua model (Kurikulum, Template, dll)
│   ├── admin.py                  # Admin + upload Excel kurikulum
│   ├── views.py                  # Views: dashboard, single, batch, riwayat
│   ├── forms.py
│   ├── urls.py
│   ├── migrations/
│   └── services/
│       ├── feeder.py             # Integrasi Neo Feeder (read-only)
│       ├── matching.py           # Logika pencocokan + hitung IPK/predikat
│       └── excel_gen.py          # Generator Excel output
└── templates/                    # HTML templates (Django + Bootstrap)
```

## Architecture decisions

- Token JWT Neo Feeder di-cache in-memory dengan auto-refresh proaktif 5 menit sebelum `exp`
- Neo Feeder: HANYA `GetToken`, `GetBiodataMahasiswa`, `GetNilaiPerkuliahanMahasiswa` — tidak ada Insert/Update/Delete
- Pencocokan MK: retake → ambil mutu tertinggi (kuning), kosong → tandai merah, tak dikenali → sheet laporan (oranye)
- IPK dihitung ulang dari data nilai final setelah dedup retake: Σ(SKS×Bobot)/ΣSKS
- Template Excel diisi via `cell_mapping` JSON — tidak membuat layout baru, isi ke template asli
- Batch Excel dikelompokkan per (jenjang, angkatan), page break per mahasiswa

## Workflow Generate

1. **Setup admin**: Upload Template Excel + isi `cell_mapping` → Admin → Templates
2. **Upload kurikulum**: Admin → Kurikulum → Upload Excel Kurikulum → pilih jenjang/angkatan/template
3. **Data pendukung**: Admin → Data Pendukung → isi NIM, No. Ijazah, Judul Skripsi
4. **Predikat kelulusan**: Admin → Predikat Kelulusan → isi aturan IPK per tahun
5. **Generate single**: Menu Generate Single → input NIM → download xlsx
6. **Generate batch**: Menu Generate Batch → upload file NIM → download xlsx per angkatan

## User preferences

- Aplikasi lokal, single-user, bahasa Indonesia
- Django dengan SQLite (tidak perlu PostgreSQL)
- Output Excel pakai template asli user, bukan layout baru

## Gotchas

- `cell_mapping` di Template harus diisi manual setelah file template asli diperiksa struktur cell-nya
- Field names dari `GetBiodataMahasiswa` berbeda antar versi Neo Feeder — lihat `services/matching.py` fungsi `parse_biodata()` untuk normalisasi
- Jenjang dari Feeder bisa berupa kode angka ('3') atau nama lengkap ('DIPLOMA TIGA') — dinormalisasi di `views.py` `_proses_satu_nim()`
- Jalankan selalu dari root project: `bash artifacts/transkrip-app/run.sh`
