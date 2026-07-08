# -*- coding: utf-8 -*-
"""
FOTO CACHE — evita rebaixar a mesma imagem várias vezes.

Cache local em data/foto_cache/ indexado por hash MD5 da URL.
TTL de 24h. Limpa automaticamente arquivos antigos.
"""
from __future__ import annotations

import hashlib
import io
import logging
import os
import time
from typing import Optional

log = logging.getLogger("foto_cache")

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_DIR = os.path.join(_BASE, "data", "foto_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

_TTL_S = 24 * 3600  # 24h


def _cache_path(url: str) -> str:
    h = hashlib.md5(url.encode("utf-8")).hexdigest()
    return os.path.join(CACHE_DIR, f"{h}.jpg")


def get_ou_baixar(url: str, max_size: int = 900) -> Optional[str]:
    """Retorna caminho de foto local (cache ou baixada). None se falhar."""
    if not url or not url.startswith("http"):
        return None
    path = _cache_path(url)
    # Cache hit
    if os.path.exists(path):
        idade = time.time() - os.path.getmtime(path)
        if idade < _TTL_S:
            return path
    # Cache miss: baixa
    try:
        import requests  # noqa: PLC0415
        from PIL import Image  # noqa: PLC0415
        r = requests.get(url, timeout=10)
        if r.status_code != 200 or not r.content:
            return None
        img = Image.open(io.BytesIO(r.content)).convert("RGB")
        img.thumbnail((max_size, max_size))
        img.save(path, "JPEG", quality=85, optimize=True)
        return path
    except Exception as e:
        log.warning("baixar foto: %s", e)
        return None


def limpar_expiradas() -> int:
    """Remove fotos com mais de 24h. Retorna quantas removeu."""
    agora = time.time()
    removidas = 0
    try:
        for nome in os.listdir(CACHE_DIR):
            f = os.path.join(CACHE_DIR, nome)
            try:
                if agora - os.path.getmtime(f) > _TTL_S:
                    os.remove(f)
                    removidas += 1
            except OSError:
                pass
    except FileNotFoundError:
        pass
    return removidas


def stats() -> dict:
    """Estatísticas do cache."""
    try:
        arquivos = os.listdir(CACHE_DIR)
        total_bytes = sum(
            os.path.getsize(os.path.join(CACHE_DIR, f))
            for f in arquivos if os.path.isfile(os.path.join(CACHE_DIR, f))
        )
        return {
            "arquivos": len(arquivos),
            "tamanho_mb": round(total_bytes / 1_000_000, 2),
        }
    except Exception:
        return {"arquivos": 0, "tamanho_mb": 0}
