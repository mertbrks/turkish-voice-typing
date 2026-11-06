"""Sesli komut: mikrofondan Türkçe konuş, seçili pencereye (ör. Claude Code terminali) yazsın.

Konuşma, ekran kartında çalışan Whisper ile internetsiz çözülür; ekran kartı yoksa Google'a düşer.
"""
import ctypes
import glob
import os
import re
import site
import sys
import threading
import time
import tkinter as tk
from ctypes import wintypes
from tkinter import ttk

import functools

import numpy as np
from PIL import Image, ImageDraw
import speech_recognition as sr
import win32api
import win32con
import win32gui

MODEL = "large-v3-turbo"
HIZ = 16000          # Whisper 16 kHz bekler
PARCA = 1024
SESSIZLIK_SN = 1.8   # konuşma bittikten sonra bu kadar sessizlikte durur; düşünme duraklamaları kesmesin
EN_UZUN_SN = 60
VK_V = 0x56
VK_F9 = 0x78
MOD_NOREPEAT = 0x4000  # tuş basılı kalınca tekrar tetiklemesin
KONUM_DOSYA = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "konum.txt")
CEVAP_DOSYA = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "son_cevap.txt")  # cevap_kaydet.py yazar
SESLENDIR_DOSYA = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "seslendir.acik")  # varsa otomatik okur
SES = "tr-TR-AhmetNeural"
OKUMA_HIZI = "+20%"   # edge-tts varsayılanı Mert'e yavaş geldi
_mci = ctypes.windll.winmm.mciSendStringW
# karşılaştırmadan önce sondaki nokta silinir; buradakiler de noktasız yazılmalı
UYDURMA = {"izlediğiniz için teşekkür ederim", "izlediğiniz için teşekkürler", "altyazı m.k",
           "abone olmayı unutmayın", "teşekkürler", "teşekkür ederim"}
UYDURMA_SON = re.compile(r"[\s.,!]*(abone ol(mayı unutmayın)?|izlediğiniz için teşekkür(ler| ederim)"
                         r"|altyazı m\.? ?k)[\s.!]*$",
                         re.IGNORECASE)

# Renkler
ARKA, KART, YAZI, SOLUK = "#1c1916", "#2b2723", "#f4eee6", "#9a9086"
CIZGI = "#4a3f36"    # panel kenarı
VURGU, KAYIT, ISLEM = "#ec7a32", "#e04b3a", "#d9a441"
D, L = 30, 84        # damlanın derinliği (kenardan içeri) ve kenar boyunca uzunluğu
EK = 9               # fare yaklaşınca damlanın uzayabileceği ek pay (pencere bu kadar geniş, fazlası saydam)
SS = 4               # yumuşak kenar için 4 kat büyük çizip küçültülür
MOD_SHIFT = 0x0004
R = 46               # köşedeki çeyrek damlanın boyu
KOSE_YAKIN = 90      # köşeye bu kadar yaklaşınca köşeye oturur
CEKIM_MESAFE = 170   # fare bu kadar yaklaşınca damla ona doğru uzamaya başlar
KENARLAR = ("sag", "sol", "ust", "alt", "sag-ust", "sag-alt", "sol-ust", "sol-alt")


def boyut(kenar):
    if "-" in kenar:
        return R + EK, R + EK
    return (D + EK, L) if kenar in ("sag", "sol") else (L, D + EK)


def cevir(kenar, x, y):
    """Sağ kenar (ya da sağ-alt köşe) çizimindeki noktayı istenen kenara/köşeye taşır."""
    w, h = boyut("sag-alt" if "-" in kenar else "sag")
    return {"sag": (x, y), "sol": (w - x, y), "ust": (y, w - x), "alt": (h - y, x),
            "sag-alt": (x, y), "sag-ust": (x, h - y), "sol-alt": (w - x, y), "sol-ust": (w - x, h - y)}[kenar]


def govde_olcu(kose, olcek, cekim, kayma):
    """(göbek merkezi, sağ kenar çiziminde 1x), bombe tepesinin kenar boyunca yeri (0..1), derinlik/yarıçap."""
    if kose:
        r = (R * 0.95 + EK * 0.9 * cekim) * olcek
        w = R + EK
        return (w - 0.38 * r, w - 0.38 * r), None, r
    p = 0.5 + 0.17 * kayma                                 # fareye doğru kayan bombe
    derin = olcek * ((D - 2) + EK * cekim)
    return (D + EK - max(derin, 1) / 2, L * p), p, derin


def merkez(kenar):
    # panel/baloncuk yerleşimi için duran hâldeki göbek noktası
    return cevir(kenar, *govde_olcu("-" in kenar, 1.0, 0.0, 0.0)[0])


