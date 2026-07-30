"""
Integrasi dengan Neo Feeder PDDIKTI.
Read-only: hanya memanggil GetToken, GetBiodataMahasiswa, GetRiwayatNilaiMahasiswa.
TIDAK mengimplementasi fungsi Insert/Update/Delete apapun.

Masalah arsitektur Feeder:
  Tabel `a` di GetBiodataMahasiswa & GetListMahasiswa adalah tabel `mahasiswa`
  (hanya punya id_mahasiswa, nama_mahasiswa). Kolom nim_mahasiswa ada di tabel
  `registrasi_mahasiswa` yang di-join dengan alias berbeda tergantung instalasi
  Feeder (umumnya `b`, tapi ada juga yang tanpa prefix).

Strategi:
  _find_biodata_by_nim() mencoba urutan filter berikut sampai berhasil:
    1. b.nim_mahasiswa  (registrasi sebagai tabel b — paling umum)
    2. nim_mahasiswa    (tanpa prefix tabel)
    3. c.nim_mahasiswa  (alias alternatif)

  _find_nilai_by_nim() mencoba:
    1. a.nim_mahasiswa  (GetRiwayatNilaiMahasiswa — registrasi bisa jadi tabel a)
    2. b.nim_mahasiswa
    3. nim_mahasiswa    (tanpa prefix)
    Atau fallback via id_registrasi dari biodata.

Flow:
  get_biodata_mahasiswa(nim)           → biodata lengkap + data akademik
  get_nilai_perkuliahan_mahasiswa(nim) → list nilai MK
"""
import base64
import json
import time
import logging
from threading import Lock

import requests
from django.conf import settings

logger = logging.getLogger(__name__)

_token_cache = {'token': None, 'exp': 0}
_token_lock = Lock()

BUFFER_MENIT = 5
TIMEOUT_DETIK = 90

# Error code Feeder yang artinya "kolom tidak dikenali di filter"
_FILTER_COLUMN_ERROR_CODES = {121, '121'}


def _decode_jwt_payload(token: str) -> dict:
    try:
        parts = token.split('.')
        if len(parts) != 3:
            return {}
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += '=' * padding
        return json.loads(base64.urlsafe_b64decode(payload_b64).decode('utf-8'))
    except Exception as e:
        logger.warning(f"Gagal decode JWT payload: {e}")
        return {}


def _get_feeder_url() -> str:
    host = settings.FEEDER_HOST
    if not host:
        raise ValueError("FEEDER_HOST belum dikonfigurasi di environment secrets")
    return f"http://{host}:3003/ws/live2.php"


class FeederFilterError(RuntimeError):
    """Raised ketika Feeder menolak filter karena kolom tidak dikenali (error_code 121)."""
    pass


def _call_feeder(payload: dict) -> dict:
    """
    Kirim POST ke Neo Feeder. Raise exception sesuai jenis error:
    - FeederFilterError  : error_code 121 (kolom filter tidak dikenali)
    - RuntimeError       : error logis Feeder lainnya
    - ConnectionError    : tidak bisa konek
    - TimeoutError       : timeout
    """
    url = _get_feeder_url()
    headers = {'Content-Type': 'application/json', 'Connection': 'close'}
    try:
        resp = requests.post(url, json=payload, headers=headers, timeout=TIMEOUT_DETIK)
        resp.raise_for_status()
        data = resp.json()

        if isinstance(data, dict):
            err_code = data.get('error_code')
            if err_code not in (None, 0, '0', ''):
                err_desc = data.get('error_desc', 'Tidak ada deskripsi')
                act = payload.get('act', '?')
                if err_code in _FILTER_COLUMN_ERROR_CODES:
                    raise FeederFilterError(
                        f"Filter tidak dikenali pada '{act}' (error_code={err_code}): {err_desc}"
                    )
                raise RuntimeError(
                    f"Feeder menolak '{act}' (error_code={err_code}): {err_desc}"
                )
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
            f"Server mungkin tidak bisa diakses dari jaringan ini (Replit ≠ intranet kampus)."
        )
    except (FeederFilterError, RuntimeError, ConnectionError, TimeoutError):
        raise
    except Exception as e:
        raise RuntimeError(f"Error memanggil Neo Feeder '{payload.get('act', '?')}': {e}")


