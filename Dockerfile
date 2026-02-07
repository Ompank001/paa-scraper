# Use Python slim image as base
# Updated: Force rebuild with modern GPG key method
FROM python:3.11-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies and Chrome
RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    unzip \
    curl \
    ca-certificates \
    --no-install-recommends \
    && mkdir -p /etc/apt/keyrings \
    && wget -q -O /etc/apt/keyrings/google-chrome.asc https://dl-ssl.google.com/linux/linux_signing_key.pub \
    && echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/google-chrome.asc] http://dl.google.com/linux/chrome/deb/ stable main" > /etc/apt/sources.list.d/google-chrome.list \
    && apt-get update \
    && apt-get install -y google-chrome-stable --no-install-recommends \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set Chrome binary location and webdriver-manager cache
ENV CHROME_BIN=/usr/bin/google-chrome
ENV WDM_LOCAL=1
ENV WDM_LOG_LEVEL=0

# Create app directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Expose port
EXPOSE 5000

# Run the application with gunicorn
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--timeout", "120", "--workers", "2", "app:app"]
