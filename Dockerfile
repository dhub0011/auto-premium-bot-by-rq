FROM python:3.10-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    wget \
    curl \
    unzip \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first (for better caching)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Install Playwright browsers
RUN playwright install chromium

# Copy the rest of the app
COPY . .

# Expose the port Railway expects
EXPOSE 5000

# Run with proper host binding
CMD ["python", "-m", "flask", "run", "--host=0.0.0.0", "--port=5000"]
