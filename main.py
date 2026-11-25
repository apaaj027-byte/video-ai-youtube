"""Cerita Bersambung: HOROR, MISTERI, KOMEDI (animasi kartun, tanpa GPU).

Alur: topics.csv (seri & part) -> naskah per adegan (Gemini, nyambung dengan part sebelumnya lewat state.json)
      -> suara (edge-tts, kata demi kata) -> animasi (anim.py) -> video (FFmpeg) -> upload YouTube.

Uji tanpa API key / tanpa internet:  python main.py --uji
"""
import asyncio
import csv
import json
import os
import re
import zlib
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import anim
from anim import EFEK, EMOSI, LATAR, POSE, SHOT, render_kartu_akhir, render_segmen

UJI = "--uji" in sys.argv
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")
GEMINI_MODELS = [m.strip() for m in os.environ.get(
    "GEMINI_MODEL", "gemini-3.5-flash,gemini-3.1-flash-lite,gemini-2.5-flash-lite").split(",") if m.strip()]
VOICE = os.environ.get("TTS_VOICE", "id-ID-ArdiNeural")           # suara narator (pria)
VOICE_WANITA = os.environ.get("TTS_VOICE_WANITA", "id-ID-GadisNeural")  # dipakai bila tokoh wanita yang bicara
MENANG = os.environ.get("MENANG", "").strip()                      # hasil voting penonton: "1" atau "2" (opsional)
PRIVACY = os.environ.get("YT_PRIVACY", "private")                 # private / unlisted / public
UPLOAD = os.environ.get("UPLOAD", "1") == "1" and not UJI
SIMPAN = os.environ.get("SIMPAN_KEMAJUAN", "0") == "1" and not UJI   # maju ke part berikutnya walau tanpa upload
WORK = Path("work")
WORK.mkdir(exist_ok=True)
TOPIK_CSV, STATE_JSON = Path("topics.csv"), Path("state.json")
KOLOM = ["seri", "genre", "part", "total", "premis", "arah", "status"]
USIA = ("anak", "dewasa", "tua")
GENDER, KULIT, BAJU = ("pria", "wanita"), ("sawo", "terang", "gelap"), tuple(anim.BAJU)


# ================================================================ genre
GENRE = {
    "horor": dict(
        gaya="cerita horor bersambung. Gaya bercerita pelan, dingin, jujur, seperti pengakuan",
        rate=-6, pitch=-6, hashtag="#horor #ceritahoror #bersambung #shorts #animasi",
        tag=["horor", "cerita horor", "horor indonesia"], kunci="seram lewat suasana dan implikasi",
        akhir="Ikuti untuk seri horor berikutnya"),
    "misteri": dict(
        gaya="cerita misteri bersambung (teka-teki, petunjuk, kejanggalan yang bisa dipecahkan). Gaya bercerita tegang, rasional, "
             "seperti detektif amatir yang menyusun petunjuk",
        rate=-3, pitch=-3, hashtag="#misteri #teka_teki #bersambung #shorts #animasi",
        tag=["misteri", "cerita misteri", "teka teki"], kunci="penasaran lewat petunjuk yang saling bertentangan",
        akhir="Ikuti untuk misteri berikutnya"),
    "komedi": dict(
        gaya="cerita komedi bersambung (situasi absurd yang makin kacau). Gaya bercerita cepat, jenaka, banyak dialog antar tokoh, "
             "ada punchline di akhir tiap adegan penting",
        rate=7, pitch=3, hashtag="#komedi #lucu #bersambung #shorts #animasi",
        tag=["komedi", "cerita lucu", "animasi lucu"], kunci="lucu lewat situasi dan dialog, bukan ejekan fisik atau SARA",
        akhir="Ikuti biar nggak ketinggalan part lucunya"),
    "drama": dict(
        gaya="cerita drama keluarga/kehidupan bersambung. Gaya bercerita pelan, hangat, emosional, jujur seperti curhat kepada teman",
        rate=-4, pitch=-2, hashtag="#drama #ceritakeluarga #bersambung #shorts #animasi",
        tag=["drama", "cerita keluarga", "kisah nyata rasa"], kunci="emosi lewat konflik dan rahasia keluarga, tanpa kekerasan",
        akhir="Ikuti biar tahu kelanjutan kisahnya"),
    "thriller": dict(
        gaya="cerita thriller bertahan hidup bersambung (waktu terbatas, tekanan tinggi, tanpa hantu). Gaya bercerita cepat, tegang, kalimat pendek",
        rate=3, pitch=-2, hashtag="#thriller #tegang #bersambung #shorts #animasi",
        tag=["thriller", "cerita tegang", "bertahan hidup"], kunci="tegang lewat waktu yang menipis dan kecurigaan antar tokoh, tanpa gore",
        akhir="Ikuti sebelum waktunya habis"),
    "legenda": dict(
        gaya="cerita legenda/mitos Nusantara versi ringan dengan twist modern. Gaya bercerita tenang seperti dongeng malam hari, sedikit misterius",
        rate=-5, pitch=-4, hashtag="#legenda #mitos #nusantara #bersambung #shorts #animasi",
        tag=["legenda", "mitos nusantara", "cerita rakyat"], kunci="hormat pada adat dan budaya, tanpa menjelekkan agama atau suku",
        akhir="Ikuti untuk legenda berikutnya"),
    "fiksi": dict(
        gaya="cerita fiksi ilmiah ringan bersambung (putaran waktu, dunia yang berubah, teknologi aneh). Gaya bercerita cerdas, penasaran, tenang tapi menggigit",
        rate=0, pitch=-2, hashtag="#fiksiilmiah #sciencefiction #bersambung #shorts #animasi",
        tag=["fiksi ilmiah", "sci-fi", "putaran waktu"], kunci="penasaran lewat aturan dunia yang aneh dan perlahan terungkap",
        akhir="Ikuti untuk bagian berikutnya"),
}

