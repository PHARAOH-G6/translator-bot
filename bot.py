import asyncio
import json
import os
import subprocess
import sys
import tempfile
from io import BytesIO

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from dotenv import load_dotenv
from vosk import Model, KaldiRecognizer

# --- Загрузка переменных окружения ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_TOKEN:
    raise SystemExit("❌ TELEGRAM_BOT_TOKEN не найден в .env")

# --- Инициализация бота ---
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# --- Путь к модели Vosk (малая версия) ---
MODEL_PATH = "vosk-model-small-ru-0.22"

# --- Путь к ffmpeg: Windows или Linux ---
if sys.platform == "win32":
    FFMPEG_PATH = os.path.abspath("ffmpeg.exe")
else:
    FFMPEG_PATH = os.path.abspath("ffmpeg")

if not os.path.exists(FFMPEG_PATH):
    raise SystemExit(f"❌ Не найден ffmpeg: {FFMPEG_PATH}")

if not os.path.exists(MODEL_PATH):
    raise SystemExit(f"❌ Не найдена папка модели: {MODEL_PATH}")

print("⏳ Загружаю модель Vosk...")
model = Model(MODEL_PATH)
print("✅ Модель загружена.")


def convert_ogg_to_wav_16k(ogg_bytes: bytes) -> bytes:
    """
    Конвертирует OGG/OPUS в WAV 16kHz mono через ffmpeg напрямую.
    Возвращает байты WAV.
    """
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f_in:
        f_in.write(ogg_bytes)
        in_path = f_in.name

    out_path = in_path + ".wav"

    try:
        cmd = [
            FFMPEG_PATH,
            "-y",
            "-i", in_path,
            "-ar", "16000",
            "-ac", "1",
            "-f", "wav",
            "-acodec", "pcm_s16le",
            out_path,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            print(f"[FFMPEG STDERR]\n{result.stderr}")
            raise RuntimeError(f"ffmpeg вернул код {result.returncode}")

        with open(out_path, "rb") as f_out:
            wav_bytes = f_out.read()

        print(f"[DEBUG] WAV сконвертирован: {len(wav_bytes)} байт")
        return wav_bytes

    finally:
        for p in (in_path, out_path):
            try:
                os.remove(p)
            except OSError:
                pass


def wav_duration_sec(wav_bytes: bytes) -> float:
    """Длительность WAV в секундах (16kHz mono 16-bit)."""
    data_size = len(wav_bytes) - 44
    return data_size / (16000 * 2)


@dp.message(F.voice)
async def handle_voice(message: Message):
    await message.answer("🎧 Слушаю...")

    try:
        # 1. Скачиваем OGG/OPUS в память
        file_info = await bot.get_file(message.voice.file_id)
        raw_buffer = BytesIO()
        await bot.download_file(file_info.file_path, destination=raw_buffer)
        ogg_bytes = raw_buffer.getvalue()
        print(f"\n[DEBUG] Получено голосовое: {len(ogg_bytes)} байт")

        # 2. Конвертируем через ffmpeg в WAV 16kHz mono
        wav_bytes = convert_ogg_to_wav_16k(ogg_bytes)
        print(f"[DEBUG] Длительность WAV: {wav_duration_sec(wav_bytes):.2f} сек")

        # 3. Распознаём
        rec = KaldiRecognizer(model, 16000)
        rec.AcceptWaveform(wav_bytes)
        result = json.loads(rec.FinalResult())
        print(f"[DEBUG] Результат Vosk: {result}")

        text = result.get("text", "").strip()

        if text:
            await message.answer(f"📝 {text}")
        else:
            await message.answer("🤷 Не удалось распознать. Говори громче и чётче.")

    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        await message.answer(f"❌ Ошибка: {e}")


@dp.message(F.text)
async def handle_text(message: Message):
    await message.answer("Отправь голосовое 🎤 — переведу в текст.")


async def main():
    print("🚀 Бот запущен. Жду сообщения...")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())