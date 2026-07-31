"""
Integrasi dengan Neo Feeder PDDIKTI.
Read-only: hanya memanggil GetToken, GetMahasiswaProgram, GetBiodataMahasiswa,
GetNilaiPerkuliahanMahasiswa.
TIDAK mengimplementasi fungsi Insert/Update/Delete apapun.

Flow pencarian per NIM:
  1. GetMahasiswaProgram  (filter: a.nim_mahasiswa)  → id_mahasiswa, id_registrasi, data program
  2. GetBiodataMahasiswa  (filter: a.id_mahasiswa)   → nama, tempat/tanggal lahir, dsb.
  3. GetNilaiPerkuliahanMahasiswa (filter: a.id_registrasi) → daftar nilai
"""
import base64
import json
import time
import logging
from threading import Lock

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

# Cache token secara in-memory (single-user, single-process)
_token_cache = {
    'token': None,
    'exp': 0,
}
_token_lock = Lock()

BUFFER_MENIT = 5        # refresh token 5 menit sebelum expired
TIMEOUT_DETIK = 90      # timeout per request ke Feeder
HEALTH_CHECK_TIMEOUT = 5  # timeout untuk pemeriksaan koneksi singkat


def _decode_jwt_payload(token: str) -> dict:
    """Decode payload JWT tanpa verifikasi signature."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += '=' * padding
        payload_json = base64.urlsafe_b64decode(payload_b64).decode('utf-8')
        return json.loads(payload_json)
    except Exception as e:
        logger.warning(f"Gagal decode JWT payload: {e}")
        return {}


def _get_feeder_url() -> str:
    host = settings.FEEDER_HOST
    if not host:
        raise ValueError("FEEDER_HOST belum dikonfigurasi di environment secrets")
    return f"http://{host}:3003/ws/live2.php"


def _call_feeder(payload: dict, timeout: int | None = None) -> dict:
    """
    Kirim request POST ke endpoint Neo Feeder.
    Return dict response atau raise exception.
    """
    url = _get_feeder_url()
    # Connection: close mencegah reuse koneksi yang sudah expired
    headers = {
        'Content-Type': 'application/json',
        'Connection': 'close',
    }
    try:
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=timeout if timeout is not None else TIMEOUT_DETIK,
        )
        resp.raise_for_status()
        data = resp.json()
        return data
    except requests.exceptions.ConnectionError as e:
        raise ConnectionError(
            f"Tidak bisa terhubung ke Neo Feeder ({url}). "
            f"Pastikan FEEDER_HOST sudah benar dan server aktif. Detail: {e}"
        )
    except requests.exceptions.Timeout:
        raise TimeoutError(
            f"Koneksi ke Neo Feeder timeout (>{timeout or TIMEOUT_DETIK} detik) "
            f"saat memanggil '{payload.get('act', '?')}'. "
            f"Server mungkin sedang lambat atau endpoint tidak merespons dari jaringan ini."
        )
    except Exception as e:
        raise RuntimeError(f"Error memanggil Neo Feeder '{payload.get('act', '?')}': {e}")


def get_token(timeout: int = TIMEOUT_DETIK) -> str:
    """
    Ambil token Neo Feeder.
    Gunakan cache; refresh otomatis jika akan expired dalam BUFFER_MENIT menit.
    """
    with _token_lock:
        now = time.time()
        buffer_seconds = BUFFER_MENIT * 60

        if _token_cache['token'] and _token_cache['exp'] - now > buffer_seconds:
            return _token_cache['token']

        logger.info("Mengambil token baru dari Neo Feeder...")
        payload = {
            "act": "GetToken",
            "username": settings.FEEDER_USERNAME,
            "password": settings.FEEDER_PASSWORD,
        }
        response = _call_feeder(payload, timeout=timeout)

        token = None
        if isinstance(response, dict):
            token = (
                response.get('data', {}).get('token')
                or response.get('token')
                or response.get('data')
            )

        if not token:
            raise ValueError(
                f"GetToken tidak mengembalikan token. Response: {response}"
            )

        payload_data = _decode_jwt_payload(token)
        exp = payload_data.get('exp', now + 3600)

        _token_cache['token'] = token
        _token_cache['exp'] = exp
        logger.info(f"Token berhasil didapat, berlaku hingga: {exp}")
        return token


def check_feeder_connection() -> tuple[bool, str]:
    """Periksa apakah Neo Feeder dapat dijangkau dan memberikan token."""
    try:
        get_token(timeout=HEALTH_CHECK_TIMEOUT)
        return True, ''
    except Exception as e:
        return False, str(e)


def get_program_mahasiswa(nim: str) -> dict:
    """
    Ambil data program/registrasi mahasiswa berdasarkan NIM via GetMahasiswaProgram.
    Return dict dengan field: id_mahasiswa, id_registrasi, nim_mahasiswa,
    jenjang_didik, tahun_masuk, tanggal_lulus, nama_program_studi, dsb.
    Raise exception jika tidak ditemukan atau timeout.
    """
    token = get_token()
    payload = {
        "act": "GetMahasiswaProgram",
        "token": token,
        "filter": f"a.nim_mahasiswa='{nim}'",
    }
    response = _call_feeder(payload)

    data = response.get('data', response) if isinstance(response, dict) else response

    if isinstance(data, list):
        if len(data) == 0:
            raise ValueError(
                f"Mahasiswa dengan NIM '{nim}' tidak ditemukan di Feeder "
                f"(GetMahasiswaProgram kosong)."
            )
        # Ambil entri terakhir (registrasi terbaru)
        data = data[-1]

    if not isinstance(data, dict):
        raise ValueError(
            f"Format response GetMahasiswaProgram tidak terduga: {response}"
        )

    return data


def get_biodata_mahasiswa(nim: str) -> dict:
    """
    Ambil biodata lengkap mahasiswa berdasarkan NIM.
    Flow: GetMahasiswaProgram → ambil id_mahasiswa → GetBiodataMahasiswa.
    Menggabungkan data program (jenjang, angkatan, tanggal_lulus) ke dalam hasil.
    """
    # Step 1: data program (mengandung id_mahasiswa + data akademik)
    program = get_program_mahasiswa(nim)

    id_mahasiswa = (
        program.get('id_mahasiswa')
        or program.get('id_mhs')
    )
    if not id_mahasiswa:
        # Jika GetMahasiswaProgram sudah ada nama_mahasiswa, kembalikan langsung
        if program.get('nama_mahasiswa'):
            logger.info(f"GetMahasiswaProgram sudah mengandung biodata untuk NIM {nim}")
            return program
        raise ValueError(
            f"GetMahasiswaProgram tidak mengembalikan id_mahasiswa untuk NIM '{nim}'. "
            f"Field tersedia: {list(program.keys())}"
        )

    # Step 2: biodata personal via id_mahasiswa
    token = get_token()
    payload = {
        "act": "GetBiodataMahasiswa",
        "token": token,
        "filter": f"a.id_mahasiswa='{id_mahasiswa}'",
    }
    try:
        response = _call_feeder(payload)
        biodata = response.get('data', response) if isinstance(response, dict) else response

        if isinstance(biodata, list):
            biodata = biodata[0] if biodata else {}
        if not isinstance(biodata, dict):
            biodata = {}
    except Exception as e:
        logger.warning(f"GetBiodataMahasiswa gagal untuk {nim}: {e}. Menggunakan data program saja.")
        biodata = {}

    # Gabungkan: program data sebagai base, biodata melengkapi
    merged = {**program, **biodata}
    # Pastikan nim_mahasiswa selalu ada
    if 'nim_mahasiswa' not in merged:
        merged['nim_mahasiswa'] = nim
    return merged


def get_nilai_perkuliahan_mahasiswa(nim: str) -> list:
    """
    Ambil daftar nilai perkuliahan mahasiswa berdasarkan NIM.
    Coba filter by id_registrasi (dari GetMahasiswaProgram), fallback ke nim_mahasiswa.
    """
    token = get_token()

    # Coba dapatkan id_registrasi dari data program
    id_registrasi = None
    try:
        program = get_program_mahasiswa(nim)
        id_registrasi = (
            program.get('id_registrasi')
            or program.get('id_reg')
        )
    except Exception as e:
        logger.warning(f"Tidak bisa ambil id_registrasi untuk {nim}: {e}")

    # Pilih filter terbaik
    if id_registrasi:
        filter_str = f"a.id_registrasi='{id_registrasi}'"
        logger.info(f"GetNilaiPerkuliahanMahasiswa filter by id_registrasi={id_registrasi}")
    else:
        filter_str = f"a.nim_mahasiswa='{nim}'"
        logger.info(f"GetNilaiPerkuliahanMahasiswa fallback filter by nim_mahasiswa={nim}")

    payload = {
        "act": "GetNilaiPerkuliahanMahasiswa",
        "token": token,
        "filter": filter_str,
    }
    response = _call_feeder(payload)

    data = response.get('data', response) if isinstance(response, dict) else response

    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return [data]
    return []


def clear_token_cache():
    """Reset cache token (untuk testing atau jika perlu force refresh)."""
    with _token_lock:
        _token_cache['token'] = None
        _token_cache['exp'] = 0