def get_token() -> str:
    with _token_lock:
        now = time.time()
        if _token_cache['token'] and _token_cache['exp'] - now > BUFFER_MENIT * 60:
            return _token_cache['token']

        logger.info("Mengambil token baru dari Neo Feeder...")
        response = _call_feeder({
            "act": "GetToken",
            "username": settings.FEEDER_USERNAME,
            "password": settings.FEEDER_PASSWORD,
        })

        token = None
        if isinstance(response, dict):
            token = (
                response.get('data', {}).get('token')
                or response.get('token')
                or response.get('data')
            )
        if not token:
            raise ValueError(f"GetToken tidak mengembalikan token. Response: {response}")

        payload_data = _decode_jwt_payload(token)
        exp = payload_data.get('exp', now + 3600)
        _token_cache['token'] = token
        _token_cache['exp'] = exp
        logger.info(f"Token berhasil didapat, berlaku hingga: {exp}")
        return token


def _extract_list_from_response(response) -> list:
    """Ekstrak list dari berbagai format response Feeder."""
    if isinstance(response, list):
        return response
    if isinstance(response, dict):
        data = response.get('data', response)
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return [data]
        if data in (None, '', []):
            return []
    return []


def _find_biodata_by_nim(nim: str, token: str) -> dict:
    """
    Cari biodata mahasiswa by NIM dengan mencoba beberapa filter.
    Strategi berurutan:
      1. GetBiodataMahasiswa  filter b.nim_mahasiswa  (registrasi sebagai join table b)
      2. GetBiodataMahasiswa  filter nim_mahasiswa     (tanpa prefix tabel)
      3. GetBiodataMahasiswa  filter c.nim_mahasiswa   (alias alternatif)
      4. GetDataLengkapMahasiswaProdi filter a.nim_mahasiswa
      5. GetListRiwayatPendidikanMahasiswa filter a.nim_mahasiswa

    Return dict biodata pertama yang ditemukan, atau raise ValueError.
    """
    strategies = [
        ("GetBiodataMahasiswa",             f"b.nim_mahasiswa='{nim}'"),
        ("GetBiodataMahasiswa",             f"nim_mahasiswa='{nim}'"),
        ("GetBiodataMahasiswa",             f"c.nim_mahasiswa='{nim}'"),
        ("GetDataLengkapMahasiswaProdi",    f"a.nim_mahasiswa='{nim}'"),
        ("GetDataLengkapMahasiswaProdi",    f"b.nim_mahasiswa='{nim}'"),
        ("GetListRiwayatPendidikanMahasiswa", f"a.nim_mahasiswa='{nim}'"),
    ]

    last_err = None
    tried = []
    for act, filter_str in strategies:
        tried.append(f"{act}({filter_str})")
        try:
            response = _call_feeder({"act": act, "token": token, "filter": filter_str})
            rows = _extract_list_from_response(response)
            if rows:
                data = rows[-1]  # ambil entri terbaru
                if 'nim_mahasiswa' not in data:
                    data['nim_mahasiswa'] = nim
                logger.info(f"Biodata NIM {nim} berhasil via {act} filter={filter_str}")
                return data
            # response sukses tapi kosong — mahasiswa tidak ditemukan di prodi ini
        except FeederFilterError as e:
            last_err = e
            logger.warning(f"Filter tidak dikenali: {act} {filter_str} — coba berikutnya")
            continue
        except (ConnectionError, TimeoutError):
            raise
        except RuntimeError as e:
            last_err = e
            logger.warning(f"Error pada {act} {filter_str}: {e} — coba berikutnya")
            continue

    raise ValueError(
        f"Mahasiswa dengan NIM '{nim}' tidak ditemukan di Feeder. "
        f"Sudah dicoba: {', '.join(tried)}. "
        f"Error terakhir: {last_err}"
    )


