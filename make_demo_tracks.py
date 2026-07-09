"""Генерація 2 коротких демо-треків (WAV) для Focus OS.

Синтезуємо прості ambient-композиції (для демонстрації плеєра).
Не потребує зовнішніх залежностей окрім numpy.

Запуск:
    python make_demo_tracks.py
"""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import numpy as np

OUT_DIR = Path(__file__).parent / "static" / "tracks"
SR = 44100  # частота дискретизації


def write_wav(path: Path, samples: np.ndarray, sr: int = SR) -> None:
    """Записує моно 16-bit PCM WAV."""
    # нормалізуємо й конвертуємо в int16
    audio = np.clip(samples, -1.0, 1.0)
    audio = (audio * 32767).astype("<i2")
    with wave.open(str(path), "w") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(audio.tobytes())


def adsr(n: int, a: float, d: float, s: float, r: float, sr: int = SR) -> np.ndarray:
    """Проста ADSR-огинаюча для надання тону природнішого звучання.

    Усі фази масштабуються під фактичну довжину n, щоб уникнути
    виходу за межі масиву для коротких нот.
    """
    env = np.ones(n)
    na = min(int(a * sr), n)
    nd = min(int(d * sr), n - na)
    nr = min(int(r * sr), n - na - nd)
    ns = n - na - nd - nr
    if ns < 0:
        ns = 0
    # attack
    if na > 0:
        env[:na] = np.linspace(0, 1, na)
    # decay
    if nd > 0:
        env[na: na + nd] = np.linspace(1, s, nd)
    # sustain
    if ns > 0:
        env[na + nd: na + nd + ns] = s
    # release
    if nr > 0:
        env[na + nd + ns:] = np.linspace(s, 0, nr)
    return env


def note_freq(name: str) -> float:
    """Частота ноти за ім'ям (напр. 'A4', 'C#5')."""
    notes = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
    pitch = name[:-1]
    octave = int(name[-1])
    semitone = notes.index(pitch) - 9  # A4 = 0
    n = (octave - 4) * 12 + semitone
    return 440.0 * (2 ** (n / 12))


def make_tone(freq: float, dur: float, sr: int = SR) -> np.ndarray:
    t = np.linspace(0, dur, int(dur * sr), endpoint=False)
    # основний тон + легка 2-а гармоніка для тепла
    tone = 0.6 * np.sin(2 * np.pi * freq * t) + 0.25 * np.sin(2 * np.pi * 2 * freq * t)
    env = adsr(len(tone), a=0.05, d=0.15, s=0.6, r=0.3)
    return tone * env


def track_ambient_pad() -> np.ndarray:
    """Трек 1: 'Deep Calm' — повільний ambient pad у A minor."""
    chords = [
        ["A3", "C4", "E4"],
        ["F3", "A3", "C4"],
        ["G3", "B3", "D4"],
        ["E3", "G3", "B3"],
    ]
    beat = 2.0  # 2 секунди на акорд
    pieces: list[np.ndarray] = []
    for chord in chords * 2:  # повторюємо прогресію двічі
        layer = np.zeros(int(beat * SR))
        for n in chord:
            tone = make_tone(note_freq(n), beat)
            # вирівнюємо довжину
            m = min(len(layer), len(tone))
            layer[:m] += tone[:m] * 0.33
        pieces.append(layer)
    audio = np.concatenate(pieces)
    # легкий фейд-ін/фейд-аут
    fade = int(1.5 * SR)
    audio[:fade] *= np.linspace(0, 1, fade)
    audio[-fade:] *= np.linspace(1, 0, fade)
    return audio * 0.8


def track_pulse() -> np.ndarray:
    """Трек 2: 'Pulse Focus' — ритмічний мотив із пунктами для концентрації."""
    pattern = ["C4", None, "E4", None, "G4", "E4", "C4", None]
    note_dur = 0.28
    pieces: list[np.ndarray] = []
    for _ in range(8):  # 8 проходів по патерну
        for n in pattern:
            if n is None:
                pieces.append(np.zeros(int(note_dur * SR)))
            else:
                pieces.append(make_tone(note_freq(n), note_dur))
    audio = np.concatenate(pieces)
    fade = int(1.0 * SR)
    audio[:fade] *= np.linspace(0, 1, fade)
    audio[-fade:] *= np.linspace(1, 0, fade)
    return audio * 0.7


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Генерую 'Deep Calm'...")
    write_wav(OUT_DIR / "deep_calm.wav", track_ambient_pad())
    print("Генерую 'Pulse Focus'...")
    write_wav(OUT_DIR / "pulse_focus.wav", track_pulse())
    print(f"✅ Демо-треки збережено у {OUT_DIR}")


if __name__ == "__main__":
    main()
