FROM python:3.12.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv/app

COPY control_plane/requirements.txt /tmp/control-plane-requirements.txt
RUN pip install --no-cache-dir --requirement /tmp/control-plane-requirements.txt \
    && groupadd --system app \
    && useradd --system --gid app --home-dir /nonexistent --shell /usr/sbin/nologin app

COPY backend/app/security ./backend/app/security
COPY control_plane ./control_plane

USER app:app
EXPOSE 8080

CMD ["python", "-m", "uvicorn", "control_plane.app.main:load_default_app", "--factory", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers", "--forwarded-allow-ips", "*"]
