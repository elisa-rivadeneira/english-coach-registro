FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# Modelos de voz (≈120 MB), se bajan una sola vez al construir la imagen
RUN mkdir -p modelos \
 && curl -fsSL -o modelos/kokoro-int8.onnx https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/kokoro-v1.0.int8.onnx \
 && curl -fsSL -o modelos/voices.bin https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/voices-v1.0.bin
COPY . .
# Los datos viven en /data: monta ahí un volumen persistente
ENV REGISTRO_DB=/data/registro.db CACHE_DIR=/data/cache_audio
EXPOSE 8000
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
