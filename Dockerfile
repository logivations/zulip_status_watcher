# Use Python 3.11 slim image as base
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY clients/ ./clients/
COPY watcher/ ./watcher/
COPY tools/ ./tools/

# Create necessary directories for logs and configuration
RUN mkdir -p /data/logs /data/logs/backup /data/appconfig_static/zulip

# Copy configuration file template
COPY zulip.properties /data/appconfig_static/zulip/zulip.properties

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the application
CMD ["python", "-m", "watcher.watcher"]
