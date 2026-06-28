# -*- coding: utf-8 -*-
"""
Gerador do banner "ALERTA CUPOM" para posts do Telegram.
Usa Pillow — gera o PNG na primeira chamada e reutiliza nas seguintes.

Uso:
    from core.banner_cupom import banner_bytes  # retorna bytes para send_photo
    python -m core.banner_cupom                 # gera assets/banner_cupom.png
"""
from __future__ import annotations
import io
import os

# Caminho padrão do banner estático (gerado uma única vez e reutilizado)
_ASSETS = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets")
BANNER_PATH = os.path.join(_ASSETS, "banner_cupom.png")

# Cores do design
_BG      = (13,  17,  23)   # fundo escuro azul-noite
_ORANGE  = (255, 107,   0)  # laranja principal
_ORANGE2 = (255, 140,   0)  # laranja claro (texto)
_WHITE   = (255, 255, 255)
_GRAY    = (136, 136, 136)
_DARK_G  = ( 85,  85,  85)


def _load_font(size: int):
    """Tenta carregar uma fonte do sistema; cai para bitmap default."""
    from PIL import ImageFont
    candidates = [
        # Ubuntu / GitHub Actions
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/usr/share/fonts/truetype/ubuntu/Ubuntu-B.ttf",
        "/usr/share/fonts/truetype/freefont/FreeSansBold.ttf",
        # Windows
        r"C:\Windows\Fonts\arialbd.ttf",
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibrib.ttf",
        # macOS
        "/Library/Fonts/Arial Bold.ttf",
        "/System/Library/Fonts/Helvetica.ttc",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    # Pillow 10.1+: load_default aceita size
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def _draw_text_center(draw, W: int, y: int, text: str, font, fill) -> None:
    """Centraliza texto horizontalmente em y (compatível com bitmap e TrueType)."""
    try:
        draw.text((W // 2, y), text, fill=fill, font=font, anchor="mm")
    except TypeError:
        try:
            from PIL import ImageDraw as _ID
            bbox = draw.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
        except Exception:
            tw, th = len(text) * 7, 12
        draw.text(((W - tw) // 2, y - th // 2), text, fill=fill, font=font)


def gerar_banner_cupom(output_path: str | None = None) -> str:
    """Gera o banner PNG e salva em output_path. Retorna o caminho salvo."""
    from PIL import Image, ImageDraw

    if output_path is None:
        output_path = BANNER_PATH

    W, H = 800, 420
    img = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    # ── Fundo degradê sutil ──────────────────────────────────────────────────
    for i in range(H):
        t = i / H
        r = int(_BG[0] + t * 18)
        g = int(_BG[1] + t * 8)
        b = int(_BG[2] + t * 28)
        draw.line([(0, i), (W, i)], fill=(min(r, 255), min(g, 255), min(b, 255)))

    # ── Barras de acento laranja ─────────────────────────────────────────────
    draw.rectangle([(0, 0),    (W, 10)],   fill=_ORANGE)
    draw.rectangle([(0, H-10), (W, H)],    fill=_ORANGE)
    draw.rectangle([(0, 10),   (8, H-10)], fill=_ORANGE2)
    draw.rectangle([(W-8, 10), (W, H-10)], fill=_ORANGE2)

    # ── Fonte por tamanho ────────────────────────────────────────────────────
    f90 = _load_font(90)
    f32 = _load_font(32)
    f22 = _load_font(22)

    # ── Textos principais ─────────────────────────────────────────────────────
    # Sombra sutil
    _draw_text_center(draw, W, 84,  "ALERTA", _load_font(90), (0, 0, 0))
    _draw_text_center(draw, W, 80,  "ALERTA", f90, _ORANGE2)

    _draw_text_center(draw, W, 182, "CUPOM",  _load_font(90), (0, 0, 0))
    _draw_text_center(draw, W, 178, "CUPOM",  f90, _WHITE)

    # ── Linha divisória ──────────────────────────────────────────────────────
    draw.line([(120, 232), (W-120, 232)], fill=(*_ORANGE, 80), width=2)

    # ── Badge "DESCONTOS EXCLUSIVOS" ─────────────────────────────────────────
    bx1, by1, bx2, by2 = 175, 252, W-175, 302
    draw.rounded_rectangle([(bx1, by1), (bx2, by2)], radius=24, fill=_ORANGE)
    _draw_text_center(draw, W, 277, "DESCONTOS EXCLUSIVOS", f32, _WHITE)

    # ── Branding ─────────────────────────────────────────────────────────────
    _draw_text_center(draw, W, 348, "Bot-Ofertas  •  Mercado Livre", f22, _GRAY)
    _draw_text_center(draw, W, 378, "Ofertas verificadas | Link oficial de afiliado", f22, _DARK_G)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    img.save(output_path, "PNG", optimize=True)
    return output_path


def banner_bytes() -> bytes:
    """Retorna o banner como bytes prontos para send_photo do Telegram."""
    if not os.path.exists(BANNER_PATH):
        try:
            gerar_banner_cupom(BANNER_PATH)
        except Exception:
            return b""  # sem Pillow instalado — caller faz fallback
    with open(BANNER_PATH, "rb") as f:
        return f.read()


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else BANNER_PATH
    path = gerar_banner_cupom(out)
    print(f"Banner gerado em: {path}")
