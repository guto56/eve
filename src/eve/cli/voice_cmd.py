"""Comandos de voz."""

from __future__ import annotations

import asyncio
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console

from eve.config import load_settings
from eve.paths import paths
from eve.secrets import build_store
from eve.voice.stt import SpeechToText
from eve.voice.tts import TextToSpeech

console = Console()
err_console = Console(stderr=True)

voice_app = typer.Typer(name="voice", help="Voz da EVE.", no_args_is_help=True)


def _clients() -> tuple[TextToSpeech, str]:
    settings = load_settings()
    store = build_store(paths().ensure().home / "secrets.json")
    try:
        tts = TextToSpeech(
            store.get("CARTESIA_API_KEY") or "",
            store.get("CARTESIA_VOICE_ID") or "",
            model=settings.voice.tts_model,
            language=settings.voice.tts_language,
            sample_rate=settings.voice.output_sample_rate,
        )
    except ValueError as exc:
        err_console.print(f"[red]{exc}[/red] — use [bold]eve key set CARTESIA_API_KEY[/bold]")
        raise typer.Exit(1) from None
    return tts, store.get("DEEPGRAM_API_KEY") or ""


@voice_app.command("say")
def say(
    text: str,
    play: Annotated[bool, typer.Option("--play/--no-play", help="Tocar o áudio.")] = True,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Salvar em WAV.")] = None,
) -> None:
    """Faz a EVE falar um texto."""
    tts, _ = _clients()

    async def gerar() -> tuple[bytes, float]:
        inicio = time.perf_counter()
        primeiro: float | None = None
        pedacos = []
        async for pedaco in tts.stream(text):
            if primeiro is None:
                primeiro = (time.perf_counter() - inicio) * 1000
            pedacos.append(pedaco)
        await tts.aclose()
        return b"".join(pedacos), primeiro or 0.0

    audio, primeiro = asyncio.run(gerar())
    if not audio:
        err_console.print("[red]Nada foi gerado.[/red]")
        raise typer.Exit(1)

    destino = out or Path(tempfile.gettempdir()) / "eve-fala.wav"
    destino.write_bytes(tts.wav_header(len(audio)) + audio)
    console.print(
        f"[dim]{len(audio) / 2 / tts.sample_rate:.1f}s de fala · "
        f"primeiro áudio em {primeiro:.0f} ms · {destino}[/dim]"
    )
    if play:
        subprocess.run(["/usr/bin/afplay", str(destino)], check=False)


@voice_app.command("test")
def test(
    text: Annotated[str, typer.Argument()] = "Abra o Safari e me diga que horas são.",
) -> None:
    """Diagnóstico: a EVE fala uma frase e transcreve o próprio áudio.

    Valida as duas pontas — Cartesia e Deepgram — sem precisar de microfone.
    """
    settings = load_settings()
    tts, deepgram_key = _clients()
    if not deepgram_key:
        err_console.print("[red]DEEPGRAM_API_KEY não configurada.[/red]")
        raise typer.Exit(1)

    async def roundtrip() -> tuple[bytes, list[str], float, float]:
        inicio = time.perf_counter()
        # A transcrição precisa da mesma taxa de amostragem da entrada.
        tts.sample_rate = settings.voice.input_sample_rate
        audio = await tts.synthesize(text)
        tempo_fala = (time.perf_counter() - inicio) * 1000
        await tts.aclose()

        inicio = time.perf_counter()
        stt = SpeechToText(
            deepgram_key,
            model=settings.voice.stt_model,
            language=settings.voice.stt_language,
            sample_rate=settings.voice.input_sample_rate,
            endpointing_ms=settings.voice.endpointing_ms,
        )
        finais: list[str] = []
        async with stt:

            async def enviar() -> None:
                passo = settings.voice.input_sample_rate // 5  # 100 ms
                for i in range(0, len(audio), passo):
                    await stt.send_audio(audio[i : i + passo])
                    await asyncio.sleep(0.02)
                await stt.finish()

            async def receber() -> None:
                async for t in stt.transcripts():
                    if t.is_final and t.usable:
                        finais.append(t.text)

            await asyncio.gather(enviar(), receber())
        return audio, finais, tempo_fala, (time.perf_counter() - inicio) * 1000

    audio, finais, ms_fala, ms_stt = asyncio.run(roundtrip())
    console.print(f"[dim]dito     :[/dim] {text}")
    console.print(f"[dim]ouvido   :[/dim] {' '.join(finais) or '[red](nada)[/red]'}")
    console.print(
        f"[dim]{len(audio) / 2 / settings.voice.input_sample_rate:.1f}s de áudio · "
        f"fala {ms_fala:.0f} ms · transcrição {ms_stt:.0f} ms[/dim]"
    )
    if not finais:
        raise typer.Exit(1)
