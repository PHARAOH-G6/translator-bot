import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from io import BytesIO

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message
from dotenv import load_dotenv
from vosk import Model, KaldiRecognizer

# --- Загрузка переменных окружения ---
load_dotenv()
TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

if not TELEGRAM_TOKEN:
    raise SystemExit("❌ TELEGRAM_BOT_TOKEN не найден")

# =============================================================
# ШАГ 1: Health-сервер стартует ПЕРВЫМ — до всего остального.
# Render сканирует порты сразу после запуска, и если не найдёт —
# убьёт сервис. Поэтому открываем порт мгновенно.
# =============================================================
def start_health_server():
    port = int(os.getenv("PORT", "10000"))

    class Handler(BaseHTTPRequestHandler):
        def _respond(self):
            body = b"Bot is running"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            self._respond()

        def do_HEAD(self):
            self._respond()

        def do_POST(self):
            self._respond()

        def do_PUT(self):
            self._respond()

        def log_message(self, *args):
            pass

    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[HTTP] Health-сервер запущен на порту {port}", flush=True)


start_health_server()

# =============================================================
# ШАГ 2: Проверки путей и инициализация бота
# =============================================================
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

MODEL_PATH = "vosk-model-small-ru-0.22"

if sys.platform == "win32":
    FFMPEG_PATH = os.path.abspath("ffmpeg.exe")
else:
    FFMPEG_PATH = os.path.abspath("ffmpeg")

if not os.path.exists(FFMPEG_PATH):
    raise SystemExit(f"❌ Не найден ffmpeg: {FFMPEG_PATH}")

if not os.path.exists(MODEL_PATH):
    raise SystemExit(f"❌ Не найдена папка модели: {MODEL_PATH}")

# =============================================================
# ШАГ 3: Загрузка модели Vosk (может занять 30-60 секунд).
# Порт уже открыт — Render не убьёт сервис во время загрузки.
# =============================================================
print("⏳ Загружаю модель Vosk...", flush=True)
model = Model(MODEL_PATH)
print("✅ Модель загружена.", flush=True)


def convert_ogg_to_wav_16k(ogg_bytes: bytes) -> bytes:
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f_in:
        f_in.write(ogg_bytes)
        in_path = f_in.name

    out_path = in_path + ".wav"

    try:
        cmd = [
            FFMPEG_PATH, "-y",
            "-i", in_path,
            "-ar", "16000",
            "-ac", "1",
            "-f", "wav",
            "-acodec", "pcm_s16le",
            out_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
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


@dp.message(F.voice)
async def handle_voice(message: Message):
    await message.answer("🎧 Слушаю...")

    try:
        file_info = await bot.get_file(message.voice.file_id)
        raw_buffer = BytesIO()
        await bot.download_file(file_info.file_path, destination=raw_buffer)
        ogg_bytes = raw_buffer.getvalue()
        print(f"\n[DEBUG] Получено голосовое: {len(ogg_bytes)} байт")

        wav_bytes = convert_ogg_to_wav_16k(ogg_bytes)

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
    print("🚀 Бот запущен. Жду сообщения...", flush=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