TANPA_SERAM = ("komedi", "drama", "thriller", "fiksi")   # emosi 'seram' (mata menyala) hanya untuk horor/misteri/legenda
TANPA_CCTV = ("komedi", "drama", "legenda")


def genre_row(row):
    g = (row.get("genre") or "horor").strip().lower()
    return g if g in GENRE else "horor"


def parse_hz(x):
    return int(re.sub(r"[^0-9+-]", "", str(x)) or 0)


PROFIL = {  # (voice-gender, usia): (selisih nada Hz dari nada narator, selisih kecepatan %)
    ("pria", "anak"): (24, 8), ("pria", "dewasa"): (9, 3), ("pria", "tua"): (-8, -14),
    ("wanita", "anak"): (22, 8), ("wanita", "dewasa"): (4, 2), ("wanita", "tua"): (-12, -16),
}


def atur_suara(row, nama, narator, gender, usia=None):
    """(voice, rate, pitch) per pembicara.
    Narator = suara bercerita (nada genre). Tokoh lain = suara sendiri sesuai gender dan usia (nenek, kakek, anak, dst)."""
    g = GENRE[genre_row(row)]
    usia = usia or {}
    rate0 = parse_hz(os.environ.get("TTS_RATE") or g["rate"])
    base = parse_hz(os.environ.get("TTS_PITCH") or g["pitch"])
    voice_pria = lambda gd: VOICE_WANITA if gd == "wanita" else VOICE
    if not nama or nama == narator:                        # penceritaan: suara narator
        return voice_pria(gender.get(narator)), f"{rate0:+d}%", f"{base:+d}Hz"
    gd, u = gender.get(nama, "pria"), usia.get(nama, "dewasa")
    dp, dr = PROFIL.get((gd, u), (6, 0))
    var = (zlib.crc32(nama.encode()) % 5 - 2) * 2          # -4..+4 Hz: tokoh sebaya tetap terdengar beda
    # dialog dibacakan dengan nada dasar netral (tidak ikut nada seram narator) supaya beda dari narasi
    pitch = (base if gd == gender.get(narator) else 0) + dp + var
    return voice_pria(gd), f"{rate0 + dr:+d}%", f"{pitch:+d}Hz"


