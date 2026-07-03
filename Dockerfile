FROM ghcr.io/astral-sh/uv:python3.11-bookworm-slim AS builder

WORKDIR /app
ENV UV_COMPILE_BYTECODE=1

# Sao chép và build trước các packages để tận dụng cache của Docker
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-editable

# --- RUNTIME STAGE ---
FROM python:3.11-slim-bookworm

WORKDIR /app

# Cài đặt các thư viện hệ thống tối thiểu cần thiết để khởi chạy Firefox của Playwright
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    libxt6 \
    libasound2 \
    libx11-xcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxi6 \
    libxtst6 \
    libnss3 \
    libcups2 \
    libxss1 \
    librandr2 \
    libpangocairo-1.0-0 \
    libpango-1.0-0 \
    libatk1.0-0 \
    ca-certificates \
    git \
    && rm -rf /var/lib/apt/lists/*

# Sao chép môi trường ảo cô lập từ builder stage sang
COPY --from=builder /app/.venv /app/.venv
COPY . /app

# Gán biến môi trường trỏ trực tiếp vào virtual env
ENV PATH="/app/.venv/bin:$PATH"

# Tải xuống binary trình duyệt tàng hình Firefox-13 đặc quyền
RUN python -m invisible_playwright fetch

EXPOSE 8000

# Khởi động Backend
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]