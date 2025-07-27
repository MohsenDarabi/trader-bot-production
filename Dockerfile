FROM python:3.12-slim

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create logs directory with proper permissions
RUN mkdir -p logs && chmod 777 logs

# Make entrypoint executable
RUN chmod +x entrypoint.sh || true

# Run as non-root user
RUN useradd -m botuser && chown -R botuser:botuser /app
USER botuser

# Set environment
ENV PYTHONUNBUFFERED=1
ENV BOT_INSTANCE=development

CMD ["python", "main.py"]