# ================================================================ topik & state
def baca_topik():
    with open(TOPIK_CSV, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    premis, genre = {}, {}
    for r in rows:  # premis cukup ditulis di part 1; part berikutnya mewarisinya
        r["seri"] = (r.get("seri") or "").strip()
        if (r.get("premis") or "").strip():
            premis[r["seri"]] = r["premis"].strip()
        r["premis"] = premis.get(r["seri"], "")
        if (r.get("genre") or "").strip():
            genre[r["seri"]] = r["genre"].strip().lower()
        r["genre"] = genre.get(r["seri"], "horor")
    return rows


def pilih_topik(rows):
    for r in rows:
        if r["seri"] and (r.get("status") or "").strip() != "selesai":
            return r
    sys.exit("Semua part sudah selesai. Tambahkan seri baru di topics.csv")


def simpan_topik(rows):
    with open(TOPIK_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=KOLOM)
        w.writeheader()
        asli = {}
        for r in rows:  # tulis premis hanya di part pertama tiap seri
            pertama = r["seri"] not in asli
            asli[r["seri"]] = True
            w.writerow({k: ((r.get(k) or "") if (pertama or k != "premis") else "") for k in KOLOM})


def muat_state():
    if STATE_JSON.exists():
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    return {}


def simpan_state(st):
    STATE_JSON.write_text(json.dumps(st, ensure_ascii=False, indent=2), encoding="utf-8")


# ================================================================ naskah
FORMAT_JSON = """{
  "judul": "judul pendek mencekam tanpa kata 'Part' (maks 60 karakter)",
  "deskripsi": "2 kalimat teaser tanpa spoiler",
  "tag": ["horor", "ceritahoror", "..."],
  "narator": "nama tokoh 'aku' (isi di part 1, sama di part berikutnya)",
  "tokoh": [{"nama": "Bima", "gender": "pria", "usia": "dewasa", "rambut": "pendek", "kulit": "sawo", "baju": "abu", "kacamata": false}],
  "bagian": [
    {"narasi": "satu kalimat pendek", "latar": "kamar_malam", "shot": "lebar",
     "tokoh": [{"nama": "Bima", "pose": "berdiri", "emosi": "takut"}], "bicara": "Bima", "efek": ["getar"]},
    {"narasi": "...", "latar": "lorong", "shot": "cctv", "tokoh": [], "cctv": {"jam": "03:00:41", "kecepatan": 1},
     "efek": ["glitch"]}
  ],
  "ringkasan": "3-4 kalimat: apa yang terjadi di part ini (untuk kesinambungan part berikutnya)",
  "cliffhanger": "1 kalimat: kejadian terakhir yang menggantung",
  "kartu_akhir": {"waktu": "03:00:00", "teks": "JANGAN BUKA PINTU JAM 03.00"},
  "pilihan": ["arah A untuk part berikutnya (maks 4 kata)", "arah B untuk part berikutnya (maks 4 kata)"]
}"""

CONTOH = [  # contoh irama kalimat (bukan untuk disalin isinya)
    "Jangan pernah buka pintu kalau ada yang mengetuk jam tiga pagi.", "Aku Bima. Ini istriku, Laras.",
    "Kami pindah ke rumah kontrakan yang murah.", "Terlalu murah, sebenarnya.",
    "Malam pertama, pintu depan diketuk. Satu kali.", "Malam kedua, dua kali.", "Malam ketujuh, tujuh kali.",
    "Jadi kupasang kamera kecil di ruang tamu.", "Tidak ada siapa pun di depan pintu.",
    "Tapi pintunya bergetar dari dalam.", "Rambut panjang. Gaun putih. Itu Laras.",
    "Padahal Laras sedang tidur di sampingku."]


def alur_genre(genre, part, total):
    hook1 = {
        "horor": 'satu larangan atau fakta janggal yang spesifik, mengandung jam atau angka. Contoh bentuk: "Jangan pernah memutar rekaman CCTV jam dua lewat tiga belas."',
        "misteri": 'satu fakta aneh yang bisa diperiksa, mengandung jam, angka, atau tempat. Contoh bentuk: "Setiap hari jam dua lewat tujuh, semua jam di kota ini berhenti."',
        "komedi": 'satu situasi absurd yang langsung bikin penasaran. Contoh bentuk: "Hari pertama kerja, bosku ternyata mengunci pintu kantor dari luar."',
        "drama": 'satu rahasia atau kalimat yang mengguncang. Contoh bentuk: "Surat wasiat kedua ayahku disembunyikan di dalam radio tua."',
        "thriller": 'satu situasi terjebak dengan hitungan waktu. Contoh bentuk: "Oksigen di lift ini tinggal enam puluh menit."',
        "legenda": 'satu pantangan adat yang spesifik dengan jam atau tempat. Contoh bentuk: "Jangan menimba air sumur itu setelah magrib."',
        "fiksi": 'satu fakta mustahil yang spesifik dengan jam atau angka. Contoh bentuk: "Setiap pagi jam enam lewat tiga belas, hari Senin ini mulai lagi."',
    }[genre]
    hookn = "kalimat yang langsung menyambung cliffhanger part sebelumnya (ingatkan apa yang baru terjadi), tetap mengandung jam, angka, atau hal spesifik."
    ujung = {
        "horor": ("TWIST + CLIFFHANGER: kalimat terakhir diawali 'Padahal...' atau pertanyaan yang mengubah arti semua tadi. Kalimat terakhir maks 9 kata.",
                  "JAWABAN + TWIST AKHIR: ungkap penyebabnya, beri satu putaran terakhir yang membuat bulu kuduk berdiri, tutup dengan kalimat pendek yang menggantung."),
        "misteri": ("TWIST + CLIFFHANGER: temuan terakhir membalik dugaan tadi, tutup dengan pertanyaan 'Kalau begitu, siapa...?' atau sejenisnya. Maks 10 kata.",
                    "PENGUNGKAPAN: semua petunjuk tersambung, ungkap pelaku/penyebabnya, beri satu putaran terakhir yang tidak terduga tapi masuk akal."),
        "komedi": ("PUNCHLINE + CLIFFHANGER: kekacauan memuncak lalu muncul kejutan konyol yang membuat part berikutnya wajib ditonton. Maks 10 kata.",
                   "PUNCHLINE AKHIR: semua kekacauan terselesaikan dengan cara yang tak terduga dan lucu, tutup dengan satu kalimat jenaka yang diingat orang."),
        "drama": ("PENGUNGKAPAN SETENGAH + CLIFFHANGER: satu rahasia terbuka tapi memunculkan rahasia yang lebih besar. Tutup dengan kalimat emosional maks 10 kata.",
                  "PENGUNGKAPAN + TWIST EMOSIONAL: kebenaran terungkap, alasan tokoh yang tampak jahat ternyata masuk akal, tutup dengan kalimat hangat yang menyentuh."),
        "thriller": ("TWIST + CLIFFHANGER: waktu hampir habis dan ada temuan yang mengubah siapa yang dicurigai. Kalimat terakhir maks 9 kata.",
                     "PENGUNGKAPAN + TWIST AKHIR: dalang di balik jebakan terungkap, satu kejutan terakhir yang masuk akal, tutup dengan kalimat pendek yang menggantung."),
        "legenda": ("CLIFFHANGER ADAT: pantangan dilanggar dan terjadi tanda aneh pertama yang menakutkan. Maks 10 kata.",
                    "ASAL-USUL + TWIST: ungkap asal-usul pantangan itu, beri satu putaran yang menghubungkan masa lalu dengan tokoh masa kini, tutup dengan kalimat tenang yang membekas."),
        "fiksi": ("TWIST ATURAN DUNIA + CLIFFHANGER: satu aturan dunia terungkap tapi membuat segalanya makin janggal. Maks 10 kata.",
                  "JAWABAN + TWIST AKHIR: ungkap penyebab fenomena itu, beri satu putaran yang mengubah siapa tokoh sebenarnya, tutup dengan kalimat singkat yang menggantung."),
    }[genre][0 if part < total else 1]
    tengah = {
        "horor": ("NORMAL TAPI JANGGAL: 2-3 adegan keseharian dengan satu detail kecil yang terasa salah.",
                  "POLA BERULANG: kejanggalan terjadi lagi, makin jelas (jam yang sama, hitungan yang bertambah).",
                  "BUKTI: tokoh memeriksa (rekaman, pesan, foto). Kalimat jadi sangat pendek (3-6 kata)."),
        "misteri": ("PETUNJUK PERTAMA: 2-3 adegan tokoh menyelidiki dan menemukan satu kejanggalan yang jelas.",
                    "PETUNJUK KEDUA YANG BERTENTANGAN: saksi atau benda yang membuat dugaan pertama diragukan.",
                    "BUKTI: temuan kunci (rekaman, catatan, foto). Kalimat jadi sangat pendek (3-6 kata)."),
        "komedi": ("KEKACAUAN MULAI: 2-3 adegan situasi normal yang mulai salah, dengan dialog cepat antar tokoh.",
                   "KEKACAUAN MEMBESAR: aturan tiga (kejadian ke-1 lucu, ke-2 lebih lucu, ke-3 paling absurd), tokoh saling menyahut.",
                   "REAKSI: tokoh utama menyadari betapa parahnya; kalimat pendek, gaya deadpan."),
        "drama": ("SITUASI NORMAL TAPI GANJIL: 2-3 adegan keseharian keluarga dengan satu detail yang mengusik.",
                  "KONFLIK MEMUNCAK: tokoh berdebat atau menyimpan rahasia; dialog saling menyela, nada naik.",
                  "BUKTI/PENGAKUAN: benda, surat, atau saksi yang mengubah pandangan tokoh. Kalimat jadi pendek dan berat."),
        "thriller": ("SITUASI TERJEBAK: 2-3 adegan tokoh sadar mereka terkurung dan menghitung waktu/sumber daya.",
                     "KECURIGAAN: tokoh saling menuduh, ada detail yang tidak cocok dari cerita masing-masing.",
                     "BUKTI: temuan kunci yang mempersempit tersangka. Kalimat sangat pendek (3-6 kata), sebut sisa waktu."),
        "legenda": ("PANTANGAN DIKENALKAN: 2-3 adegan tokoh dari luar mendengar adat dan menertawakannya.",
                    "PANTANGAN DILANGGAR: tokoh melanggar dan tanda aneh muncul, makin jelas tiap adegan.",
                    "SESEPUH BERCERITA: orang tua desa menjelaskan sebagian asal-usulnya. Kalimat tenang, penuh jeda."),
        "fiksi": ("KEJANGGALAN DUNIA: 2-3 adegan keseharian dengan satu hal yang tidak masuk akal secara fisik.",
                  "POLA & ATURAN: tokoh mencatat pola, menemukan aturan tersembunyi, bertemu orang lain yang sadar.",
                  "BUKTI: catatan, rekaman, atau benda yang memastikan dugaan. Kalimat jadi sangat pendek (3-6 kata)."),
    }[genre]
    return (f"1. HOOK (adegan 1, maks 6 detik): {hook1 if part == 1 else hookn} Jangan mulai dengan \"Tahukah kamu\".\n"
            f"2. PERKENALAN: {"singkat saja, 'Aku [Nama].' lalu tokoh lain." if part == 1 else 'tidak perlu memperkenalkan lagi; sambungkan langsung dari cliffhanger part sebelumnya dalam 1 kalimat.'}\n"
            f"3. {tengah[0]}\n4. {tengah[1]}\n5. {tengah[2]}\n6. {ujung}")


def buat_prompt(row, st):
    part, total = int(row["part"]), int(row["total"])
    genre = genre_row(row)
    g = GENRE[genre]
    s = st.get(row["seri"], {})
    arah = (row.get("arah") or "").strip() or "bebas; lanjutkan secara logis dari cliffhanger sebelumnya dan naikkan taruhannya"
    p = f"""Kamu penulis naskah {g['gaya']}, berbahasa Indonesia, untuk video pendek animasi kartun
(YouTube Shorts / Reels / TikTok, vertikal 9:16, durasi 60-80 detik). Sudut pandang orang pertama ("aku").
Seri: "{row['seri']}" (genre {genre}). Ini PART {part} dari {total}.

PREMIS SERI: {row['premis']}
ARAH CERITA PART INI: {arah}
"""
    if s.get("ringkasan"):
        p += "\nRINGKASAN PART SEBELUMNYA:\n" + "\n".join(s["ringkasan"]) + "\n"
        p += f"CLIFFHANGER TERAKHIR: {s.get('cliffhanger', '')}\n"
        p += f"NARATOR: {s.get('narator', '')}. TOKOH YANG SUDAH ADA (pakai persis, jangan diubah): " \
             f"{json.dumps(s.get('tokoh', []), ensure_ascii=False)}\n"
    dialog = {"horor": "2-3 adegan boleh berupa ucapan tokoh lain (dalam tanda kutip, isi \"bicara\" dengan nama pembicara).",
              "misteri": "4-6 adegan berupa dialog singkat antar tokoh (saksi, rekan, tokoh utama); isi \"bicara\" dengan nama pembicara.",
              "komedi": "6-9 adegan berupa dialog cepat antar tokoh dengan punchline; ganti pembicara tiap 1-2 adegan, isi \"bicara\" dengan nama pembicara.",
              "drama": "5-8 adegan berupa dialog emosional antar tokoh (bergantian pembicara); isi \"bicara\" dengan nama pembicara.",
              "thriller": "5-7 adegan berupa dialog pendek dan tegang antar tokoh yang saling curiga; isi \"bicara\" dengan nama pembicara.",
              "legenda": "3-5 adegan berupa ucapan sesepuh atau tokoh desa (tokoh tua usia \"tua\"); isi \"bicara\" dengan nama pembicara.",
              "fiksi": "4-6 adegan berupa dialog singkat dan cerdas antar tokoh; isi \"bicara\" dengan nama pembicara."}[genre]
    p += f"""
ALUR KHAS (wajib diikuti urutannya):
{alur_genre(genre, part, total)}

ATURAN NARASI:
- Total narasi 140-180 kata, 14-18 adegan. Satu adegan = satu kalimat (maks 14 kata). Irama seperti ini: {' / '.join(CONTOH[:6])}
- DIALOG: {dialog} Suara tiap tokoh dibedakan otomatis berdasarkan gender dan usia (nenek, kakek, anak, pria, wanita), jadi pembicara harus jelas.
- PEMBAGIAN SUARA: adegan penceritaan = kalimat narator (isi "bicara" dengan narator bila ia tampil di layar, kosongkan bila tidak). Adegan dialog = HANYA kata-kata tokoh itu sendiri, tanpa "katanya"/"ujar", tanpa tanda kutip, dan "bicara" wajib diisi nama pembicara. Tokoh tua ("nenek", "kakek", "Bu", "Pak" sepuh) wajib usia "tua"; anak kecil usia "anak".
- Campur penceritaan dan dialog secara bergantian, jangan semua dialog atau semua penceritaan.
- Tulis jam dan angka dengan huruf agar dibacakan benar ("jam dua lewat tiga belas", bukan "02.13").
- Tanpa gore, tanpa darah, tanpa kekerasan eksplisit, tanpa bunuh diri, tanpa tokoh/merek nyata, tanpa SARA. {g['kunci'].capitalize()}.
- Konsisten: nama, jam kunci, aturan misterinya tidak berubah antar part.
- "pilihan": {'dua arah berbeda untuk part berikutnya yang akan dipilih penonton lewat komentar (maks 4 kata, tanpa spoiler berat)' if part < total else 'kosongkan ([])'}.

ATURAN VISUAL (tiap adegan pilih dari daftar ini saja):
- latar: {', '.join(LATAR)} (kantor = ruang kerja dengan meja; banjir = jalan kampung tergenang air; pilih yang cocok dengan ceritanya)
- shot: lebar (tokoh kecil, lihat ruangan), sedang (tokoh agak dekat), closeup (wajah penuh; untuk reaksi/ketakutan/punchline),
  ranjang (zoom ke tempat tidur; hanya di kamar_malam/kamar_pagi), cctv (tampilan kamera pengawas hijau, wajib ada
  "cctv": {{"jam": "HH:MM:SS", "kecepatan": 1 atau 16}}; jam harus naik masuk akal antar adegan cctv), cctv_zoom (wajah penuh di CCTV; pakai tokoh emosi seram).
- pose tokoh: berdiri, duduk (di meja dapur / sofa ruang_tamu / meja kantor / di kasur), tidur (hanya di kamar; maks 2 orang)
- emosi: {', '.join(EMOSI)} ({'seram = mata menyala, hanya untuk sosok hantu/kerasukan' if genre not in TANPA_SERAM else 'pakai kaget/marah/senang/sedih/takut sesuai cerita; jangan pakai seram'})
- efek (opsional, hemat): getar (gemetar), kedip (lampu berkedip), glitch (gangguan gambar, cocok di cctv), kejut (kilatan + dentuman, pakai maks 2 kali, untuk twist/punchline)
- Variasikan shot: jangan sama lebih dari 3 adegan berturut-turut. {'Pakai cctv/cctv_zoom untuk adegan bukti. ' if genre not in TANPA_CCTV else 'CCTV hanya bila benar-benar cocok dengan cerita. '}Pakai closeup untuk puncak emosi.
- "bicara" = nama tokoh yang mulutnya bergerak (narator bila ia tampil; tokoh lain bila narasi adalah ucapannya). Kosongkan jika tidak ada.
- Tokoh: gender {'/'.join(GENDER)}, usia {'/'.join(USIA)}, rambut {'/'.join(anim.RAMBUT)}, kulit {'/'.join(KULIT)}, baju {'/'.join(BAJU)}. Maks 4 tokoh per seri; tokoh hantu cukup ditulis dengan emosi seram pada adegannya.
- kartu_akhir.waktu = jam kunci cerita (HH:MM:SS); kartu_akhir.teks = peringatan/slogan khas seri, huruf besar, maks 5 kata.

Balas HANYA JSON valid dengan bentuk ini (jumlah adegan sesuai aturan, bukan hanya 2):
{FORMAT_JSON}"""
    return p


def _nama_cocok(nama, cast):
    for k in cast:
        if str(nama).strip().lower() == k.lower():
            return k
    return None


def _rapikan_tokoh(t):
    nama = str(t.get("nama", "")).strip()[:14] or "Tokoh"
    rambut = t.get("rambut") if t.get("rambut") in anim.RAMBUT else "pendek"
    gender = t.get("gender") if t.get("gender") in GENDER else "pria"
    return dict(nama=nama, gender=gender, usia=t.get("usia") if t.get("usia") in USIA else "dewasa", rambut=rambut, kulit=t.get("kulit") if t.get("kulit") in KULIT else "sawo",
                baju=t.get("baju") if t.get("baju") in BAJU else "abu", kacamata=bool(t.get("kacamata")))


def validasi(data, st_seri, part, cek_kata=True, genre="horor"):
    """Rapikan keluaran model supaya tidak pernah membuat render error."""
    cast = {t["nama"]: t for t in st_seri.get("tokoh", [])}
    for t in data.get("tokoh") or []:
        if isinstance(t, dict):
            r = _rapikan_tokoh(t)
            if _nama_cocok(r["nama"], cast) is None:
                cast[r["nama"]] = r
    assert cast, "tokoh kosong"
    narator = _nama_cocok(data.get("narator") or st_seri.get("narator") or "", cast) or next(iter(cast))
    bagian = data.get("bagian") or []
    assert len(bagian) >= 8, "adegan terlalu sedikit"
    out = []
    for b in bagian[:24]:
        narasi = re.sub(r"\s+", " ", str(b.get("narasi", "")).replace('"', "").replace("\u201c", "").replace("\u201d", "")).strip()
        assert narasi, "narasi kosong"
        shot = b.get("shot") if b.get("shot") in SHOT else "sedang"
        latar = b.get("latar") if b.get("latar") in LATAR else "kamar_malam"
        if shot == "ranjang" and latar not in ("kamar_malam", "kamar_pagi"):
            shot = "sedang"
        tokoh, dipakai = [], set()
        for t in b.get("tokoh") or []:
            t = {"nama": t} if isinstance(t, str) else t
            if not isinstance(t, dict):
                continue
            nm = _nama_cocok(t.get("nama", ""), cast)
            if nm and nm not in dipakai and len(tokoh) < 3:
                dipakai.add(nm)
                emo = t.get("emosi") if t.get("emosi") in EMOSI else "netral"
                if genre in TANPA_SERAM and emo == "seram":
                    emo = "marah"
                tokoh.append(dict(nama=nm, pose=t.get("pose") if t.get("pose") in POSE else "berdiri", emosi=emo))
        if shot in ("closeup", "cctv_zoom") and not tokoh:
            tokoh = [dict(nama=narator, pose="berdiri", emosi="takut")]
        if sum(t["pose"] == "tidur" for t in tokoh) > 2:
            for t in tokoh:
                t["pose"] = "berdiri"
        if "bicara" in b:
            bicara = _nama_cocok(b.get("bicara") or "", cast)
        else:
            bicara = narator if narator in dipakai else None
        if bicara and bicara not in dipakai and len(tokoh) < 3:   # pembicara harus tampil di layar
            tk_b = dict(nama=bicara, pose="berdiri", emosi="netral")
            tokoh.insert(0, tk_b) if shot in ("closeup", "cctv_zoom") else tokoh.append(tk_b)
            dipakai.add(bicara)
        if bicara not in dipakai:
            bicara = None
        item = dict(narasi=narasi, latar=latar, shot=shot, tokoh=tokoh, bicara=bicara,
                    efek=[e for e in (b.get("efek") or []) if e in EFEK][:3])
        if shot in ("cctv", "cctv_zoom"):
            c = b.get("cctv") if isinstance(b.get("cctv"), dict) else {}
            jam = str(c.get("jam", ""))
            jam = jam if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", jam) else "02:13:00"
            kec = c.get("kecepatan") if c.get("kecepatan") in (1, 2, 4, 8, 16, 32) else 1
            item["cctv"] = dict(jam=jam, kecepatan=kec)
        out.append(item)
    kata = sum(len(b["narasi"].split()) for b in out)
    assert not cek_kata or 100 <= kata <= 280, f"jumlah kata aneh: {kata}"
    ka = data.get("kartu_akhir") if isinstance(data.get("kartu_akhir"), dict) else {}
    waktu = str(ka.get("waktu", ""))
    waktu = waktu if re.fullmatch(r"\d{1,2}:\d{2}:\d{2}", waktu) else "02:13:00"
    pilihan = [re.sub(r"\s+", " ", str(x)).strip()[:28] for x in (data.get("pilihan") or []) if str(x).strip()][:2]
    return dict(judul=str(data.get("judul") or "Cerita Bersambung")[:70], pilihan=pilihan, deskripsi=str(data.get("deskripsi") or ""),
                tag=[str(x) for x in (data.get("tag") or [])][:12], narator=narator, tokoh=list(cast.values()),
                bagian=out, ringkasan=str(data.get("ringkasan") or ""), cliffhanger=str(data.get("cliffhanger") or ""),
                kartu_akhir=dict(waktu=waktu, teks=str(ka.get("teks") or "")[:60]))


def tulis_naskah(row, st):
    import requests
    part = int(row["part"])
    prompt = buat_prompt(row, st)
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0.9}}
    for model in GEMINI_MODELS:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        for percobaan in range(3):
            try:
                r = requests.post(url, params={"key": GEMINI_KEY}, json=body, timeout=180)
                if r.status_code in (404, 429):
                    print(f"Model {model} ditolak (HTTP {r.status_code}), coba model berikutnya")
                    break
                r.raise_for_status()
                bag = r.json()["candidates"][0]["content"]["parts"]
                teks = "".join(p.get("text", "") for p in bag if not p.get("thought"))
                data = validasi(json.loads(teks), st.get(row["seri"], {}), part, genre=genre_row(row))
                print("Naskah dibuat dengan model:", model)
                return data
            except Exception as e:  # noqa: BLE001
                print(f"Naskah gagal ({model}, percobaan {percobaan + 1}): {e}")
                time.sleep(5)
    sys.exit("Gagal membuat naskah.")