@functools.lru_cache(maxsize=512)
def cam_govde(kose, renk, olcek, cekim=0.0, kayma=0.0):
    """Sağ kenara (ya da sağ-alt köşeye) yapışık cam damla, SS kat büyüklükte RGBA (iOS 'liquid glass' benzeri).
    olcek: belirme animasyonu (0..1); cekim: fare yakınlığı (0..1); kayma: bombenin fareye kayması (-1..1)."""
    _, p, olcu = govde_olcu(kose, olcek, cekim, kayma)
    W, H = (boyut("sag-alt") if kose else boyut("sag"))
    W, H = W * SS, H * SS
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    t = yy / (H - 1)
    if kose:  # köşede çeyrek daire
        r = olcu * SS
        uzak = r - np.hypot(W - xx, H - yy)               # dış kavise uzaklık
        ic = uzak >= 0
        duz = (xx < W - 2 * SS) & (yy < H - 2 * SS)        # ekran kenarına değen düz taraflarda ışık yok
        pcx, pcy, psx, psy = W - r * 0.5, H - r * 0.78, r * 0.2, r * 0.13
    else:
        tw = t ** (np.log(0.5) / np.log(p))                # bombe tepesi p'ye kayar
        derin = olcu * SS * np.clip(np.sin(np.pi * tw), 0, 1) ** 1.4  # iki uç sivri
        ic = xx >= W - derin
        uzak = xx - (W - derin)                            # dış eğriye yatay uzaklık
        duz = xx < W - 2 * SS
        pcx, pcy, psx, psy = W - derin * 0.6, H * (p - 0.2), 0.22 * D * SS * max(olcek, 0.2), H * 0.11
    renk_ = np.array([int(renk[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
    taban = renk_ * 0.75 + 255 * 0.25                       # buzlu, açık tonlu cam
    a = np.where(ic, 175 - 55 * t, 0)                      # üstte daha dolu, altta daha saydam
    kenar_isik = (ic & (uzak < 1.6 * SS) & duz) * (0.95 - 0.6 * t)
    parlama = ic * 0.5 * np.exp(-(((xx - pcx) / psx) ** 2 + ((yy - pcy) / psy) ** 2))
    golge = (ic & (uzak < 3.5 * SS) & (uzak >= 1.6 * SS)) * 0.18 * t  # alt kenarda hafif derinlik
    beyaz = np.maximum(kenar_isik, parlama)[..., None]
    rgb = taban * (1 - beyaz) + 255 * beyaz
    rgb = rgb * (1 - golge[..., None])
    a = a + (255 - a) * beyaz[..., 0] * ic
    return Image.fromarray(np.dstack([rgb, a]).clip(0, 255).astype(np.uint8), "RGBA")


def damla_resmi(kenar, renk, cubuklar=None, olcek=1.0, cekim=0.0, kayma=0.0):
    """Kenara/köşeye döndürülmüş damla + içinde mikrofon ya da ses çizgileri.
    kayma ekranın x/y yönündedir (alt kenarda çizim ters döndüğü için çevrilir)."""
    kose = "-" in kenar
    kayma_ = 0.0 if kose else (-kayma if kenar == "alt" else kayma)
    im = cam_govde(kose, renk, olcek, cekim, kayma_)
    im = {"sag": lambda: im.copy(), "sol": lambda: im.transpose(Image.FLIP_LEFT_RIGHT),
          "ust": lambda: im.rotate(90, expand=True), "alt": lambda: im.rotate(-90, expand=True),
          "sag-alt": lambda: im.copy(), "sag-ust": lambda: im.transpose(Image.FLIP_TOP_BOTTOM),
          "sol-alt": lambda: im.transpose(Image.FLIP_LEFT_RIGHT), "sol-ust": lambda: im.transpose(Image.ROTATE_180),
          }[kenar]()
    cx, cy = cevir(kenar, *govde_olcu(kose, olcek, cekim, kayma_)[0])
    c = ImageDraw.Draw(im)
    s = lambda *v: [n * SS for n in v]
    if olcek < 0.85:  # büyürken içi boş
        pass
    elif cubuklar:
        for i, h in enumerate(cubuklar):
            x = cx + (i - 1.5) * 5
            c.rounded_rectangle(s(x - 1.3, cy - h, x + 1.3, cy + h), radius=1.3 * SS, fill=(255, 255, 255, 245))
    else:  # mikrofon: kapsül + kavis + ayak
        c.rounded_rectangle(s(cx - 2.6, cy - 7, cx + 2.6, cy + 1.5), radius=2.6 * SS, fill=(255, 255, 255, 245))
        c.arc(s(cx - 5, cy - 4.5, cx + 5, cy + 4.5), 10, 170, fill=(255, 255, 255, 245), width=int(1.3 * SS))
        c.line(s(cx, cy + 4.5, cx, cy + 7), fill=(255, 255, 255, 245), width=int(1.3 * SS))
    return im.resize((im.width // SS, im.height // SS), Image.LANCZOS)


OW, OR = 60, 15      # oynat topunun penceresi (halkalar için pay) ve cam topun yarıçapı
OYNAT_STIL = "dalga"  # sade: nefes alır · dalga: dışa halka yayar · jole: esneyip sallanır


@functools.lru_cache(maxsize=256)
def cam_top(renk, rx, ry):
    """Damlayla aynı buzlu cam görünüşte serbest top (rx, ry: 1x yarıçaplar), SS kat büyüklükte RGBA."""
    W = int((max(rx, ry) + 2) * 2 * SS)
    yy, xx = np.mgrid[0:W, 0:W].astype(np.float32)
    c, RX, RY = W / 2, rx * SS, ry * SS
    dn = np.hypot((xx - c) / RX, (yy - c) / RY)
    ic = dn <= 1
    uzak = (1 - dn) * min(RX, RY)                          # dış kenara uzaklık
    t = np.clip((yy - (c - RY)) / (2 * RY), 0, 1)
    renk_ = np.array([int(renk[i:i + 2], 16) for i in (1, 3, 5)], np.float32)
    taban = renk_ * 0.75 + 255 * 0.25
    a = np.where(ic, 185 - 55 * t, 0)
    kenar_isik = (ic & (uzak < 1.1 * SS)) * (0.9 - 0.7 * t)
    parlama = ic * 0.55 * np.exp(-(((xx - (c - 0.3 * RX)) / (0.42 * RX)) ** 2 + ((yy - (c - 0.45 * RY)) / (0.24 * RY)) ** 2))
    golge = (ic & (uzak < 3.5 * SS) & (uzak >= 1.1 * SS)) * 0.15 * t
    beyaz = np.maximum(kenar_isik, parlama)[..., None]
    rgb = (taban * (1 - beyaz) + 255 * beyaz) * (1 - golge[..., None])
    a = a + (255 - a) * beyaz[..., 0] * ic
    return Image.fromarray(np.dstack([rgb, a]).clip(0, 255).astype(np.uint8), "RGBA")


def oynat_resmi(stil, durum, t, olcek=1.0, kx=0.0, ky=0.0, ustunde=False):
    """Cevabı okutan cam top. durum: hazir (▶), hazirlaniyor (dönen yay), caliyor (ses çubukları, fare üstündeyse ■).
    t: saniye (animasyon), olcek: belirme yayı, kx/ky: fareye doğru kayma."""
    im = Image.new("RGBA", (OW * SS, OW * SS), (0, 0, 0, 0))
    c = ImageDraw.Draw(im)
    s = lambda *v: [n * SS for n in v]
    cx, cy = OW / 2 + kx, OW / 2 + ky
    canli = durum == "caliyor"
    turuncu = tuple(int(c_ * 0.7 + 255 * 0.3) for c_ in (int(VURGU[i:i + 2], 16) for i in (1, 3, 5)))  # halka: açık turuncu
    if stil == "dalga" and olcek > 0.9:  # dışa yayılan iki halka; okurken hızlanır
        for k in range(2):
            f = (t * (0.9 if canli else 0.45) + k / 2) % 1
            r = OR + 2 + f * 12
            c.ellipse(s(cx - r, cy - r, cx + r, cy + r), outline=(*turuncu, int((1 - f) ** 2 * (210 if canli else 150))),
                      width=int(1.5 * SS))
    nabiz = 1 + (0.05 * abs(np.sin(t * 6)) if canli else 0.035 * np.sin(t * 2 * np.pi * 0.8))
    rx = ry = OR * olcek * nabiz
    if stil == "jole":  # esneyip sallanma: biri uzarken öbürü kısalır
        w = 0.08 * np.sin(t * 2 * np.pi * (1.7 if canli else 0.7))
        rx, ry = rx * (1 + w), ry * (1 - w)
    top = cam_top(VURGU, max(1.0, round(rx * 4) / 4), max(1.0, round(ry * 4) / 4))
    im.alpha_composite(top, (int(cx * SS - top.width / 2), int(cy * SS - top.height / 2)))
    beyaz = (255, 255, 255, 245)
    if olcek < 0.6:
        pass
    elif durum == "hazirlaniyor":
        bas = (t * 400) % 360
        c.arc(s(cx - 6.5, cy - 6.5, cx + 6.5, cy + 6.5), bas, bas + 260, fill=beyaz, width=int(1.8 * SS))
    elif canli and ustunde:  # okurken üstüne gelince: durdur
        c.rounded_rectangle(s(cx - 4.5, cy - 4.5, cx + 4.5, cy + 4.5), radius=1.5 * SS, fill=beyaz)
    elif canli:
        for i in range(4):
            h = 1.5 + 5.5 * (0.5 + 0.5 * np.sin(t * 9 + i * 1.9))
            x = cx + (i - 1.5) * 4.2
            c.rounded_rectangle(s(x - 1.2, cy - h, x + 1.2, cy + h), radius=1.2 * SS, fill=beyaz)
    else:
        c.polygon(s(cx - 3.5, cy - 6, cx - 3.5, cy + 6, cx + 6.5, cy), fill=beyaz)
    return im.resize((OW, OW), Image.LANCZOS)


def belirme(p):
    # 0..1 → üstünden aşıp yerine oturan yay (jöle gibi "pop")
    return 1.0 if p >= 1 else float(1 - np.exp(-6 * p) * np.cos(9 * p))


class _BMI(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD), ("w", wintypes.LONG), ("h", wintypes.LONG), ("planes", wintypes.WORD),
                ("bits", wintypes.WORD), ("comp", wintypes.DWORD), ("isize", wintypes.DWORD),
                ("xp", wintypes.LONG), ("yp", wintypes.LONG), ("used", wintypes.DWORD), ("imp", wintypes.DWORD)]


_u32, _gdi = ctypes.windll.user32, ctypes.windll.gdi32
for _f in (_u32.GetDC, _gdi.CreateCompatibleDC, _gdi.CreateDIBSection, _gdi.SelectObject):
    _f.restype = ctypes.c_void_p
_gdi.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint, ctypes.c_void_p,
                                  ctypes.c_void_p, wintypes.DWORD]
_gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
_gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_gdi.DeleteObject.argtypes = [ctypes.c_void_p]
_gdi.DeleteDC.argtypes = [ctypes.c_void_p]
_u32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_u32.UpdateLayeredWindow.argtypes = [ctypes.c_void_p] * 9


def katmana_bas(hwnd, im):
    # piksel başına saydamlık: Windows'a önceden çarpılmış BGRA verilir
    a = np.asarray(im).astype(np.uint16)
    a[..., :3] = a[..., :3] * a[..., 3:4] // 255
    veri = np.ascontiguousarray(a[..., [2, 1, 0, 3]].astype(np.uint8)).tobytes()
    w, h = im.size
    ekran = _u32.GetDC(None)
    dc = _gdi.CreateCompatibleDC(ekran)
    bmi = _BMI(ctypes.sizeof(_BMI), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    bitler = ctypes.c_void_p()
    bmp = _gdi.CreateDIBSection(dc, ctypes.byref(bmi), 0, ctypes.byref(bitler), None, 0)
    ctypes.memmove(bitler, veri, len(veri))
    eski = _gdi.SelectObject(dc, bmp)
    boyut, kaynak = wintypes.SIZE(w, h), wintypes.POINT(0, 0)
    karisim = ctypes.c_uint32(0x01FF0000)                  # AC_SRC_OVER, alfa 255, AC_SRC_ALPHA
    _u32.UpdateLayeredWindow(hwnd, ekran, None, ctypes.byref(boyut), dc, ctypes.byref(kaynak),
                             None, ctypes.byref(karisim), 2)  # ULW_ALPHA
    _gdi.SelectObject(dc, eski)
    _gdi.DeleteObject(bmp)
    _gdi.DeleteDC(dc)
    _u32.ReleaseDC(None, ekran)


def cam_kart(pencere):
    # Windows 11: yuvarlak köşe + ince sıcak kenar çizgisi
    h = kabuk(pencere)
    for ozellik, deger in ((33, 2), (34, int(CIZGI[5:7] + CIZGI[3:5] + CIZGI[1:3], 16))):  # köşe, kenar rengi (BGR)
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(h, ozellik, ctypes.byref(ctypes.c_int(deger)),
                                                       ctypes.sizeof(ctypes.c_int))
        except Exception:
            pass


def calisma_alani():
    # görev çubuğu hariç ana ekran
    return win32api.GetMonitorInfo(win32api.MonitorFromPoint((0, 0)))["Work"]


def cuda_dll_yollari():
    # pip ile gelen nvidia-cublas/cudnn DLL'leri PATH'te değil; Windows'a gösteriyoruz
    for kok in site.getsitepackages():
        for d in glob.glob(os.path.join(kok, "Lib", "site-packages", "nvidia", "*", "bin")) + \
                 glob.glob(os.path.join(kok, "nvidia", "*", "bin")):
            os.add_dll_directory(d)
            os.environ["PATH"] = d + os.pathsep + os.environ["PATH"]


def pencereler():
    sonuc = []

    def ekle(hwnd, _):
        baslik = win32gui.GetWindowText(hwnd)
        if win32gui.IsWindowVisible(hwnd) and baslik.strip():
            sonuc.append((baslik, hwnd))

    win32gui.EnumWindows(ekle, None)
    return sonuc


def tus(vk, birak=False):
    win32api.keybd_event(vk, 0, win32con.KEYEVENTF_KEYUP if birak else 0, 0)


def kabuk(pencere):
    return ctypes.windll.user32.GetParent(pencere.winfo_id())


def odak_alma(pencere):
    # tıklanınca ya da belirince yazdığın pencereden odağı çalmasın
    h = kabuk(pencere)
    stil = win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE)
    win32gui.SetWindowLong(h, win32con.GWL_EXSTYLE,
                           stil | win32con.WS_EX_NOACTIVATE | win32con.WS_EX_TOOLWINDOW)


def ekrana_sigdir(x, y, g, y_):
    # ekran çıkarılmışsa kayıtlı konum görünmez kalmasın
    sol, ust = win32api.GetSystemMetrics(76), win32api.GetSystemMetrics(77)
    gen, yuk = win32api.GetSystemMetrics(78), win32api.GetSystemMetrics(79)
    return max(sol, min(x, sol + gen - g)), max(ust, min(y, ust + yuk - y_))


def okunur(metin):
    """Markdown cevabı kulağa uygun düz metne çevirir: kod blokları, bağlantı adresleri, işaretler gider."""
    metin = re.sub(r"```.*?(```|$)", " (kod) ", metin, flags=re.S)
    metin = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", metin)       # [yazı](adres) → yazı
    metin = re.sub(r"https?://\S+", "", metin)
    metin = re.sub(r"^\s*(#+|[-*+>]|\d+\.)\s+", "", metin, flags=re.M)  # başlık, madde, alıntı
    metin = re.sub(r"[`*_|#]+", " ", metin)
    metin = re.sub(r"^[\s:-]+$", "", metin, flags=re.M)           # tablo ayırıcıları
    return re.sub(r"\s+", " ", metin).strip()


assert okunur("## Başlık\n- **kalın** `kod` [link](http://x)\n```py\nx=1\n```") == "Başlık kalın kod link (kod)"


def parcala(metin, sinir=350):
    """İlk cümle tek başına (okuma hemen başlasın), gerisi ~sinir karakterlik gruplar."""
    cumleler = [c for c in re.split(r"(?<=[.!?:])\s+", metin) if c]
    parcalar, tampon = cumleler[:1], ""
    for c in cumleler[1:]:
        if tampon and len(tampon) + len(c) > sinir:
            parcalar.append(tampon)
            tampon = ""
        tampon = f"{tampon} {c}".strip()
    return parcalar + ([tampon] if tampon else [])


assert parcala("Bir. İki! Üç?", 6) == ["Bir.", "İki!", "Üç?"] and parcala("Bir. İki. Üç.") == ["Bir.", "İki. Üç."]


def sil(yol):
    try:
        os.remove(yol)
    except OSError:
        pass


def seviye(veri):
    x = np.frombuffer(veri, np.int16).astype(np.float32)
    return float(np.sqrt(np.mean(x * x)))


class Cozucu:
    """Önce ekran kartında Whisper; olmazsa Google."""

    def __init__(self):
        self.model = None
        self.ad = "yükleniyor"

    def yukle(self):
        try:
            cuda_dll_yollari()
            from faster_whisper import WhisperModel
            self.model = WhisperModel(MODEL, device="cuda", compute_type="float16")
            self.model.transcribe(np.zeros(HIZ, np.float32), language="tr")  # ilk çağrı yavaş, ısıt
            self.ad = "internetsiz · ekran kartı"
        except Exception:
            self.model = None
            self.ad = "Google · internet"

    def coz(self, ham):
        if self.model:
            ses = np.frombuffer(ham, np.int16).astype(np.float32) / 32768
            parcalar, _ = self.model.transcribe(ses, language="tr", beam_size=1,
                                                condition_on_previous_text=False)
            metin = " ".join(p.text.strip() for p in parcalar if p.no_speech_prob < 0.6).strip()
            # Whisper sessizlikte/gürültüde altyazı kalıpları uydurur; sona eklenenleri de kes
            metin = UYDURMA_SON.sub("", metin).strip()
            return "" if metin.rstrip(".! ").lower() in UYDURMA else metin
        return sr.Recognizer().recognize_google(sr.AudioData(ham, HIZ, 2), language="tr-TR")


class Uygulama:
    def __init__(self, kok):
        self.kok = kok
        self.kayitta = False
        self.mesgul = False
        self.esik = 300.0
        self.anlik = 0.0
        self.cozucu = Cozucu()

        kok.title("Sesli Komut")
        kok.overrideredirect(True)  # başlık çubuğu yok; damlanın yanında açılan cam kart
        kok.attributes("-topmost", True, "-alpha", 0.95)
        kok.configure(bg=ARKA)
        kok.update()
        cam_kart(kok)
        kok.withdraw()  # açılışta yalnızca damla görünür

        stil = ttk.Style()
        stil.theme_use("clam")
        stil.configure("TCombobox", fieldbackground=KART, background=KART, foreground=YAZI,
                       arrowcolor=SOLUK, bordercolor=KART, lightcolor=KART, darkcolor=KART)
        stil.map("TCombobox", fieldbackground=[("readonly", KART)], foreground=[("readonly", YAZI)],
                 selectbackground=[("readonly", KART)], selectforeground=[("readonly", YAZI)])
        kok.option_add("*TCombobox*Listbox.background", KART)
        kok.option_add("*TCombobox*Listbox.foreground", YAZI)
        kok.option_add("*TCombobox*Listbox.selectBackground", VURGU)

        ust = tk.Frame(kok, bg=ARKA)
        ust.pack(fill="x", padx=18, pady=(14, 10))
        tk.Label(ust, text="●", bg=ARKA, fg=VURGU, font=("Segoe UI", 9)).pack(side="left", padx=(0, 6))
        tk.Label(ust, text="Sesli Komut", bg=ARKA, fg=YAZI, font=("Segoe UI Semibold", 12)).pack(side="left")
        kucult = tk.Label(ust, text="✕", bg=ARKA, fg=SOLUK, font=("Segoe UI", 10), cursor="hand2")
        kucult.pack(side="right", padx=(10, 0))
        kucult.bind("<Button-1>", lambda e: self.panel_degis())
        self.motor = tk.Label(ust, text="motor yükleniyor…", bg=ARKA, fg=SOLUK, font=("Segoe UI", 8))
        self.motor.pack(side="right")

        hedef = tk.Frame(kok, bg=ARKA)
        hedef.pack(fill="x", padx=18)
        self.secim = ttk.Combobox(hedef, state="readonly", font=("Segoe UI", 9), width=34)
        self.secim.pack(side="left", fill="x", expand=True, ipady=3)
        yenile = tk.Label(hedef, text="⟳", bg=KART, fg=YAZI, font=("Segoe UI", 11), width=3, cursor="hand2")
        yenile.pack(side="left", padx=(6, 0), fill="y")
        yenile.bind("<Button-1>", lambda e: self.yenile())

        self.enter = True
        self.enter_btn = tk.Label(kok, bg=ARKA, font=("Segoe UI", 9), cursor="hand2", anchor="w")
        self.enter_btn.pack(fill="x", padx=18, pady=(10, 0))
        self.seslendir = os.path.exists(SESLENDIR_DOSYA)
        self.ses_btn = tk.Label(kok, bg=ARKA, font=("Segoe UI", 9), cursor="hand2", anchor="w")
        self.ses_btn.pack(fill="x", padx=18, pady=(6, 0))
        self.ses_btn.bind("<Button-1>", lambda e: self.seslendir_degis())
        self.seslendir_degis(ilk=True)

        tk.Frame(kok, bg=CIZGI, height=1).pack(fill="x", padx=18, pady=10)
        self.durum = tk.Label(kok, text="Hazırlanıyor…", bg=ARKA, fg=YAZI, font=("Segoe UI", 10), anchor="w")
        self.durum.pack(fill="x", padx=18)
        self.son = tk.Label(kok, text="", bg=ARKA, fg=SOLUK, font=("Segoe UI", 9), wraplength=300,
                            height=2, anchor="nw", justify="left")
        self.son.pack(fill="x", padx=18, pady=(2, 4))
        alt = tk.Frame(kok, bg=ARKA)
        alt.pack(fill="x", padx=18, pady=(0, 12))
        tk.Label(alt, text="F9 konuş  ·  Shift+F9 bu panel", bg=ARKA, fg=SOLUK,
                 font=("Segoe UI", 8)).pack(side="left")
        cik = tk.Label(alt, text="⏻  Uygulamadan çık", bg=ARKA, fg=SOLUK, font=("Segoe UI", 8), cursor="hand2")
        cik.pack(side="right")
        cik.bind("<Enter>", lambda e: cik.config(fg=KAYIT))
        cik.bind("<Leave>", lambda e: cik.config(fg=SOLUK))
        cik.bind("<Button-1>", lambda e: kok.destroy())  # ✕ yalnızca paneli gizler; bu tamamen kapatır
        self.enter_btn.bind("<Button-1>", lambda e: self.enter_degis())
        self.enter_degis(ilk=True)

        self.mini_kur()
        self.yenile()
        threading.Thread(target=self.hazirla, daemon=True).start()
        threading.Thread(target=self.kisayol_dinle, daemon=True).start()
        self.ses_no, self.caliyor, self.bekle = 0, False, False
        self.cevap_zaman = self.dosya_zamani()  # açılıştan önceki cevabı okuma
        self.cevap_bak()
        self.ciz()

    # --- küçük mikrofon ve baloncuk ---
    def mini_kur(self):
        # damla Tk ile değil, piksel başına saydam katman olarak çizilir (katmana_bas)
        self.mini = tk.Toplevel(self.kok, cursor="hand2")
        self.mini.title("Sesli Komut")
        self.mini.overrideredirect(True)
        self.mini.attributes("-topmost", True)
        self.m_anahtar = None
        self.cek = self.kay = 0.0          # mıknatıs efektinin yumuşatılmış hâli
        self.surukleniyor = False
        self.mini.bind("<Button-1>", self.tut)
        self.mini.bind("<B1-Motion>", self.surukle)
        self.mini.bind("<ButtonRelease-1>", self.birak)
        self.mini.bind("<Button-3>", lambda e: self.panel_degis())  # sağ tık: ayarlar

        try:
            with open(KONUM_DOSYA, encoding="utf-8") as f:
                kenar, poz = f.read().split()
            if kenar not in KENARLAR:
                raise ValueError(kenar)
            self.kenar, poz = kenar, int(poz)
        except (OSError, ValueError):  # eski biçim ya da yok: sağ kenarın ortası
            self.kenar, poz = "sag", (calisma_alani()[1] + calisma_alani()[3] - L) // 2
        self.damla_yerles(poz)

        self.balon = tk.Toplevel(self.kok, bg=KART)
        self.balon.title("Sesli Komut")
        self.balon.overrideredirect(True)
        self.balon.attributes("-topmost", True)
        self.balon_yazi = tk.Label(self.balon, bg=KART, fg=YAZI, font=("Segoe UI", 10),
                                   padx=12, pady=6, wraplength=260, justify="left")
        self.balon_yazi.pack()
        # Claude cevabı gelince damlanın yanında beliren cam ▶ topu (damla gibi katman olarak çizilir)
        self.oynat = tk.Toplevel(self.kok, cursor="hand2")
        self.oynat.title("Sesli Komut")
        self.oynat.overrideredirect(True)
        self.oynat.attributes("-topmost", True)
        self.oynat.geometry(f"{OW}x{OW}+0+0")
        self.oynat.bind("<ButtonRelease-1>", lambda e: self.oku())
        self.oynat_acik, self.oynat_zaman, self.okx, self.oky = False, None, 0.0, 0.0
        self.kok.update()
        odak_alma(self.mini)
        odak_alma(self.balon)
        odak_alma(self.oynat)
        for h in (kabuk(self.mini), kabuk(self.oynat)):
            win32gui.SetWindowLong(h, win32con.GWL_EXSTYLE,
                                   win32gui.GetWindowLong(h, win32con.GWL_EXSTYLE) | win32con.WS_EX_LAYERED)
        # SW_HIDE'dan sonra Windows bu pencereye çizimi reddediyor (hata 87); gizlemek = tamamen saydam kare
        katmana_bas(kabuk(self.oynat), Image.new("RGBA", (OW, OW)))
        self.balon.attributes("-alpha", 0.95)
        cam_kart(self.balon)
        win32gui.ShowWindow(kabuk(self.balon), win32con.SW_HIDE)
        self.balon_zaman = None

    def damla_yerles(self, poz):
        # shortcut: yalnızca ana ekranın kenarlarına yapışır; ikinci monitör gerekirse genişlet
        if self.kenar != getattr(self, "onceki_kenar", None):  # kenar/köşe değişti: yeni yerinde büyüyerek belir
            self.onceki_kenar, self.anim = self.kenar, time.time()
        w, h = boyut(self.kenar)
        self.dw, self.dh = w, h
        self.dcx, self.dcy = merkez(self.kenar)
        sol, ust, sag, alt = calisma_alani()
        if "-" in self.kenar:
            self.poz = poz
            x = sag - w if self.kenar.startswith("sag") else sol
            y = alt - h if self.kenar.endswith("alt") else ust
        elif self.kenar in ("sag", "sol"):
            self.poz = max(ust, min(poz, alt - h))
            x, y = (sag - w if self.kenar == "sag" else sol), self.poz
        else:
            self.poz = max(sol, min(poz, sag - w))
            x, y = self.poz, (alt - h if self.kenar == "alt" else ust)
        self.mini.geometry(f"{w}x{h}+{x}+{y}")

    def yanina(self, g, y_, bosluk):
        # paneli/baloncuğu damlanın ekrana bakan tarafına koyar
        mx, my = self.mini.winfo_x(), self.mini.winfo_y()
        cx, cy = mx + round(self.dcx), my + round(self.dcy)  # Tk geometrisi tam sayı ister ("+40+179.0" hata verir)
        # mesafe pencereden değil damlanın gövdesinden ölçülür (pencerede mıknatıs için saydam pay var)
        if "-" in self.kenar:  # köşede: çeyrek dairenin orta yüksekliğinde, kavisin hemen yanında
            r = R * 0.95
            x = mx + self.dw - round(r * 0.87) - g - bosluk if self.kenar.startswith("sag") \
                else mx + round(r * 0.87) + bosluk
            y = my + self.dh - round(r / 2) - y_ // 2 if self.kenar.endswith("alt") else my + round(r / 2) - y_ // 2
            return ekrana_sigdir(x, y, g, y_)
        ic = D - 2  # duran damlanın kenardan derinliği
        x, y = {"sag": (mx + self.dw - ic - g - bosluk, cy - y_ // 2), "sol": (mx + ic + bosluk, cy - y_ // 2),
                "ust": (cx - g // 2, my + ic + bosluk),
                "alt": (cx - g // 2, my + self.dh - ic - y_ - bosluk)}[self.kenar]
        return ekrana_sigdir(x, y, g, y_)

    def tut(self, e):
        self.tutma = (e.x_root, e.y_root, self.mini.winfo_x(), self.mini.winfo_y())
        self.surukleniyor = False

    def surukle(self, e):
        fx, fy = e.x_root - self.tutma[0], e.y_root - self.tutma[1]
        if abs(fx) + abs(fy) > 4:
            if not self.surukleniyor and self.kok.state() == "normal":
                self.kok.withdraw()  # panel eski yerinde asılı kalmasın
            self.surukleniyor = True
        if self.surukleniyor:
            # köşeye yakınsa köşeye, değilse en yakın kenara yapışır, imleci ortalar
            sol, ust, sag, alt = calisma_alani()
            px, py = e.x_root, e.y_root
            yatay = "sol" if px - sol < sag - px else "sag"
            dikey = "ust" if py - ust < alt - py else "alt"
            if min(px - sol, sag - px) < KOSE_YAKIN and min(py - ust, alt - py) < KOSE_YAKIN:
                self.kenar = f"{yatay}-{dikey}"
            else:
                self.kenar = min((px - sol, "sol"), (sag - px, "sag"), (py - ust, "ust"), (alt - py, "alt"))[1]
            self.damla_yerles((py if self.kenar in ("sag", "sol") else px) - L // 2)
            self.balon_yerlestir()
            if self.oynat_acik:
                self.oynat_yerlestir()

    def birak(self, e):
        if not self.surukleniyor:
            return self.bas()
        self.surukleniyor = False  # mıknatıs efekti yeniden açılsın
        try:
            with open(KONUM_DOSYA, "w", encoding="utf-8") as f:
                f.write(f"{self.kenar} {self.poz}")
        except OSError:
            pass  # konum kaydedilemezse bir sonraki açılış varsayılan köşeden başlar

    def panel_degis(self):
        if self.kok.state() == "normal":
            return self.kok.withdraw()
        self.balon_gizle()
        self.oynat_gizle()  # panel topun üstüne açılır
        self.kok.update_idletasks()
        g, y_ = self.kok.winfo_reqwidth(), self.kok.winfo_reqheight()
        x, y = self.yanina(g, y_, 10)
        self.kok.geometry(f"+{x}+{y}")
        self.kok.deiconify()
        self.kok.lift()

    def balon_yerlestir(self):
        self.balon.update_idletasks()
        g, y_ = self.balon.winfo_reqwidth(), self.balon.winfo_reqheight()
        x, y = self.yanina(g, y_, EK + 3)  # EK+3: damla fareyle uzayınca da üstüne binmesin
        self.balon.geometry(f"{g}x{y_}+{x}+{y}")
        # SW_HIDE ile gizlendikten sonra Tk konumu uygulamıyor (baloncuk ilk yerinde takılıyordu); Windows'a doğrudan ver
        win32gui.SetWindowPos(kabuk(self.balon), win32con.HWND_TOPMOST, x, y, g, y_, win32con.SWP_NOACTIVATE)

    def oynat_yerlestir(self):
        # damlanın ekrana bakan yanında, biraz yukarıda (durum baloncuğuyla çakışmasın)
        x, y = self.yanina(OW, OW, EK + 3 - (OW - 2 * OR) // 2)
        if self.kenar in ("sag", "sol"):
            y -= L // 2 + 2
        elif self.kenar in ("ust", "alt"):
            x -= L // 2 + 2
        x, y = ekrana_sigdir(x, y, OW, OW)
        win32gui.SetWindowPos(kabuk(self.oynat), win32con.HWND_TOPMOST, x, y, OW, OW, win32con.SWP_NOACTIVATE)

    def oynat_goster(self, sure=30000):
        self.oynat_zaman_iptal()
        if not self.oynat_acik:
            self.oynat_acik, self.oynat_t0 = True, time.time()
            self.oynat_yerlestir()
            self.oynat_ciz()
        if not self.caliyor or self.bekle:
            self.oynat_zaman = self.kok.after(sure, self.oynat_gizle)

    def oynat_zaman_iptal(self):
        if self.oynat_zaman:
            self.kok.after_cancel(self.oynat_zaman)
            self.oynat_zaman = None

    def oynat_gizle(self):
        self.oynat_zaman_iptal()
        if self.bekle:  # ▶'ye basılmadan kapandı: hazırlanan sesi at
            self.dur()
        self.oynat_acik = False
        katmana_bas(kabuk(self.oynat), Image.new("RGBA", (OW, OW)))  # saydam pikseller tıklamayı da geçirir

    def oynat_ciz(self):
        t = time.time()
        if self.caliyor and not self.bekle:
            durum = "caliyor" if self.ses_yol else "hazirlaniyor"
        else:
            durum = "hazir"
        try:
            px, py = win32api.GetCursorPos()
        except Exception:  # kilit ekranında imleç okunamaz
            px, py = -10 ** 6, -10 ** 6
        cx, cy = self.oynat.winfo_rootx() + OW / 2, self.oynat.winfo_rooty() + OW / 2
        uzak = float(np.hypot(px - cx, py - cy))
        # mıknatıs: fare yaklaşınca top ona doğru birkaç piksel kayar
        g = max(0.0, 1 - uzak / 130) * 3.5 / max(uzak, 1)
        self.okx += ((px - cx) * g - self.okx) * 0.25
        self.oky += ((py - cy) * g - self.oky) * 0.25
        im = oynat_resmi(OYNAT_STIL, durum, t, belirme((t - self.oynat_t0) / 0.45), self.okx, self.oky, uzak < OR + 3)
        katmana_bas(kabuk(self.oynat), im)

    def balon_goster(self, metin, sure=2500):
        if self.balon_zaman:
            self.kok.after_cancel(self.balon_zaman)
            self.balon_zaman = None
        if self.kok.state() == "normal":  # panel açıkken durum zaten orada yazıyor
            return
        self.balon_yazi.config(text=metin)
        self.balon_yerlestir()
        win32gui.ShowWindow(kabuk(self.balon), win32con.SW_SHOWNOACTIVATE)
        if not (self.kayitta or self.mesgul):
            self.balon_zaman = self.kok.after(sure, self.balon_gizle)

    # --- Claude cevabını sesli okuma ---
    def dosya_zamani(self):
        try:
            return os.path.getmtime(CEVAP_DOSYA)
        except OSError:
            return 0

    def cevap_bak(self):
        # cevap_kaydet.py (Claude Code Stop hook'u) dosyayı her cevapta yeniler
        z = self.dosya_zamani()
        if z != self.cevap_zaman:
            self.cevap_zaman = z
            self.dur()
            self.oku(bekle=not self.seslendir)  # okuma kapalıysa da sesi önceden hazırla: ▶ anında çalsın
            self.oynat_goster()
        self.kok.after(700, self.cevap_bak)

    def seslendir_degis(self, ilk=False):
        if not ilk:
            self.seslendir = not self.seslendir
            try:
                if self.seslendir:
                    open(SESLENDIR_DOSYA, "w").close()
                else:
                    os.remove(SESLENDIR_DOSYA)
            except OSError:
                pass  # ayar kaydedilemezse yalnızca bu açılışta geçerli
        self.ses_btn.config(text=("●  Cevabı sesli oku" if self.seslendir else "○  Cevabı okuma (▶ ile dinle)"),
                            fg=YAZI if self.seslendir else SOLUK)

    def oku(self, bekle=False):
        if self.caliyor:
            if self.bekle:  # ses önceden hazırlanmıştı: ▶ ile başlat
                self.bekle = False
                return self.oynat_zaman_iptal()
            return self.dur()  # okurken ya da hazırlanırken tıklama: durdur
        try:
            with open(CEVAP_DOSYA, encoding="utf-8") as f:
                metin = okunur(f.read())
        except OSError:
            return
        if not metin:
            return
        self.ses_no += 1
        self.caliyor, self.bekle = True, bekle
        if not bekle:
            self.oynat_zaman_iptal()
        self.sira, self.ses_yol = [], None
        threading.Thread(target=self.ses_uret, args=(metin, self.ses_no), daemon=True).start()
        self.cal_bak(self.ses_no)

    def ses_uret(self, metin, no):
        # ilk cümle tek başına üretilir ki okuma hemen başlasın; gerisi o çalarken hazırlanır
        import asyncio
        import edge_tts
        for i, parca in enumerate(parcala(metin)):
            if no != self.ses_no:
                return
            yol = os.path.join(os.environ.get("TEMP", "."), f"sesli_komut_{no}_{i}.mp3")
            try:
                asyncio.run(edge_tts.Communicate(parca, SES, rate=OKUMA_HIZI).save(yol))
            except Exception:
                if i == 0:
                    return self.kok.after(0, self.ses_hata, no)
                break  # ortada koparsa okunan kadarıyla bitir
            self.kok.after(0, self.sira_ekle, yol, no)
        self.kok.after(0, self.sira_ekle, None, no)  # None: üretim bitti

    def sira_ekle(self, yol, no):
        if no == self.ses_no:
            self.sira.append(yol)
        elif yol:  # bu arada durduruldu ya da yeni cevap geldi
            sil(yol)

    def ses_hata(self, no):
        if no == self.ses_no:
            self.dur()
            self.oynat_gizle()
            self.balon_goster("Seslendirilemedi (internet?)", sure=5000)

    def cal_bak(self, no):
        # MCI: Windows'un kendi oynatıcısı; ek paket gerekmez, açma/çalma/kapama hep bu (Tk) iş parçacığında
        if no != self.ses_no:
            return
        b = ctypes.create_unicode_buffer(32)
        _mci("status cevap mode", b, 32, None)
        if b.value != "playing" and not self.bekle:
            if self.ses_yol:
                _mci("close cevap", None, 0, None)
                sil(self.ses_yol)
                self.ses_yol = None
            if self.sira:
                yol = self.sira.pop(0)
                if yol is None:
                    self.dur()
                    self.oynat_zaman = self.kok.after(4000, self.oynat_gizle)
                    return
                _mci(f'open "{yol}" type mpegvideo alias cevap', None, 0, None)
                _mci("play cevap", None, 0, None)
                self.ses_yol = yol
        self.kok.after(120, self.cal_bak, no)

    def dur(self):
        self.ses_no += 1
        self.caliyor = self.bekle = False
        _mci("close cevap", None, 0, None)
        for yol in [getattr(self, "ses_yol", None)] + getattr(self, "sira", []):
            if yol:
                sil(yol)
        self.ses_yol, self.sira = None, []

    def balon_gizle(self):
        win32gui.ShowWindow(kabuk(self.balon), win32con.SW_HIDE)

    def kisayol_dinle(self):
        u = ctypes.windll.user32
        if not u.RegisterHotKey(None, 1, MOD_NOREPEAT, VK_F9):
            return self.yaz_durum("F9 başka program tarafından kullanılıyor.")
        m = wintypes.MSG()
        u.RegisterHotKey(None, 2, MOD_NOREPEAT | MOD_SHIFT, VK_F9)  # Shift+F9: ayarlar
        while u.GetMessageW(ctypes.byref(m), None, 0, 0) > 0:
            self.kok.after(0, self.panel_degis if m.wParam == 2 else self.bas)

    # --- arayüz ---
    def enter_degis(self, ilk=False):
        if not ilk:
            self.enter = not self.enter
        self.enter_btn.config(text=("●  Otomatik gönder (Enter)" if self.enter else "○  Sadece yaz, gönderme"),
                              fg=YAZI if self.enter else SOLUK)

    def ciz(self):
        # damlada: dinlerken sese göre oynayan çizgiler, çözerken yavaş dalga
        renk = KAYIT if self.kayitta else ISLEM if self.mesgul else VURGU
        cubuklar = None
        if self.kayitta or self.mesgul:
            g = max(0.2, min(1.0, self.anlik / max(self.esik, 1) / 3)) if self.kayitta else 0.35
            t = time.time() * (10 if self.kayitta else 4)
            cubuklar = tuple(2 + g * 8 * (0.55 + 0.45 * np.sin(t + i * 1.9)) for i in range(4))
        olcek = 1.0
        if self.anim is not None:  # 0.25 sn'de yumuşak büyüme (ease-out), 8 adımlı önbellekle
            p = min(1.0, (time.time() - self.anim) / 0.25)
            olcek = max(0.125, round((1 - (1 - p) ** 3) * 8) / 8)
            if p >= 1:
                self.anim = None
        # mıknatıs: fare yaklaştıkça damla ona doğru uzar, bombe farenin hizasına kayar
        try:
            px, py = win32api.GetCursorPos()
        except Exception:  # kilit ekranında imleç okunamaz
            px, py = -10 ** 6, -10 ** 6
        mx, my = self.mini.winfo_rootx(), self.mini.winfo_rooty()
        cx, cy = mx + self.dcx, my + self.dcy
        yakin = 0.0 if self.surukleniyor else min(1.0, max(0.0, 1 - (np.hypot(px - cx, py - cy) - 25) / CEKIM_MESAFE))
        boyuna = (py - cy) if self.kenar in ("sag", "sol") else (px - cx)
        hedef_kay = 0.0 if "-" in self.kenar else max(-1.0, min(1.0, boyuna / (L / 2))) * yakin
        self.cek += (yakin - self.cek) * 0.3
        self.kay += (hedef_kay - self.kay) * 0.3
        cekim, kayma = round(self.cek * 6) / 6, round(self.kay * 4) / 4
        oynuyor = abs(yakin - self.cek) > 0.02 or abs(hedef_kay - self.kay) > 0.02
        anahtar = (self.kenar, renk, cubuklar, olcek, cekim, kayma)
        if anahtar != self.m_anahtar:  # boştayken her karede değil, değişince bas
            self.m_anahtar = anahtar
            katmana_bas(kabuk(self.mini), damla_resmi(self.kenar, renk, cubuklar, olcek, cekim, kayma))
        if self.oynat_acik:
            self.oynat_ciz()
        self.kok.after(16 if self.anim or oynuyor else 33 if self.oynat_acik else 40, self.ciz)

    def yaz_durum(self, metin, son=None):
        self.kok.after(0, self.durum_goster, metin, son)

    def durum_goster(self, metin, son):
        self.durum.config(text=metin)
        if son is not None:
            self.son.config(text=son)
        self.balon_goster(metin.split(" · ")[0])

    def yenile(self):
        onceki = self.liste[self.secim.current()][1] if getattr(self, "liste", None) and self.secim.current() >= 0 else None
        self.liste = [p for p in pencereler() if p[0] != "Sesli Komut"]
        self.secim["values"] = [b for b, _ in self.liste]

        def bul(kosul):
            return next((i for i, (b, h) in enumerate(self.liste) if kosul(b.lower(), h)), None)
        # sıra: elle seçilen pencere hâlâ açıksa o → VS Code → terminal/Claude → ilk pencere
        secilen = bul(lambda b, h: h == onceki)
        if secilen is None:
            secilen = bul(lambda b, h: b.endswith("visual studio code"))
        if secilen is None:
            secilen = bul(lambda b, h: any(k in b for k in ("claude", "terminal", "powershell", "✳")))
        if self.liste:
            self.secim.current(secilen or 0)

    # --- iş ---
    def hazirla(self):
        with sr.Microphone(sample_rate=HIZ, chunk_size=PARCA) as mik:  # gürültü eşiği bir kez ölçülür
            olcum = [seviye(mik.stream.read(PARCA)) for _ in range(int(0.5 * HIZ / PARCA))]
        self.esik = max(300.0, float(np.mean(olcum)) * 2.5)
        self.cozucu.yukle()
        self.kok.after(0, self.motor.config, {"text": self.cozucu.ad})
        self.yaz_durum("Hazır. F9'a bas ya da mikrofona tıkla.")

    def bas(self):
        if self.kayitta:
            self.kayitta = False  # ikinci tıklama: hemen bitir
        elif not self.mesgul and self.cozucu.ad != "yükleniyor":
            if self.secim.current() < 0:
                return self.yaz_durum("Önce bir pencere seç.")
            self.hedef = self.liste[self.secim.current()][1]
            if self.caliyor:
                self.dur()  # mikrofon okunan cevabı duymasın
            self.oynat_gizle()
            self.kayitta = True
            threading.Thread(target=self.dinle, daemon=True).start()
        elif self.cozucu.ad == "yükleniyor":
            self.yaz_durum("Motor hazırlanıyor…")

    def dinle(self):
        self.yaz_durum("Dinliyorum…", "")
        try:
            parcalar, konustu, sessiz, bas = [], False, 0.0, time.time()
            self.en_uzun = 0.0  # konuşma arasındaki en uzun duraklama; SESSIZLIK_SN ayarı için gösterilir
            with sr.Microphone(sample_rate=HIZ, chunk_size=PARCA) as mik:
                while self.kayitta and time.time() - bas < EN_UZUN_SN:
                    veri = mik.stream.read(PARCA)
                    parcalar.append(veri)
                    self.anlik = seviye(veri)
                    if self.anlik > self.esik:
                        if konustu:
                            self.en_uzun = max(self.en_uzun, sessiz)
                        konustu, sessiz = True, 0.0
                    else:
                        sessiz += PARCA / HIZ
                    if konustu and sessiz > SESSIZLIK_SN:
                        break
                    if not konustu and time.time() - bas > 8:
                        break
        except Exception as e:
            self.kayitta = False
            return self.yaz_durum(f"Mikrofon hatası: {e}")
        self.kayitta, self.mesgul = False, True
        if not konustu:
            self.mesgul = False
            return self.yaz_durum("Ses gelmedi.")
        self.yaz_durum("Çözümlüyorum…")
        try:
            t = time.time()
            metin = self.cozucu.coz(b"".join(parcalar))
            sure = time.time() - t
        except sr.UnknownValueError:
            metin = ""
        except Exception as e:
            self.mesgul = False
            return self.yaz_durum(f"Hata: {e}")
        if not metin:
            self.mesgul = False
            return self.yaz_durum("Anlayamadım, tekrar dene.")
        self.kok.after(0, self.gonder, metin, sure)

    def gonder(self, metin, sure):
        hwnd = self.hedef
        if not win32gui.IsWindow(hwnd):
            self.mesgul = False
            return self.yaz_durum("Pencere kapanmış, listeyi yenile.")
        # Pano + Ctrl+V: Türkçe karakterler tuş tuş yazmaktan daha güvenilir geçer
        self.kok.clipboard_clear()
        self.kok.clipboard_append(metin)
        self.kok.update()
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        if win32gui.GetForegroundWindow() != hwnd:
            # Windows arka plandaki programın öne pencere getirmesini engeller; Alt dokunuşu izni açar
            tus(win32con.VK_MENU); tus(win32con.VK_MENU, True)
            try:
                win32gui.SetForegroundWindow(hwnd)
            except win32gui.error:
                self.mesgul = False
                return self.yaz_durum("Pencere öne alınamadı, metin panoda.", metin)
        self.kok.after(120, self.yapistir, metin, sure)

    def yapistir(self, metin, sure):
        tus(win32con.VK_CONTROL); tus(VK_V); tus(VK_V, True); tus(win32con.VK_CONTROL, True)
        if self.enter:
            self.kok.after(120, lambda: (tus(win32con.VK_RETURN), tus(win32con.VK_RETURN, True)))
        self.mesgul = False
        self.yaz_durum(f"Yazıldı · {sure:.1f} sn · en uzun duraklama {self.en_uzun:.1f} sn", metin)


if __name__ == "__main__":
    ctypes.windll.shcore.SetProcessDpiAwareness(2)  # ölçekli ekranda bulanık görünmesin
    _tek = ctypes.windll.kernel32.CreateMutexW(None, False, "SesliKomut.TekKopya")
    if ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS: zaten açık, ikinci damla olmasın
        sys.exit(0)
    kok = tk.Tk()
    Uygulama(kok)
    kok.mainloop()
