"""
Integrasi dengan Neo Feeder PDDIKTI.
Read-only: hanya memanggil GetToken, GetBiodataMahasiswa, GetNilaiPerkuliahanMahasiswa.
TIDAK mengimplementasi fungsi Insert/Update/Delete apapun.
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

BUFFER_MENIT = 5  # refresh token 5 menit sebelum expired


def _decode_jwt_payload(token: str) -> dict:
    """Decode payload JWT tanpa verifikasi signature."""
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1]
        # Tambah padding jika perlu
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
        raise ValueError("FEEDER_HOST belum dikonfigurasi di .env")
    return f"http://{host}:3003/ws/live2.php"


def _call_feeder(payload: dict) -> dict:
    """
    Kirim request POST ke endpoint Neo Feeder.
    Return dict response atau raise exception.
    """
    url = _get_feeder_url()
    try:
        resp = requests.post(
            url,
            json=payload,
            headers={'Content-Type': 'application/json'},
            timeout=30,
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
        raise TimeoutError("Koneksi ke Neo Feeder timeout (>30 detik).")
    except Exception as e:
        raise RuntimeError(f"Error memanggil Neo Feeder: {e}")


def get_token() -> str:
    """
    Ambil token Neo Feeder.
    Gunakan cache; refresh otomatis jika akan expired dalam BUFFER_MENIT menit.
    """
    with _token_lock:
        now = time.time()
        buffer_seconds = BUFFER_MENIT * 60

        # Gunakan cache jika masih valid
        if _token_cache['token'] and _token_cache['exp'] - now > buffer_seconds:
            return _token_cache['token']

        # Request token baru
        logger.info("Mengambil token baru dari Neo Feeder...")
        payload = {
            "act": "GetToken",
            "username": settings.FEEDER_USERNAME,
            "password": settings.FEEDER_PASSWORD,
        }
        response = _call_feeder(payload)

        token = None
        if isinstance(response, dict):
            # Coba berbagai path response yang mungkin
            token = (
                response.get('data', {}).get('token')
                or response.get('token')
                or response.get('data')
            )

        if not token:
            raise ValueError(
                f"GetToken tidak mengembalikan token. Response: {response}"
            )

        # Decode exp dari JWT payload
        payload_data = _decode_jwt_payload(token)
        exp = payload_data.get('exp', now + 3600)  # default 1 jam jika tidak ada

        _token_cache['token'] = token
        _token_cache['exp'] = exp
        logger.info(f"Token berhasil didapat, berlaku hingga: {exp}")
        return token


def _build_biodata_payloads(nim: str) -> list[dict]:
    """Bangun daftar payload alternatif untuk GetBiodataMahasiswa."""
    payloads = []
    filters = [
        f"a.id_mahasiswa={nim}",
        f"a.id_mahasiswa='{nim}'",
        f"id_mahasiswa={nim}",
        f"id_mahasiswa='{nim}'",
        f"a.nim={nim}",
        f"a.nim='{nim}'",
        f"nim={nim}",
        f"nim='{nim}'",
        f"a.nik={nim}",
        f"a.nik='{nim}'",
        f"nik={nim}",
        f"nik='{nim}'",
        f"a.nama_mahasiswa='{nim}'",
        f"nama_mahasiswa='{nim}'",
    ]
    for filt in filters:
        payloads.append({"filter": filt})

    # Tambahkan payload yang menggunakan parameter langsung jika filter tidak cocok
    payloads.extend([
        {"nim": nim},
        {"id_mahasiswa": nim},
        {"nim_mahasiswa": nim},
        {"nama_mahasiswa": nim},
    ])
    return payloads


def _call_with_payloads(act: str, nim: str) -> dict:
    token = get_token()
    last_error = None

    if act == "GetBiodataMahasiswa":
        payload_candidates = _build_biodata_payloads(nim)
    else:
        payload_candidates = [
            {"filter": f"a.nim_mahasiswa='{nim}'"},
            {"filter": f"a.nik='{nim}'"},
            {"filter": f"nim='{nim}'"},
            {"filter": f"nik='{nim}'"},
            {"nim": nim},
            {"id_mahasiswa": nim},
            {"nik": nim},
        ]

    for extra in payload_candidates:
        payload = {
            "act": act,
            "token": token,
            **extra,
        }
        try:
            response = _call_feeder(payload)
        except Exception as e:
            logger.warning("Feeder call failed for payload %s: %s", extra, e)
            last_error = e
            continue

        if isinstance(response, dict) and response.get('error_code'):
            logger.warning("Feeder returned error for payload %s: %s", extra, response)
            last_error = RuntimeError(f"Feeder error: {response}")
            continue

        return response

    if last_error:
        raise last_error

    raise RuntimeError(f"Gagal memanggil {act}: tidak ada response valid dari Feeder.")


def get_biodata_mahasiswa(nim: str) -> dict:
    """
    Ambil biodata mahasiswa berdasarkan NIM.
    Return dict biodata atau raise exception.
    """
    response = _call_with_payloads("GetBiodataMahasiswa", nim)

    data = response.get('data', response) if isinstance(response, dict) else response

    if isinstance(data, list):
        if len(data) == 0:
            raise ValueError(f"Mahasiswa dengan NIM '{nim}' tidak ditemukan di Feeder.")
        data = data[0]

    if isinstance(data, dict):
        return data

    raise ValueError(f"Format response GetBiodataMahasiswa tidak terduga: {response}")


def get_nilai_perkuliahan_mahasiswa(nim: str) -> list:
    """
    Ambil daftar nilai perkuliahan mahasiswa berdasarkan NIM.
    Return list nilai atau raise exception.
    """
    response = _call_with_payloads("GetNilaiPerkuliahanMahasiswa", nim)

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
