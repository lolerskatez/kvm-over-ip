FROM alpine:3.23

# Install required system packages
RUN apk update && apk add --no-cache \
    python3 \
    python3-dev \
    py3-pip \
    ffmpeg \
    ffmpeg-dev \
    udev \
    openrc \
    linux-headers \
    build-base \
    curl \
    pkgconf

# Create app directory
WORKDIR /app

# Copy application files
COPY requirements.txt .
COPY . .

# Create virtual environment and install Python dependencies
RUN python3 -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
ENV CONTAINER=1
RUN pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

# Create necessary directories
RUN mkdir -p /var/log/kvm-over-ip \
    && mkdir -p /var/lib/kvm \
    && mkdir -p /app/data

# Expose ports (HTTP: 80, HTTPS: 443)
EXPOSE 80 443

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost/api/health || exit 1

# Run the application
CMD ["/app/.venv/bin/gunicorn", "-c", "/app/gunicorn_config.py", "app:app"]
