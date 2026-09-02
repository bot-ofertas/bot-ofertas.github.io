# -*- coding: utf-8 -*-
"""
Compatibilidade: reescrita de título via IA.

A implementação real vive em `core/ai_rewriter.py` — este módulo virou um
encaminhamento fino porque mantinha uma segunda cópia da mesma lógica
(cliente Anthropic, chave do .env, prompt de reescrita) sem cache, sem
timeout e sem limpar URL nenhuma da resposta do modelo (Regra 1: preferir
estender a duplicar). `bot_ofertas.py` importa daqui e segue funcionando
sem mudança.
"""
from __future__ import annotations

from core.ai_rewriter import reescrever_titulo  # noqa: F401

__all__ = ["reescrever_titulo"]
