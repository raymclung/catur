"""
Keluarga pembukaan berdasarkan rentang kode ECO.

Dipakai untuk mengelompokkan partai yang cuma punya kode ECO tanpa nama
pembukaan (TWIC tidak menyertakan tag Opening). Rentangnya mengikuti
klasifikasi ECO standar; rentang khusus repertoire Ray (Ragozin, Catalan)
dipisah dari induknya supaya bisa disaring sendiri.
"""

from __future__ import annotations

# (kode_awal, kode_akhir, nama). Urutan penting: rentang yang lebih sempit
# ditaruh SEBELUM induknya (Ragozin sebelum QGD).
KELUARGA = [
    ("A00", "A09", "Pembukaan tidak umum / flank"),
    ("A10", "A39", "English Opening"),
    ("A40", "A44", "Queen's Pawn (lain-lain)"),
    ("A45", "A49", "Indian tanpa c4 (Trompowsky, London, dll.)"),
    ("A50", "A79", "Benoni / Budapest / Old Indian"),
    ("A80", "A99", "Dutch Defense"),
    ("B00", "B00", "King's Pawn tidak umum"),
    ("B01", "B01", "Scandinavian Defense"),
    ("B02", "B05", "Alekhine Defense"),
    ("B06", "B09", "Modern / Pirc Defense"),
    ("B10", "B19", "Caro-Kann Defense"),
    ("B20", "B99", "Sicilian Defense"),
    ("C00", "C19", "French Defense"),
    ("C20", "C29", "Open Game (lain-lain) / Vienna"),
    ("C30", "C39", "King's Gambit"),
    ("C40", "C43", "Petrov / Philidor / lain-lain"),
    ("C44", "C49", "Scotch / Four Knights / lain-lain"),
    ("C50", "C59", "Italian Game / Two Knights"),
    ("C60", "C99", "Ruy Lopez"),
    ("D00", "D05", "Queen's Pawn (London, Colle, dll.)"),
    ("D06", "D09", "Queen's Gambit (lain-lain)"),
    ("D10", "D19", "Slav Defense"),
    ("D20", "D29", "Queen's Gambit Accepted"),
    ("D38", "D39", "Ragozin Defense"),
    ("D30", "D69", "Queen's Gambit Declined"),
    ("D70", "D99", "Grünfeld Defense"),
    ("E00", "E09", "Catalan Opening"),
    ("E10", "E19", "Queen's Indian / Bogo-Indian"),
    ("E20", "E59", "Nimzo-Indian Defense"),
    ("E60", "E99", "King's Indian Defense"),
]

# Repertoire Ray, dipakai untuk tombol saringan cepat di halaman daftar.
REPERTOIRE = [
    ("English Opening", "A10", "A39"),
    ("Catalan", "E00", "E09"),
    ("Ragozin", "D38", "D39"),
    ("Sicilian", "B20", "B99"),
]


def keluarga_eco(kode: str | None) -> str | None:
    if not kode or len(kode) < 3:
        return None
    k = kode[:3].upper()
    for awal, akhir, nama in KELUARGA:
        if awal <= k <= akhir:
            return nama
    return None
