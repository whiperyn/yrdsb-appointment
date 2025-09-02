# Playwright official image (Python + all browsers + system deps)
FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your project
COPY . .

# Run your watcher
CMD ["python", "yrdsb_appointment.py"]
