"""
Logika pencocokan data nilai mahasiswa dengan kurikulum.
Sesuai PRD §6 dan §5 (bobot nilai).
"""
import logging
from decimal import Decimal, ROUND_HALF_UP
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

# Bobot nilai sesuai PRD §5 — tetap untuk semua angkatan
BOBOT_NILAI = {
    'A': Decimal('4.00'),
    'A-': Decimal('3.85'),
    'B+': Decimal('3.25'),
    'B': Decimal('3.00'),
    'B-': Decimal('2.85'),
    'C+': Decimal('2.35'),
    'C': Decimal('2.00'),
}


def get_bobot(nilai_lambang: str) -> Decimal:
    """Konversi lambang nilai ke bobot angka. Selain daftar di atas = 0."""
    return BOBOT_NILAI.get(str(nilai_lambang).strip() if nilai_lambang else '', Decimal('0'))


@dataclass
class HasilMK:
    """Hasil pencocokan satu mata kuliah."""
    kode_mk: str
    nama_mk: str
    sks: int
    urutan: int
    # Hasil pencocokan
    nilai_lambang: Optional[str] = None   # misal 'A', 'B+', dll
    nilai_angka: Optional[str] = None     # nilai numerik (string)
    bobot: Decimal = Decimal('0')
    mutu: Decimal = Decimal('0')          # SKS × bobot
    # Status
    is_retake: bool = False               # ada nilai >1 → ambil tertinggi
    is_kosong: bool = False               # tidak ada nilai di API
    # Catatan retake
    semua_nilai: list = field(default_factory=list)


@dataclass
class HasilTidakDikenali:
    """MK dari API yang kode-nya tidak ada di kurikulum."""
    kode_mk: str
    nama_mk: str
    sks_api: str
    nilai_lambang: str
    nilai_angka: str


@dataclass
class HasilPencocokan:
    """Hasil lengkap pencocokan satu mahasiswa."""
    nim: str
    nama: str
    jenjang: str
    angkatan: int
    prodi: str
    tempat_lahir: str
    tanggal_lahir: str
    tanggal_lulus: str
    tahun_lulus: int

    daftar_mk: list[HasilMK] = field(default_factory=list)
    mk_tidak_dikenali: list[HasilTidakDikenali] = field(default_factory=list)

    total_sks: int = 0
    total_mutu: Decimal = Decimal('0')
    ipk: Decimal = Decimal('0')
    predikat: str = ''

    jumlah_mk_retake: int = 0
    jumlah_mk_kosong: int = 0
    jumlah_mk_tidak_dikenali: int = 0


def _extract_field(data: dict, *keys, default='') -> str:
    """Coba beberapa nama field alternatif, return yang pertama ditemukan."""
    for key in keys:
        if key in data and data[key] is not None:
            return str(data[key]).strip()
    return default


def _extract_int(data: dict, *keys, default=0) -> int:
    for key in keys:
        if key in data and data[key] is not None:
            try:
                return int(data[key])
            except (ValueError, TypeError):
                pass
    return default


def parse_biodata(biodata: dict) -> dict:
    """
    Normalisasi biodata dari berbagai kemungkinan nama field Neo Feeder.
    Field names dari dokumentasi Feeder bisa berbeda-beda versi.
    """
    return {
        'nim': _extract_field(biodata, 'nim_mahasiswa', 'nim'),
        'nama': _extract_field(biodata, 'nama_mahasiswa', 'nama'),
        'jenjang': _extract_field(biodata, 'jenjang_didik', 'jenjang', 'id_jenjang'),
        'angkatan': _extract_int(biodata, 'tahun_masuk', 'angkatan'),
        'prodi': _extract_field(biodata, 'nama_program_studi', 'prodi', 'nama_prodi'),
        'tempat_lahir': _extract_field(biodata, 'tempat_lahir', 'kota_lahir'),
        'tanggal_lahir': _extract_field(biodata, 'tanggal_lahir', 'tgl_lahir'),
        # tanggal_lulus: coba berbagai nama field
        'tanggal_lulus': _extract_field(
            biodata,
            'tanggal_lulus', 'tgl_lulus', 'tanggal_kelulusan',
            'tgl_kelulusan', 'tanggal_sidang', 'tgl_sidang',
        ),
    }


def _parse_tahun_lulus(tanggal_lulus: str) -> int:
    """Ekstrak tahun dari string tanggal (YYYY-MM-DD atau DD/MM/YYYY atau YYYY)."""
    if not tanggal_lulus:
        return 0
    # Coba format YYYY-MM-DD
    if len(tanggal_lulus) >= 4:
        tahun_str = tanggal_lulus[:4]
        try:
            return int(tahun_str)
        except ValueError:
            pass
    # Coba DD/MM/YYYY
    if '/' in tanggal_lulus:
        parts = tanggal_lulus.split('/')
        if len(parts) == 3:
            try:
                return int(parts[2])
            except ValueError:
                pass
    return 0