def naskah_contoh():
    """Contoh lengkap (dipakai --uji) sekaligus gambaran hasil yang dituju."""
    def T(n, p="berdiri", e="netral"):
        return dict(nama=n, pose=p, emosi=e)
    B, L = "Bima", "Laras"
    s = [
        ("Jangan pernah buka pintu kalau ada yang mengetuk jam tiga pagi.", "luar_malam", "lebar", [T(B, e="takut")], B, ["getar"]),
        ("Aku Bima. Ini istriku, Laras.", "ruang_tamu", "sedang", [T(B, "duduk", "senang"), T(L, "duduk", "senang")], B, []),
        ("Kami pindah ke rumah kontrakan yang murah.", "ruang_tamu", "lebar", [T(B, e="senang"), T(L, e="senang")], B, []),
        ("Terlalu murah, sebenarnya.", "dapur", "closeup", [T(B)], B, []),
        ("Nak, rumah itu jangan ditinggali lama-lama.", "ruang_tamu", "sedang", [T(B, "duduk", "kaget"), T("Bu Rukmi", "duduk", "sedih")], "Bu Rukmi", []),
        ("Kenapa, Bu?", "ruang_tamu", "sedang", [T(B, "duduk", "kaget"), T(L, "duduk", "netral")], L, []),
        ("Malam pertama, pintu depan diketuk. Satu kali.", "kamar_malam", "ranjang", [T(B, "tidur"), T(L, "tidur")], None, []),
        ("Jam tiga tepat.", "kamar_malam", "lebar", [T(B, "tidur"), T(L, "tidur")], None, ["kedip"]),
        ("Malam kedua, dua kali.", "kamar_malam", "ranjang", [T(B, "tidur", "takut"), T(L, "tidur")], None, []),
        ("Malam ketujuh, tujuh kali.", "lorong", "sedang", [T(B, e="takut")], B, ["getar"]),
        ("Jadi kupasang kamera kecil di ruang tamu.", "rak_buku", "lebar", [T(B)], B, []),
        ("Jam tiga. Aku memutar rekamannya.", "ruang_tamu", "sedang", [T(B, "duduk", "takut")], B, []),
        ("Tidak ada siapa pun di depan pintu.", "lorong", "cctv", [], None, []),
        ("Tapi pintunya bergetar dari dalam.", "lorong", "cctv", [], None, ["glitch"]),
        ("Lalu seseorang berjalan ke arah pintu.", "lorong", "cctv", [T(L, e="seram")], None, []),
        ("Rambut panjang. Gaun putih. Itu Laras.", "lorong", "cctv_zoom", [T(L, e="seram")], None, ["glitch", "getar"]),
        ("Padahal Laras sedang tidur di sampingku.", "kamar_malam", "ranjang", [T(B, "tidur", "kaget"), T(L, "tidur")], None, ["kejut"]),
    ]
    jam = iter(["03:00:00", "03:00:41", "03:01:02", "03:01:10"])
    bagian = []
    for n, la, sh, tk, bc, ef in s:
        d = dict(narasi=n, latar=la, shot=sh, tokoh=tk, efek=ef)
        if bc:
            d["bicara"] = bc
        if sh.startswith("cctv"):
            d["cctv"] = dict(jam=next(jam), kecepatan=1)
        bagian.append(d)
    return dict(judul="Rumah Nomor 13", deskripsi="Setiap jam tiga pagi, pintu depan diketuk.", tag=["horor"], narator=B,
                tokoh=[dict(nama=B, gender="pria", rambut="pendek", kulit="sawo", baju="abu"),
                       dict(nama=L, gender="wanita", rambut="panjang", kulit="terang", baju="putih"),
                       dict(nama="Bu Rukmi", gender="wanita", usia="tua", rambut="kuncir", kulit="sawo", baju="merah")],
                bagian=bagian + [], ringkasan="Bima dan Laras pindah ke rumah nomor 13; pintu diketuk tiap jam tiga.",
                cliffhanger="Rekaman memperlihatkan Laras di depan pintu, padahal Laras tertidur di sampingnya.",
                kartu_akhir=dict(waktu="03:00:00", teks="Jangan buka pintu jam 03.00"))


