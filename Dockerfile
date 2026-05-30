FROM python:3.11-slim
WORKDIR /app
RUN pip install --no-cache-dir \
    python-telegram-bot[job-queue] \
    aiohttp \
    fastapi \
    uvicorn
COPY . .
CMD ["bash", "start_cloud.sh"]
