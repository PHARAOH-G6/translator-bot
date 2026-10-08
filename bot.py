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
# ШАГ 1: Health-сервер для Render (запускается первым!)
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

        def do_GET(self): self._respond()
        def do_HEAD(self): self._respond()
        def do_POST(self): self._respond()
        def do_PUT(self): self._respond()
        def log_message(self, *args): pass

    server = HTTPServer(("0.0.0.0", port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"[HTTP] Health-сервер запущен на порту {port}", flush=True)


start_health_server()


# =============================================================
# ШАГ 2: Инициализация бота
# =============================================================
bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

MODEL_PATH_RU = "vosk-model-small-ru-0.22"
MODEL_PATH_EN = "vosk-model-small-en-us-0.15"

if sys.platform == "win32":
    FFMPEG_PATH = os.path.abspath("ffmpeg.exe")
else:
    FFMPEG_PATH = os.path.abspath("ffmpeg")

if not os.path.exists(FFMPEG_PATH):
    raise SystemExit(f"❌ Не найден ffmpeg: {FFMPEG_PATH}")

if not os.path.exists(MODEL_PATH_RU):
    raise SystemExit(f"❌ Не найдена русская модель: {MODEL_PATH_RU}")

# Английская модель опциональна — грузим только если папка есть
load_en = os.path.exists(MODEL_PATH_EN)
if not load_en:
    print(f"⚠️ Английская модель не найдена ({MODEL_PATH_EN}). "
          f"Будет работать только русский.", flush=True)


# =============================================================
# ШАГ 3: Загрузка моделей Vosk
# =============================================================
print("⏳ Загружаю русскую модель Vosk...", flush=True)
model_ru = Model(MODEL_PATH_RU)
print("✅ Русская модель загружена.", flush=True)

model_en = None
if load_en:
    print("⏳ Загружаю английскую модель Vosk...", flush=True)
    model_en = Model(MODEL_PATH_EN)
    print("✅ Английская модель загружена.", flush=True)


# =============================================================
# Конвертация OGG → WAV 16kHz mono
# =============================================================
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
            return f_out.read()

    finally:
        for p in (in_path, out_path):
            try:
                os.remove(p)
            except OSError:
                pass


# =============================================================
# Определение языка по эвристике: пробуем RU, потом EN
# =============================================================
def recognize_with_lang(wav_bytes: bytes) -> tuple[str, str]:
    """
    Возвращает (текст, язык).
    Сначала пробуем русскую модель. Если она вернула пусто — английскую.
    """
    rec_ru = KaldiRecognizer(model_ru, 16000)
    rec_ru.AcceptWaveform(wav_bytes)
    res_ru = json.loads(rec_ru.FinalResult())
    text_ru = res_ru.get("text", "").strip()

    # Если русская модель дала текст — используем её
    if text_ru:
        return text_ru, "ru"

    # Иначе — пробуем английскую (если загружена)
    if model_en is not None:
        rec_en = KaldiRecognizer(model_en, 16000)
        rec_en.AcceptWaveform(wav_bytes)
        res_en = json.loads(rec_en.FinalResult())
        text_en = res_en.get("text", "").strip()
        if text_en:
            return text_en, "en"

    return "", "unknown"


# =============================================================
# Обработка голосовых сообщений
# =============================================================
@dp.message(F.voice)
async def handle_voice(message: Message):
    # Игнорируем слишком короткие записи (менее 1 сек) — обычно это случайные нажатия
    if message.voice.duration < 1:
        if message.chat.type == "private":
            await message.answer("🤏 Слишком короткое сообщение.")
        return

    # Реагируем только если это личка ИЛИ бот упомянут ИЛИ это ответ боту
    is_private = message.chat.type == "private"
    bot_mentioned = False
    if not is_private and message.text:
        me = await bot.me()
        bot_mentioned = f"@{me.username}" in (message.text or "")

    # В группах — не отвечаем на каждое голосовое, чтобы не спамить.
    # Работаем, только если бот упомянут в reply или отдельным сообщением.
    # Если хочешь, чтобы бот реагировал на ВСЕ голосовые в группе — убери блок ниже.

    try:
        file_info = await bot.get_file(message.voice.file_id)
        raw_buffer = BytesIO()
        await bot.download_file(file_info.file_path, destination=raw_buffer)
        ogg_bytes = raw_buffer.getvalue()
        print(f"\n[DEBUG] Голосовое {len(ogg_bytes)} байт, чат={message.chat.type}")

        wav_bytes = convert_ogg_to_wav_16k(ogg_bytes)
        print(f"[DEBUG] WAV: {len(wav_bytes)} байт")

        text, lang = recognize_with_lang(wav_bytes)
        print(f"[DEBUG] Язык={lang}, текст='{text}'")

        if not text:
            await message.answer("🤷 Не удалось распознать речь.")
            return

        flag = "🇷🇺" if lang == "ru" else ("🇬🇧" if lang == "en" else "🌐")
        # В группе — отвечаем реплаем на сообщение пользователя
        if message.chat.type in ("group", "supergroup"):
            await message.reply(f"{flag} {text}")
        else:
            await message.answer(f"{flag} {text}")

    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")
        await message.answer(f"❌ Ошибка: {e}")


# =============================================================
# Обработка текста — только в личке
# =============================================================
@dp.message(F.text)
async def handle_text(message: Message):
    if message.chat.type == "private":
        await message.answer(
            "Отправь голосовое 🎤 — переведу в текст.\n"
            "Поддерживаю 🇷🇺 русский и 🇬🇧 английский."
        )


# =============================================================
# /start и /help
# =============================================================
@dp.message(F.text == "/start")
async def cmd_start(message: Message):
    await message.answer(
        "👋 Привет! Я перевожу голосовые сообщения в текст.\n\n"
        "🎤 Просто отправь мне голосовое — получишь расшифровку.\n"
        "🌍 Языки: русский, английский.\n\n"
        "➕ Можно добавить меня в группу: отключи Privacy Mode "
        "в @BotFather, и я буду распознавать все голосовые."
    )


# =============================================================
# Запуск polling
# =============================================================
async def main():
    print("🚀 Бот запущен. Жду сообщения...", flush=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