# ================================================================ suara
def _komunikasi(teks, voice, rate, pitch):
    import edge_tts
    try:
        return edge_tts.Communicate(teks, voice, rate=rate, pitch=pitch, boundary="WordBoundary")
    except TypeError:  # edge-tts versi lama tanpa parameter boundary
        return edge_tts.Communicate(teks, voice, rate=rate, pitch=pitch)


async def _tts(teks, path, voice, rate, pitch):
    com = _komunikasi(teks, voice, rate, pitch)
    kata = []
    with open(path, "wb") as f:
        async for ch in com.stream():
            if ch["type"] == "audio":
                f.write(ch["data"])
            elif ch["type"] == "WordBoundary":
                mulai = ch["offset"] / 1e7
                kata.append((mulai, mulai + ch["duration"] / 1e7, ch["text"]))
    return kata


def suara(teks, path, voice, rate="+0%", pitch="+0Hz"):
    """Daftar (mulai, selesai, kata) bila server memberi waktu per kata, kalau tidak daftar kosong."""
    if UJI:  # suara tiruan: nada bergetar sepanjang kalimat (hanya untuk uji tampilan)
        dur = max(1.2, 0.36 * len(teks.split()))
        frek = max(90, 170 + 5 * parse_hz(pitch))            # nada tiruan ikut berbeda per tokoh
        anim.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"sine=f={frek}:d={dur:.2f}",
                  "-af", "tremolo=f=4.5:d=0.95,volume=0.8", str(path)])
        return []
    for percobaan in range(3):
        try:
            return asyncio.run(_tts(teks, str(path), voice, rate, pitch))
        except Exception as e:  # noqa: BLE001
            print(f"Suara gagal (percobaan {percobaan + 1}): {e}")
            time.sleep(3)
    sys.exit("Gagal membuat suara.")


