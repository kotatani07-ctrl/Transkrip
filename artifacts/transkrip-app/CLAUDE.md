# CLAUDE.md — Konteks Proyek untuk AI

> **Aturan wajib:** File ini harus diperbarui setiap kali ada perubahan besar pada proyek.
> Perubahan besar meliputi: penambahan fitur utama, perubahan model database, perubahan alur generate, penggantian library, perubahan struktur folder, atau perubahan integrasi eksternal.

---

## Ringkasan Proyek

**Transkrip & Ijazah** adalah aplikasi Django untuk menghasilkan transkrip akademik mahasiswa secara otomatis. Data mahasiswa diambil dari **Neo Feeder PDDIKTI** (sistem nasional data pendidikan tinggi Indonesia), dicocokkan dengan kurikulum yang diinput manual, lalu diekspor sebagai Excel.

---

## Stack Teknis

| Komponen | Detail |
|---|---|
| Language | Python 3.11 |
| Framework | Django 5.2 |
| Database | SQLite (`db.sqlite3`) |
| Excel I/O | openpyxl 3.1 |
| HTTP Client | requests 2.31 |
| Env management | python-dotenv 1.0 |
| Template engine | Django HTML templates (bukan React/Vue) |
| Auth | Django session-based (bawaan) |

---

## Struktur File Kritis

```
artifacts/transkrip-app/
├── core/
│   ├── models.py           ← Semua model database
│   ├── views.py            ← Semua view (dashboard, generate, riwayat)
│   ├── admin.py            ← Admin Django + logika upload kurikulum Excel
│   ├── forms.py            ← Form Django
│   └── services/
│       ├── feeder.py       ← Integrasi Neo Feeder (JANGAN modifikasi tanpa tes)
│       ├── matching.py     ← Algoritma pencocokan MK ← logika inti
│       └── excel_gen.py    ← Generate output Excel
├── transkrip_project/
│   └── settings.py         ← Konfigurasi utama + baca env vars
├── templates/              ← HTML templates
├── run.sh                  ← Entry point: migrasi + buat admin + runserver
└── requirements.txt
```

---

## Model Database (ringkasan)

```python
Template          # File Excel template + cell_mapping JSON
Kurikulum         # Per jenjang (D3/S1) + angkatan → FK ke Template
MataKuliahKurikulum  # MK dalam kurikulum (kode, nama, SKS, urutan)
PredikatKelulusan # Range IPK → predikat per rentang tahun lulus
DataPendukung     # Data tambahan per NIM (skripsi, no. ijazah, dekan)
RiwayatGenerate   # Log setiap generate (NIM, status, IPK, mode, user)
```

---

## Alur Generate Transkrip

1. User input NIM → `views._proses_satu_nim()`
2. `feeder.get_biodata_mahasiswa(nim)` → ambil jenjang + angkatan
3. Lookup `Kurikulum` by (jenjang, angkatan) → dapat daftar MK + Template
4. `feeder.get_nilai_perkuliahan_mahasiswa(nim)` → semua nilai dari Feeder
5. `matching.cocokan_nilai()` → cocokkan MK kurikulum ↔ nilai Feeder
   - MK retake: ambil nilai terbaik
   - MK kosong: ditandai, tidak mengisi nilai
   - MK tidak dikenali: nilai ada di Feeder tapi tidak ada di kurikulum
6. Hitung IPK, lookup `PredikatKelulusan`
7. `excel_gen.generate_single_excel()` / `generate_batch_excel()` → tulis ke template Excel
8. Return `HttpResponse` dengan file `.xlsx`

---

## Integrasi Neo Feeder

- Endpoint: `http://{FEEDER_HOST}:3003/ws/live2.php`
- Auth: JWT token via `act=GetToken`, di-cache in-memory (thread-safe dengan `Lock`)
- Token auto-refresh 5 menit sebelum expired
- Hanya 3 action yang dipakai: `GetToken`, `GetBiodataMahasiswa`, `GetNilaiPerkuliahanMahasiswa`
- **Read-only** — tidak ada write ke Feeder

---

## Variabel Environment

```env
SECRET_KEY          # Django secret key
DEBUG               # True / False
ALLOWED_HOSTS       # Koma-separated hosts
CSRF_TRUSTED_ORIGINS
FEEDER_HOST         # IP server Neo Feeder (tanpa http://)
FEEDER_USERNAME
FEEDER_PASSWORD
DEFAULT_NAMA_DEKAN  # Nama dekan default di transkrip
```

---

## Hal Penting untuk AI

- **Jangan ubah `feeder.py`** tanpa memahami format response Neo Feeder — response bisa berupa `dict`, `list`, atau nested `data` key
- **`cell_mapping` di Template adalah JSON bebas** — koordinat cell Excel dikonfigurasi per template, bukan hardcode
- **Batch maks 100 NIM** dan ada jeda 0.3 detik per NIM — jangan hilangkan jeda ini
- **MK retake** dideteksi berdasarkan `kode_mk` yang sama muncul lebih dari sekali di nilai Feeder — ambil yang terbaik
- **Angkatan** diambil dari biodata Feeder, bukan dari NIM
- `run.sh` membuat superuser `admin/admin123` otomatis jika belum ada — ini hanya untuk development
- Template Excel di-upload user dan disimpan di `media/templates/` (tidak masuk Git)
- Database `db.sqlite3` tidak masuk Git

---

## Riwayat Perubahan Besar

| Tanggal | Perubahan |
|---|---|
| 2026-07-29 | Setup awal proyek di Replit, instalasi dependencies, konfigurasi workflow |
| 2026-07-29 | Tema glassmorphism iPhone-inspired untuk Django admin: `templates/admin/base_site.html`, `templates/admin/login.html`, `templates/admin/index.html`, `static/admin/css/glass-admin.css` |

> Tambahkan baris baru di tabel ini setiap ada perubahan besar.
