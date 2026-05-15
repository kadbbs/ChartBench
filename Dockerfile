# Use a current Python 3.11 slim image to avoid the retired Debian buster apt sources
FROM swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io/python:3.11-slim-bookworm

# Set working directory
WORKDIR /app

# Install system dependencies for better network support
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --timeout=600 --retries=10 --index-url https://mirrors.aliyun.com/pypi/simple/ --trusted-host mirrors.aliyun.com -r requirements.txt

# Copy the entire project
COPY . .

# Environment variables for better performance
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Expose the port the app runs on
EXPOSE 8050

# Healthcheck
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8050/api/health || exit 1

# Command to run the application with bitget as default provider
CMD ["python", "web_tq_chart.py", "--provider", "bitget", "--symbol", "BTCUSDT", "--host", "0.0.0.0", "--port", "8050"]
