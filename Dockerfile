FROM python:3.12-slim

WORKDIR /app

# Install uv from official image
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy dependency files first — layer cache means this only reruns when deps change
COPY pyproject.toml uv.lock* ./

# Install dependencies into the system python (no venv needed in a container)
RUN uv sync --frozen --no-dev --no-editable

# Copy application source
COPY . .

# Entrypoint handles migrations then server start
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000

ENTRYPOINT ["/entrypoint.sh"]