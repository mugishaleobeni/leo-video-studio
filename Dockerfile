FROM pytorch/pytorch:2.6.0-cuda12.4-cudnn9-runtime
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV STUDIO_DATA=/data HF_HOME=/data/model-cache PORT=7860 CPU_OFFLOAD=1
EXPOSE 7860
CMD ["python", "app.py"]