def cocokan_nilai(
    daftar_mk_kurikulum: list,  # list MataKuliahKurikulum objects
    daftar_nilai_api: list,     # list dict nilai dari Feeder
    biodata_parsed: dict,
    predikat_qs,                # queryset PredikatKelulusan
    data_pendukung=None,        # optional DataPendukung object
    default_nama_dekan: str = '',
) -> HasilPencocokan:
    """
    Lakukan pencocokan nilai mahasiswa dengan kurikulum.
    Implementasi sesuai PRD §6.
    """
    tanggal_lulus = biodata_parsed.get('tanggal_lulus', '')
    tahun_lulus = _parse_tahun_lulus(tanggal_lulus)

    hasil = HasilPencocokan(
        nim=biodata_parsed.get('nim', ''),
        nama=biodata_parsed.get('nama', ''),
        jenjang=biodata_parsed.get('jenjang', ''),
        angkatan=biodata_parsed.get('angkatan', 0),
        prodi=biodata_parsed.get('prodi', ''),
        tempat_lahir=biodata_parsed.get('tempat_lahir', ''),
        tanggal_lahir=biodata_parsed.get('tanggal_lahir', ''),
        tanggal_lulus=tanggal_lulus,
        tahun_lulus=tahun_lulus,
    )

    # Build index nilai API berdasarkan kode_mk
    # kode_mk (lower) → list nilai
    index_nilai: dict[str, list[dict]] = {}
    for nilai in daftar_nilai_api:
        kode = _extract_field(nilai, 'kode_mata_kuliah', 'kode_mk', 'kode_matkul').lower()
        if kode:
            index_nilai.setdefault(kode, []).append(nilai)

    kode_mk_dikenali = set()

    # Proses setiap MK di kurikulum (urutan alfabetis)
    for mk in daftar_mk_kurikulum:
        kode = mk.kode_mk.lower()
        ditemukan = index_nilai.get(kode, [])
        kode_mk_dikenali.add(kode)

        hasil_mk = HasilMK(
            kode_mk=mk.kode_mk,
            nama_mk=mk.nama_mk,
            sks=mk.sks,
            urutan=mk.urutan_alfabet,
        )

        if len(ditemukan) == 0:
            # MK kosong → tandai merah
            hasil_mk.is_kosong = True
            hasil.jumlah_mk_kosong += 1

        elif len(ditemukan) == 1:
            # Normal
            n = ditemukan[0]
            lambang = _extract_field(n, 'nilai_lambang', 'lambang', 'grade_lambang', 'nilai_huruf')
            angka = _extract_field(n, 'nilai_angka', 'nilai_numerik', 'angka')
            hasil_mk.nilai_lambang = lambang
            hasil_mk.nilai_angka = angka
            hasil_mk.bobot = get_bobot(lambang)
            hasil_mk.mutu = Decimal(str(mk.sks)) * hasil_mk.bobot
            hasil_mk.semua_nilai = [lambang]

        else:
            # Retake → ambil mutu tertinggi
            hasil_mk.is_retake = True
            hasil.jumlah_mk_retake += 1

            # Cari nilai dengan bobot tertinggi
            best = None
            best_bobot = Decimal('-1')
            semua_lambang = []
            for n in ditemukan:
                lambang = _extract_field(n, 'nilai_lambang', 'lambang', 'grade_lambang', 'nilai_huruf')
                b = get_bobot(lambang)
                semua_lambang.append(lambang)
                if b > best_bobot:
                    best_bobot = b
                    best = n

            if best:
                lambang = _extract_field(best, 'nilai_lambang', 'lambang', 'grade_lambang', 'nilai_huruf')
                angka = _extract_field(best, 'nilai_angka', 'nilai_numerik', 'angka')
                hasil_mk.nilai_lambang = lambang
                hasil_mk.nilai_angka = angka
                hasil_mk.bobot = best_bobot
                hasil_mk.mutu = Decimal(str(mk.sks)) * best_bobot
                hasil_mk.semua_nilai = semua_lambang

        hasil.daftar_mk.append(hasil_mk)

    # MK di API yang kode-nya tidak dikenali kurikulum
    for kode, nilai_list in index_nilai.items():
        if kode not in kode_mk_dikenali:
            for n in nilai_list:
                mk_tidak_dikenali = HasilTidakDikenali(
                    kode_mk=_extract_field(n, 'kode_mata_kuliah', 'kode_mk', 'kode_matkul'),
                    nama_mk=_extract_field(n, 'nama_mata_kuliah', 'nama_mk', 'nama_matkul'),
                    sks_api=_extract_field(n, 'sks_mata_kuliah', 'sks'),
                    nilai_lambang=_extract_field(n, 'nilai_lambang', 'lambang', 'nilai_huruf'),
                    nilai_angka=_extract_field(n, 'nilai_angka', 'nilai_numerik', 'angka'),
                )
                hasil.mk_tidak_dikenali.append(mk_tidak_dikenali)
                hasil.jumlah_mk_tidak_dikenali += 1

    # Hitung total SKS dan IPK
    total_sks = 0
    total_mutu = Decimal('0')
    for mk in hasil.daftar_mk:
        if not mk.is_kosong and mk.nilai_lambang:
            total_sks += mk.sks
            total_mutu += mk.mutu

    hasil.total_sks = total_sks
    hasil.total_mutu = total_mutu

    if total_sks > 0:
        hasil.ipk = (total_mutu / Decimal(str(total_sks))).quantize(
            Decimal('0.01'), rounding=ROUND_HALF_UP
        )
    else:
        hasil.ipk = Decimal('0.00')

    # Tentukan predikat
    hasil.predikat = tentukan_predikat(predikat_qs, tahun_lulus, hasil.ipk)

    return hasil


def tentukan_predikat(predikat_qs, tahun_lulus: int, ipk: Decimal) -> str:
    """
    Cari predikat kelulusan berdasarkan tahun lulus dan IPK.
    Sesuai model PredikatKelulusan di PRD §4.
    """
    if not tahun_lulus:
        return ''
    for p in predikat_qs:
        # Cek rentang tahun
        if p.tahun_lulus_mulai <= tahun_lulus:
            if p.tahun_lulus_selesai is None or tahun_lulus <= p.tahun_lulus_selesai:
                # Cek rentang IPK
                if Decimal(str(p.ipk_min)) <= ipk <= Decimal(str(p.ipk_max)):
                    return p.predikat
    return ''