# ================================================================ render paralel
_KAR = {}


def _worker(args):
    b, specs, audio, kata, out, pengantar = args
    cast = {}
    for s in specs:
        k = json.dumps(s, sort_keys=True)
        if k not in _KAR:
            _KAR[k] = anim.Karakter(s)
        cast[s["nama"]] = _KAR[k]
    render_segmen(b, cast, audio, kata, out, set(pengantar))
    return out


def gabung(segmen, out_path):
    daftar = WORK / "list.txt"
    daftar.write_text("".join(f"file '{Path(s).name}'\n" for s in segmen), encoding="utf-8")
    anim.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(daftar),
              "-c:v", "copy", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(out_path)])


# ================================================================ upload
def upload_youtube(video, naskah, row):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials(None, refresh_token=os.environ["YT_REFRESH_TOKEN"], token_uri="https://oauth2.googleapis.com/token",
                        client_id=os.environ["YT_CLIENT_ID"], client_secret=os.environ["YT_CLIENT_SECRET"],
                        scopes=["https://www.googleapis.com/auth/youtube.upload"])
    creds.refresh(Request())
    yt = build("youtube", "v3", credentials=creds)
    part, total = int(row["part"]), int(row["total"])
    g = GENRE[genre_row(row)]
    tag_hash = g["hashtag"]
    ajak = ""
    if part < total and naskah.get("pilihan"):
        ajak = "\nPart berikutnya KAMU yang pilih! Komen 1 atau 2:\n" + "\n".join(
            f"{i} = {p}" for i, p in enumerate(naskah["pilihan"], 1)) + "\n"
    deskripsi = (f"{naskah['deskripsi']}\n\nPart {part} dari {total} - {row['seri']}\n"
                 + ajak + ("Nonton part berikutnya, ikuti channel ini!\n" if part < total else "TAMAT.\n")
                 + f"\n{tag_hash}\nCerita fiksi. Animasi dan narasi dibuat dengan bantuan AI.")
    body = {"snippet": {"title": f"Part {part} | {naskah['judul']}"[:95], "description": deskripsi[:4900],
                        "tags": (naskah["tag"] + g["tag"] + ["shorts"])[:15], "categoryId": "24"},
            "status": {"privacyStatus": PRIVACY, "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(str(video), mimetype="video/mp4", resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    print("Terunggah: https://youtube.com/shorts/" + resp["id"])


# ================================================================ main
def main():
    if UJI:
        row = dict(seri="Rumah Nomor 13 (uji)", genre=os.environ.get("GENRE_UJI", "horor"), part="1", total="3", premis="", arah="", status="")
        rows, st = [row], {}
        naskah = validasi(naskah_contoh(), {}, 1, cek_kata=False)
        naskah["pilihan"] = ["Cari tetangga tua", "Pasang kamera lagi"]
    else:
        rows = baca_topik()
        row = pilih_topik(rows)
        st = muat_state()
        pil = st.get(row["seri"], {}).get("pilihan") or []
        if MENANG in ("1", "2") and len(pil) >= int(MENANG):  # voting penonton menimpa kolom 'arah'
            row["arah"] = f"PILIHAN PENONTON MENANG: {pil[int(MENANG) - 1]}. Jadikan ini arah utama part ini."
            print("Voting penonton dipakai:", pil[int(MENANG) - 1])
        print(f"Seri: {row['seri']} ({genre_row(row)}) - Part {row['part']}/{row['total']}")
        naskah = tulis_naskah(row, st)
    part, total = int(row["part"]), int(row["total"])
    print("Judul:", naskah["judul"], "| adegan:", len(naskah["bagian"]), "| narator:", naskah["narator"])

    kenal = set(t["nama"] for t in st.get(row["seri"], {}).get("tokoh", [])) if part > 1 else set()
    gender = {t["nama"]: t["gender"] for t in naskah["tokoh"]}
    usia = {t["nama"]: t.get("usia", "dewasa") for t in naskah["tokoh"]}
    kerja = []
    for i, b in enumerate(naskah["bagian"]):
        audio, seg = WORK / f"a{i}.mp3", WORK / f"seg{i}.mp4"
        voice, rate, pitch = atur_suara(row, b["bicara"], naskah["narator"], gender, usia)
        kata = suara(b["narasi"], audio, voice, rate, pitch)
        baru = set()
        if b["shot"] in ("lebar", "sedang", "ranjang"):
            baru = {t["nama"] for t in b["tokoh"]} - kenal
            kenal |= baru
        kerja.append((b, naskah["tokoh"], str(audio), kata, str(seg), sorted(baru)))
    print("Suara selesai, mulai render animasi...")

    n = max(1, min(4, os.cpu_count() or 1))
    with ProcessPoolExecutor(max_workers=n) as ex:
        for k, hasil in enumerate(ex.map(_worker, kerja), 1):
            print(f"Bagian {k}/{len(kerja)} selesai")
    segmen = [k[4] for k in kerja]

    ka = naskah["kartu_akhir"]
    kartu = WORK / "kartu.mp4"
    pil = naskah.get("pilihan") or []
    ajak = (["Komen pilihanmu:"] + [f"{i}. {p}" for i, p in enumerate(pil, 1)]
            if part < total and len(pil) == 2 else ["Komen: seri apa lagi?"])
    render_kartu_akhir(dict(judul="BERSAMBUNG…" if part < total else "", besar=f"PART {part + 1}" if part < total else "TAMAT",
                            kecil=ka["teks"] if part < total else GENRE[genre_row(row)]["akhir"],
                            rec=ka["waktu"], efek=["glitch"], ajak=ajak), kartu)
    segmen.append(kartu)

    final = WORK / ("final_uji.mp4" if UJI else "final.mp4")
    gabung(segmen, final)
    print("Video jadi:", final, f"({anim.durasi(final):.1f} detik)")
    (WORK / "info.json").write_text(json.dumps(dict(
        seri=row["seri"], genre=genre_row(row), part=part, total=total, judul=f"Part {part} | {naskah['judul']}",
        deskripsi=naskah["deskripsi"], hashtag=GENRE[genre_row(row)]["hashtag"], pilihan=naskah.get("pilihan", []),
        cliffhanger=naskah["cliffhanger"]), ensure_ascii=False, indent=2), encoding="utf-8")

    if UPLOAD:
        upload_youtube(final, naskah, row)
    if UPLOAD or SIMPAN:
        s = st.setdefault(row["seri"], {})
        s["narator"] = naskah["narator"]
        s["tokoh"] = naskah["tokoh"]
        s.setdefault("ringkasan", []).append(f"Part {part}: {naskah['ringkasan']}")
        s["cliffhanger"] = naskah["cliffhanger"]
        s["pilihan"] = naskah.get("pilihan", [])
        simpan_state(st)
        row["status"] = "selesai"
        simpan_topik(rows)


if __name__ == "__main__":
    main()
