#!/usr/bin/env bash
set -e

# Скачиваем модель Vosk (малая русская, ~45 МБ)
wget -q https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip
unzip -q vosk-model-small-ru-0.22.zip
rm vosk-model-small-ru-0.22.zip

# Скачиваем статический ffmpeg для Linux
wget -q https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz
tar -xf ffmpeg-release-amd64-static.tar.xz
cp ffmpeg-*-amd64-static/ffmpeg .
cp ffmpeg-*-amd64-static/ffprobe .
chmod +x ffmpeg ffprobe

# Устанавливаем Python-зависимости
pip install -r requirements.txt