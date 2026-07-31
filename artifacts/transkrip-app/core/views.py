"""
Views utama aplikasi transkrip.
Semua view memerlukan login (login_required).
"""
import io
import logging
import time
from datetime import date

from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse
from django.shortcuts import render, redirect
from django.contrib import messages
from django.conf import settings
from django.utils import timezone

from .forms import GenerateSingleForm, GenerateBatchForm, RiwayatFilterForm
from .models import (
    Kurikulum, MataKuliahKurikulum, Template, PredikatKelulusan,
    DataPendukung, RiwayatGenerate
)
from .services import feeder as feeder_service
from .services.matching import cocokan_nilai, parse_biodata
from .services.excel_gen import generate_single_excel, generate_batch_excel

logger = logging.getLogger(__name__)


def _parse_nim_from_file(f) -> list[str]:
    """Parse daftar NIM dari file .txt/.csv/.xlsx."""
    name = f.name.lower()
    nims = []

    if name.endswith('.xlsx'):
        import openpyxl
        wb = openpyxl.load_workbook(f, read_only=True, data_only=True)
        ws = wb.active
        for row in ws.iter_rows(values_only=True):
            if row and row[0]:
                nim = str(row[0]).strip()
                if nim and nim.lower() not in ('nim', 'no', '#'):
                    nims.append(nim)
    elif name.endswith('.csv'):
        import csv
        import io as io_mod
        content = f.read().decode('utf-8', errors='replace')
        reader = csv.reader(io_mod.StringIO(content))
        for row in reader:
            if row and row[0]:
                nim = str(row[0]).strip()
                if nim and nim.lower() not in ('nim', 'no', '#'):
                    nims.append(nim)
    else:  # .txt
        content = f.read().decode('utf-8', errors='replace')
        for line in content.splitlines():
            nim = line.strip()
            if nim and not nim.startswith('#'):
                nims.append(nim)

    # Deduplikasi sambil mempertahankan urutan
    seen = set()
    result = []
    for nim in nims:
        if nim not in seen:
            seen.add(nim)
            result.append(nim)
    return result


def _proses_satu_nim(nim: str, mode: str, request_user) -> tuple:
    """
    Proses satu NIM: fetch data, cocokan, return (HasilPencocokan, data_pendukung, template, cm).
    Simpan ke RiwayatGenerate.
    Raise exception jika gagal.
    """
    # 1. Ambil biodata dari Feeder
    biodata_raw = feeder_service.get_biodata_mahasiswa(nim)
    biodata = parse_biodata(biodata_raw)

    jenjang = biodata.get('jenjang', '').upper()
    angkatan = biodata.get('angkatan', 0)

    # Normalisasi jenjang (dari Feeder bisa jadi kode angka atau nama lengkap)
    if jenjang in ('1', 'D1'):
        jenjang = 'D1'
    elif jenjang in ('3', 'D3', 'DIPLOMA TIGA', 'DIPLOMA 3'):
        jenjang = 'D3'
    elif jenjang in ('4', 'S1', 'STRATA SATU', 'SARJANA', 'SARJANA TERAPAN'):
        jenjang = 'S1'

    # 2. Cari Kurikulum
    try:
        kurikulum = Kurikulum.objects.select_related('template').get(
            jenjang=jenjang, angkatan=angkatan
        )
    except Kurikulum.DoesNotExist:
        raise ValueError(
            f"Kurikulum untuk jenjang '{jenjang}' angkatan {angkatan} belum ada. "
            f"Silakan tambahkan di Admin → Kurikulum."
        )

    if not kurikulum.template:
        raise ValueError(
            f"Kurikulum {jenjang} {angkatan} belum memiliki Template Excel. "
            f"Set template terlebih dahulu di Admin → Kurikulum."
        )

    template = kurikulum.template
    mk_kurikulum = list(kurikulum.matakuliah.all().order_by('urutan_alfabet'))

    # 3. Ambil nilai dari Feeder
    daftar_nilai = feeder_service.get_nilai_perkuliahan_mahasiswa(nim)

    # 4. Predikat kelulusan
    predikat_qs = PredikatKelulusan.objects.all()

    # 5. Data pendukung
    try:
        data_pendukung = DataPendukung.objects.get(nim=nim)
    except DataPendukung.DoesNotExist:
        data_pendukung = None

    # 6. Cocokan
    hasil = cocokan_nilai(
        daftar_mk_kurikulum=mk_kurikulum,
        daftar_nilai_api=daftar_nilai,
        biodata_parsed=biodata,
        predikat_qs=predikat_qs,
        data_pendukung=data_pendukung,
        default_nama_dekan=getattr(settings, 'DEFAULT_NAMA_DEKAN', ''),
    )

    # 7. Simpan riwayat
    RiwayatGenerate.objects.create(
        nim=nim,
        nama_mahasiswa=hasil.nama,
        jenjang=hasil.jenjang,
        angkatan=hasil.angkatan,
        mode=mode,
        jumlah_mk_retake=hasil.jumlah_mk_retake,
        jumlah_mk_kosong=hasil.jumlah_mk_kosong,
        jumlah_mk_tidak_dikenali=hasil.jumlah_mk_tidak_dikenali,
        status='success',
        generated_by=request_user,
    )

    return hasil, data_pendukung, template


