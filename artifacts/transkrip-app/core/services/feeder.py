"""
Integrasi dengan Neo Feeder PDDIKTI.
Read-only: hanya memanggil GetToken, GetListMahasiswa, GetBiodataMahasiswa,
GetRiwayatNilaiMahasiswa.
TIDAK mengimplementasi fungsi Insert/Update/Delete apapun.

Flow pencarian per NIM:
  1. GetListMahasiswa        (filter: a.nim_mahasiswa='NIM')
         → id_mahasiswa, id_registrasi, jenjang_didik, tahun_masuk, nama_program_studi, dsb.
  2. GetBiodataMahasiswa     (filter: a.id_mahasiswa='...')
         → nama_mahasiswa, tempat_lahir, tanggal_lahir, dsb.
  3. GetRiwayatNilaiMahasiswa (filter: a.id_registrasi='...')
         → daftar nilai: kode_mata_kuliah, nama_mata_kuliah, sks, nilai_lambang, nilai_angka
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


def _call_feeder(payload: dict) -> dict:
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
            timeout=TIMEOUT_DETIK,
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
            f"Koneksi ke Neo Feeder timeout (>{TIMEOUT_DETIK} detik) "
            f"saat memanggil '{payload.get('act', '?')}'. "
            f"Server mungkin sedang lambat atau endpoint tidak merespons dari jaringan ini."
        )
    except Exception as e:
        raise RuntimeError(f"Error memanggil Neo Feeder '{payload.get('act', '?')}': {e}")


def get_token() -> str:
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
        response = _call_feeder(payload)

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


def get_program_mahasiswa(nim: str) -> dict:
    """
    Ambil data program/registrasi mahasiswa berdasarkan NIM via GetListMahasiswa.
    Return dict dengan field: id_mahasiswa, id_registrasi, nim_mahasiswa,
    jenjang_didik, tahun_masuk, tanggal_lulus, nama_program_studi, dsb.
    Raise exception jika tidak ditemukan atau timeout.
    """
    token = get_token()
    payload = {
        "act": "GetListMahasiswa",
        "token": token,
        "filter": f"a.nim_mahasiswa='{nim}'",
    }
    response = _call_feeder(payload)

    data = response.get('data', response) if isinstance(response, dict) else response

    if isinstance(data, list):
        if len(data) == 0:
            raise ValueError(
                f"Mahasiswa dengan NIM '{nim}' tidak ditemukan di Feeder "
                f"(GetListMahasiswa kosong)."
            )
        # Ambil entri terakhir (registrasi terbaru)
        data = data[-1]

    if not isinstance(data, dict):
        raise ValueError(
            f"Format response GetListMahasiswa tidak terduga: {response}"
        )

    return data


def get_biodata_mahasiswa(nim: str) -> dict:
    """
    Ambil biodata lengkap mahasiswa berdasarkan NIM.
    Flow: GetListMahasiswa → ambil id_mahasiswa → GetBiodataMahasiswa.
    Menggabungkan data program (jenjang, angkatan, tanggal_lulus) ke dalam hasil.
    """
    # Step 1: data program (mengandung id_mahasiswa + data akademik)
    program = get_program_mahasiswa(nim)

    id_mahasiswa = (
        program.get('id_mahasiswa')
        or program.get('id_mhs')
    )
    if not id_mahasiswa:
        # Jika GetListMahasiswa sudah ada nama_mahasiswa, kembalikan langsung
        if program.get('nama_mahasiswa'):
            logger.info(f"GetListMahasiswa sudah mengandung biodata untuk NIM {nim}")
            return program
        raise ValueError(
            f"GetListMahasiswa tidak mengembalikan id_mahasiswa untuk NIM '{nim}'. "
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
    Ambil daftar riwayat nilai mahasiswa berdasarkan NIM via GetRiwayatNilaiMahasiswa.
    Filter by id_registrasi (dari GetListMahasiswa), fallback ke nim_mahasiswa.
    Return list of dict dengan field: kode_mata_kuliah, nama_mata_kuliah,
    sks_mata_kuliah, nilai_lambang, nilai_angka.
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
        logger.info(f"GetRiwayatNilaiMahasiswa filter by id_registrasi={id_registrasi}")
    else:
        filter_str = f"a.nim_mahasiswa='{nim}'"
        logger.info(f"GetRiwayatNilaiMahasiswa fallback filter by nim_mahasiswa={nim}")

    payload = {
        "act": "GetRiwayatNilaiMahasiswa",
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


# Cache hasil check_connection agar tidak hit Feeder tiap load dashboard
_conn_cache = {
    'ok': None,
    'msg': '',
    'checked_at': 0,
}
_conn_lock = Lock()
CONN_CACHE_TTL = 60  # detik


def check_connection() -> tuple[bool, str]:
    """
    Cek koneksi nyata ke Neo Feeder dengan memanggil GetToken (timeout 5 detik).
    Hasil di-cache selama CONN_CACHE_TTL detik agar dashboard tidak lambat.
    Return: (ok: bool, pesan: str)
    """
    with _conn_lock:
        now = time.time()
        if _conn_cache['ok'] is not None and now - _conn_cache['checked_at'] < CONN_CACHE_TTL:
            return _conn_cache['ok'], _conn_cache['msg']

    # Cek konfigurasi dulu
    host = getattr(settings, 'FEEDER_HOST', '')
    username = getattr(settings, 'FEEDER_USERNAME', '')
    password = getattr(settings, 'FEEDER_PASSWORD', '')
    if not (host and username and password):
        result = (False, 'Konfigurasi FEEDER_HOST / USERNAME / PASSWORD belum lengkap.')
        _update_conn_cache(*result)
        return result

    url = f"http://{host}:3003/ws/live2.php"
    payload = {
        "act": "GetToken",
        "username": username,
        "password": password,
    }
    headers = {'Content-Type': 'application/json', 'Connection': 'close'}

    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=5)
        resp.raise_for_status()
        data = resp.json()
        token = (
            data.get('data', {}).get('token')
            or data.get('token')
            or data.get('data')
        ) if isinstance(data, dict) else None

        if token:
            # Simpan ke token cache juga agar tidak login ulang saat generate
            payload_data = _decode_jwt_payload(token)
            exp = payload_data.get('exp', time.time() + 3600)
            with _token_lock:
                _token_cache['token'] = token
                _token_cache['exp'] = exp
            result = (True, host)
        else:
            result = (False, f'Server merespons tapi GetToken gagal: {data}')

    except requests.exceptions.ConnectionError:
        result = (False, f'Tidak bisa menjangkau {host}:3003 — server mati atau IP salah.')
    except requests.exceptions.Timeout:
        result = (False, f'Koneksi ke {host}:3003 timeout (>5 detik).')
    except requests.exceptions.HTTPError as e:
        result = (False, f'HTTP error dari Feeder: {e}')
    except Exception as e:
        result = (False, f'Error: {e}')

    _update_conn_cache(*result)
    return result


def _update_conn_cache(ok: bool, msg: str):
    with _conn_lock:
        _conn_cache['ok'] = ok
        _conn_cache['msg'] = msg
        _conn_cache['checked_at'] = time.time()