def _find_nilai_by_nim_or_reg(nim: str, id_registrasi: str | None, token: str) -> list:
    """
    Ambil daftar nilai via GetRiwayatNilaiMahasiswa dengan strategi fallback:
      1. id_registrasi (jika tersedia) — a.id_registrasi
      2. a.nim_mahasiswa
      3. b.nim_mahasiswa
      4. nim_mahasiswa (tanpa prefix)
    """
    strategies = []

    if id_registrasi:
        strategies.append(f"a.id_registrasi='{id_registrasi}'")

    strategies += [
        f"a.nim_mahasiswa='{nim}'",
        f"b.nim_mahasiswa='{nim}'",
        f"nim_mahasiswa='{nim}'",
    ]

    last_err = None
    for filter_str in strategies:
        try:
            response = _call_feeder({
                "act": "GetRiwayatNilaiMahasiswa",
                "token": token,
                "filter": filter_str,
            })
            rows = _extract_list_from_response(response)
            logger.info(
                f"GetRiwayatNilaiMahasiswa NIM {nim} via filter={filter_str}: "
                f"{len(rows)} baris"
            )
            return rows  # bisa kosong (mahasiswa belum ada nilai)
        except FeederFilterError as e:
            last_err = e
            logger.warning(f"Filter tidak dikenali: GetRiwayatNilaiMahasiswa {filter_str}")
            continue
        except (ConnectionError, TimeoutError):
            raise
        except RuntimeError as e:
            last_err = e
            logger.warning(f"Error GetRiwayatNilaiMahasiswa {filter_str}: {e}")
            continue

    logger.warning(
        f"Semua filter GetRiwayatNilaiMahasiswa gagal untuk NIM {nim}. "
        f"Error terakhir: {last_err}. Return list kosong."
    )
    return []


# ─── Public API ────────────────────────────────────────────────────────────────

def get_biodata_mahasiswa(nim: str) -> dict:
    """
    Ambil biodata + data akademik mahasiswa berdasarkan NIM.
    Mencoba berbagai kombinasi filter/endpoint secara otomatis.
    """
    token = get_token()
    return _find_biodata_by_nim(nim, token)


def get_program_mahasiswa(nim: str) -> dict:
    """Alias get_biodata_mahasiswa untuk kompatibilitas internal."""
    return get_biodata_mahasiswa(nim)


def get_nilai_perkuliahan_mahasiswa(nim: str) -> list:
    """
    Ambil riwayat nilai mahasiswa berdasarkan NIM.
    Gunakan id_registrasi dari biodata jika tersedia.
    """
    token = get_token()

    id_registrasi = None
    try:
        biodata = _find_biodata_by_nim(nim, token)
        id_registrasi = biodata.get('id_registrasi') or biodata.get('id_reg')
    except Exception as e:
        logger.warning(f"Tidak bisa ambil id_registrasi untuk {nim}: {e}")

    return _find_nilai_by_nim_or_reg(nim, id_registrasi, token)


def clear_token_cache():
    with _token_lock:
        _token_cache['token'] = None
        _token_cache['exp'] = 0


# ─── Connection check (untuk dashboard) ───────────────────────────────────────

_conn_cache = {'ok': None, 'msg': '', 'checked_at': 0}
_conn_lock = Lock()
CONN_CACHE_TTL = 60


def check_connection() -> tuple[bool, str]:
    with _conn_lock:
        now = time.time()
        if _conn_cache['ok'] is not None and now - _conn_cache['checked_at'] < CONN_CACHE_TTL:
            return _conn_cache['ok'], _conn_cache['msg']

    host = getattr(settings, 'FEEDER_HOST', '')
    username = getattr(settings, 'FEEDER_USERNAME', '')
    password = getattr(settings, 'FEEDER_PASSWORD', '')
    if not (host and username and password):
        result = (False, 'Konfigurasi FEEDER_HOST / USERNAME / PASSWORD belum lengkap.')
        _update_conn_cache(*result)
        return result

    url = f"http://{host}:3003/ws/live2.php"
    try:
        resp = requests.post(
            url,
            json={"act": "GetToken", "username": username, "password": password},
            headers={'Content-Type': 'application/json', 'Connection': 'close'},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()
        token = (
            data.get('data', {}).get('token') or data.get('token') or data.get('data')
        ) if isinstance(data, dict) else None

        if token:
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