@login_required
def dashboard(request):
    """Halaman beranda / dashboard."""
    total_kurikulum = Kurikulum.objects.count()
    total_template = Template.objects.count()
    total_riwayat = RiwayatGenerate.objects.count()
    riwayat_terakhir = RiwayatGenerate.objects.select_related('generated_by').order_by('-generated_at')[:10]

    # Cek koneksi nyata ke Feeder (dengan cache 60 detik)
    feeder_ok, feeder_msg = feeder_service.check_connection()

    context = {
        'total_kurikulum': total_kurikulum,
        'total_template': total_template,
        'total_riwayat': total_riwayat,
        'riwayat_terakhir': riwayat_terakhir,
        'feeder_ok': feeder_ok,
        'feeder_msg': feeder_msg,
    }
    return render(request, 'dashboard.html', context)


@login_required
def generate_single(request):
    """Generate transkrip untuk satu NIM."""
    form = GenerateSingleForm()

    if request.method == 'POST':
        form = GenerateSingleForm(request.POST)
        if form.is_valid():
            nim = form.cleaned_data['nim'].strip()
            try:
                hasil, data_pendukung, template = _proses_satu_nim(nim, 'single', request.user)

                # Generate Excel
                template_path = template.file_excel.path
                excel_bytes = generate_single_excel(
                    hasil=hasil,
                    template_file_path=template_path,
                    cell_mapping=template.cell_mapping,
                    data_pendukung=data_pendukung,
                    default_nama_dekan=getattr(settings, 'DEFAULT_NAMA_DEKAN', ''),
                )

                filename = f"Transkrip_{nim}_{hasil.nama.replace(' ', '_')}.xlsx"
                response = HttpResponse(
                    excel_bytes,
                    content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                )
                response['Content-Disposition'] = f'attachment; filename="{filename}"'
                return response

            except Exception as e:
                logger.exception(f"Error generate single NIM {nim}: {e}")
                # Simpan riwayat error
                try:
                    RiwayatGenerate.objects.create(
                        nim=nim,
                        nama_mahasiswa='',
                        mode='single',
                        status='error',
                        error_message=str(e),
                        generated_by=request.user,
                    )
                except Exception:
                    pass
                messages.error(request, f"Gagal generate transkrip: {e}")

    return render(request, 'generate_single.html', {'form': form})


