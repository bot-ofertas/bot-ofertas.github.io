# -*- coding: utf-8 -*-
"""
JANELA — horários do ciclo diário do PC, num lugar só.

O PC do Daniel liga às 08:30 e desliga (suspende) às 02:00. Esses dois
horários aparecem em cinco lugares diferentes: as tarefas do Agendador do
Windows (`agendar_shutdown.ps1`), o supervisor que garante o bot de pé
(`garantir_bot.py`), o watchdog no n8n, o relatório diário e o `status.ps1`.
Quando cada um carregava a sua própria cópia, mudar o horário significava
lembrar dos cinco — e esquecer de um deixava, por exemplo, o watchdog
alertando "bot caiu" toda madrugada às 02:30, porque o desligamento era
planejado e só ele não sabia disso.

Aqui os horários são lidos do `.env` (`HORA_LIGAR` / `HORA_DESLIGAR`) com
os valores acordados como padrão, e todo o resto pergunta a este módulo.

Uso na linha de comando (é como o PowerShell lê os mesmos valores):

    python -m core.janela --agenda    # JSON com os horários
    python -m core.janela --dentro    # exit 0 = dentro da janela, 1 = fora
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, time, timedelta

# Padrões acordados com o Daniel em 2026-08-29: liga 08:30, desliga 02:00.
HORA_LIGAR_PADRAO = "08:30"
HORA_DESLIGAR_PADRAO = "02:00"
# A verificação diária roda antes do desligamento para que o relatório saia
# com o dia inteiro contabilizado — 1h de folga cobre uma rodada em curso.
HORA_VERIFICACAO_PADRAO = "01:00"

# Depois do horário de religar, quanto tempo se espera até considerar que o
# despertar falhou. O bot leva ~1 min para subir e mandar o primeiro
# heartbeat; 45 min de folga evitam alarme por atraso de boot ou rede lenta.
MINUTOS_TOLERANCIA_RELIGAR = 45


def _hora(env: str, padrao: str) -> time:
    """Lê 'HH:MM' do ambiente. Valor inválido cai no padrão em vez de
    derrubar o processo: um `.env` com erro de digitação não pode impedir o
    bot de subir — ele só perde a personalização."""
    bruto = (os.getenv(env) or "").strip() or padrao
    try:
        h, m = bruto.split(":")
        return time(int(h), int(m))
    except (ValueError, TypeError):
        h, m = padrao.split(":")
        return time(int(h), int(m))


def hora_ligar() -> time:
    return _hora("HORA_LIGAR", HORA_LIGAR_PADRAO)


def hora_desligar() -> time:
    return _hora("HORA_DESLIGAR", HORA_DESLIGAR_PADRAO)


def hora_verificacao() -> time:
    return _hora("HORA_VERIFICACAO", HORA_VERIFICACAO_PADRAO)


def dentro_da_janela(agora: datetime | None = None) -> bool:
    """True quando o PC deveria estar ligado e publicando.

    A janela atravessa a meia-noite (08:30 → 02:00 do dia seguinte), então
    não dá para comparar com um simples `inicio <= t < fim`: às 23h o
    horário é maior que os dois extremos e a comparação ingênua diria
    "fora da janela" bem no meio do horário de pico.
    """
    agora = agora or datetime.now()
    t = agora.time()
    ini, fim = hora_ligar(), hora_desligar()
    if ini == fim:
        return True  # operação 24h
    if ini < fim:
        return ini <= t < fim
    return t >= ini or t < fim


def em_silencio(agora: datetime | None = None) -> bool:
    """Complemento de `dentro_da_janela`: o PC está desligado por decisão,
    não por falha. É o que impede o watchdog de gritar toda madrugada."""
    return not dentro_da_janela(agora)


def proxima_religada(agora: datetime | None = None) -> datetime:
    """Quando o PC deve voltar. Dentro da janela, é a religada de amanhã."""
    agora = agora or datetime.now()
    alvo = datetime.combine(agora.date(), hora_ligar())
    if alvo <= agora:
        alvo += timedelta(days=1)
    return alvo


def proximo_desligamento(agora: datetime | None = None) -> datetime:
    agora = agora or datetime.now()
    alvo = datetime.combine(agora.date(), hora_desligar())
    if alvo <= agora:
        alvo += timedelta(days=1)
    return alvo


def religada_atrasada(agora: datetime | None = None) -> bool:
    """True quando já passou do horário de religar (mais a tolerância) e
    ainda estamos no começo do dia — a assinatura de "o PC não acordou".

    Serve para o relatório da manhã: dentro da janela, mas sem sinal de
    vida logo depois do horário de religar, é o caso que precisa de alarme.
    """
    agora = agora or datetime.now()
    limite = datetime.combine(agora.date(), hora_ligar()) + timedelta(
        minutes=MINUTOS_TOLERANCIA_RELIGAR
    )
    return dentro_da_janela(agora) and limite <= agora < limite + timedelta(hours=2)


def agenda() -> dict:
    """Os horários prontos para quem não fala Python (o PowerShell lê isto)."""
    return {
        "ligar": hora_ligar().strftime("%H:%M"),
        "desligar": hora_desligar().strftime("%H:%M"),
        "verificacao": hora_verificacao().strftime("%H:%M"),
        "tolerancia_religar_min": MINUTOS_TOLERANCIA_RELIGAR,
    }


def resumo(agora: datetime | None = None) -> dict:
    """Estado atual da janela — vai para `/health` e para o relatório."""
    agora = agora or datetime.now()
    dentro = dentro_da_janela(agora)
    return {
        **agenda(),
        "dentro_da_janela": dentro,
        "em_silencio": not dentro,
        "proxima_religada": proxima_religada(agora).isoformat(timespec="minutes"),
        "proximo_desligamento": proximo_desligamento(agora).isoformat(timespec="minutes"),
    }


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv(
            os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
        )
    except Exception:
        pass

    if "--dentro" in sys.argv:
        sys.exit(0 if dentro_da_janela() else 1)
    if "--agenda" in sys.argv:
        print(json.dumps(agenda(), ensure_ascii=False))
        sys.exit(0)
    print(json.dumps(resumo(), ensure_ascii=False, indent=2))
