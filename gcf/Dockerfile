# Use Python 3.11 to match the Cloud Functions runtime
FROM python:3.11-slim

WORKDIR /app

# Copy requirements.txt and install dependencies
COPY requirements.txt .
RUN python -m venv venv && \
    . venv/bin/activate && \
    pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Copy the application code
COPY main.py .

# Expose port 8080 for local testing
EXPOSE 8080

# Set the entry point for local testing
CMD [".", "venv/bin/activate", "&&", "functions-framework", "--target=main", "--port=8080"]