@login_required
def generate_batch(request):
    """Generate transkrip batch dari file daftar NIM."""
    form = GenerateBatchForm()
    hasil_batch = []

    if request.method == 'POST':
        form = GenerateBatchForm(request.POST, request.FILES)
        if form.is_valid():
            f = request.FILES['file_nim']
            try:
                daftar_nim = _parse_nim_from_file(f)
            except Exception as e:
                messages.error(request, f"Gagal membaca file: {e}")
                return render(request, 'generate_batch.html', {'form': form, 'hasil_batch': []})

            if not daftar_nim:
                messages.warning(request, "File tidak mengandung NIM yang valid.")
                return render(request, 'generate_batch.html', {'form': form, 'hasil_batch': []})

            if len(daftar_nim) > 100:
                messages.warning(
                    request,
                    f"File mengandung {len(daftar_nim)} NIM. Direkomendasikan maks 100 NIM per run. "
                    f"Hanya 100 NIM pertama yang akan diproses."
                )
                daftar_nim = daftar_nim[:100]

            daftar_hasil_excel = []  # untuk generate_batch_excel
            for nim in daftar_nim:
                try:
                    hasil, data_pendukung, template = _proses_satu_nim(nim, 'batch', request.user)
                    daftar_hasil_excel.append((
                        hasil, data_pendukung,
                        template.file_excel.path,
                        template.cell_mapping,
                    ))
                    hasil_batch.append({
                        'nim': nim,
                        'nama': hasil.nama,
                        'status': 'success',
                        'mk_kosong': hasil.jumlah_mk_kosong,
                        'mk_retake': hasil.jumlah_mk_retake,
                        'mk_tidak_dikenali': hasil.jumlah_mk_tidak_dikenali,
                        'ipk': str(hasil.ipk),
                    })
                except Exception as e:
                    logger.exception(f"Error batch NIM {nim}: {e}")
                    try:
                        RiwayatGenerate.objects.create(
                            nim=nim,
                            mode='batch',
                            status='error',
                            error_message=str(e),
                            generated_by=request.user,
                        )
                    except Exception:
                        pass
                    hasil_batch.append({
                        'nim': nim,
                        'nama': '-',
                        'status': 'error',
                        'error': str(e),
                    })

                # Jeda kecil antar NIM agar tidak membebani Feeder
                time.sleep(0.3)

            if daftar_hasil_excel:
                try:
                    excel_bytes = generate_batch_excel(
                        daftar_hasil=daftar_hasil_excel,
                        default_nama_dekan=getattr(settings, 'DEFAULT_NAMA_DEKAN', ''),
                    )
                    berhasil = sum(1 for h in hasil_batch if h['status'] == 'success')
                    gagal = len(hasil_batch) - berhasil
                    filename = f"Transkrip_Batch_{date.today().strftime('%Y%m%d')}.xlsx"
                    response = HttpResponse(
                        excel_bytes,
                        content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
                    )
                    response['Content-Disposition'] = f'attachment; filename="{filename}"'
                    return response
                except Exception as e:
                    logger.exception(f"Error generate batch Excel: {e}")
                    messages.error(request, f"Gagal membuat file Excel: {e}")
            else:
                messages.error(request, "Semua NIM gagal diproses. Cek log untuk detail.")

    return render(request, 'generate_batch.html', {'form': form, 'hasil_batch': hasil_batch})


@login_required
def riwayat(request):
    """Halaman daftar riwayat generate."""
    form = RiwayatFilterForm(request.GET or None)
    qs = RiwayatGenerate.objects.select_related('generated_by').order_by('-generated_at')

    if form.is_valid():
        if form.cleaned_data.get('tanggal_mulai'):
            qs = qs.filter(generated_at__date__gte=form.cleaned_data['tanggal_mulai'])
        if form.cleaned_data.get('tanggal_selesai'):
            qs = qs.filter(generated_at__date__lte=form.cleaned_data['tanggal_selesai'])
        if form.cleaned_data.get('angkatan'):
            qs = qs.filter(angkatan=form.cleaned_data['angkatan'])
        if form.cleaned_data.get('status'):
            qs = qs.filter(status=form.cleaned_data['status'])
        if form.cleaned_data.get('jenjang'):
            qs = qs.filter(jenjang=form.cleaned_data['jenjang'])

    # Statistik ringkasan
    total = qs.count()
    berhasil = qs.filter(status='success').count()
    error = qs.filter(status='error').count()

    context = {
        'form': form,
        'riwayat_list': qs[:200],
        'total': total,
        'berhasil': berhasil,
        'error': error,
    }
    return render(request, 'riwayat.html', context)
