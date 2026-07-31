FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OMNI_GAME_OBSERVATORY_HOST=0.0.0.0 \
    OMNI_GAME_OBSERVATORY_PORT=8222

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[dashboard]"

EXPOSE 8222
CMD ["python", "-m", "omnicompany.packages.domains.game_observatory.public_server", "--host", "0.0.0.0", "--port", "8222"]
