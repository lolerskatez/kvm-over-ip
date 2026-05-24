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
    pkgconf \
    libva \
    libva-dev \
    libva-intel-driver \
    mesa-va-gallium \
    v4l-utils \
    v4l-utils-dev \
    libvpx \
    libvpx-dev \
    opus \
    opus-dev \
    libsrtp \
    libsrtp-dev

# Create app directory
WORKDIR /app

# Copy application files
COPY requirements.txt .
COPY . .

# Create virtual environment and install Python dependencies
RUN python3 -m venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"
ENV CONTAINER=1

# Set environment variables for VA-API
ENV LIBVA_DRIVER_NAME=iHD
ENV LIBVA_DRIVERS_PATH=/usr/lib/dri

RUN pip install --upgrade pip setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

# Create necessary directories
RUN mkdir -p /var/log/kvm-over-ip \
    && mkdir -p /var/lib/kvm \
    && mkdir -p /app/data

# Add root user to video and render groups for hardware access
RUN adduser root video 2>/dev/null || true
RUN addgroup render 2>/dev/null || true
RUN adduser root render 2>/dev/null || true

# Expose port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=40s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

# Run the application
CMD ["/app/.venv/bin/gunicorn", "-c", "/app/gunicorn_config.py", "app:app"]
