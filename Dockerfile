# Use official Python image
FROM python:3.10-slim

# Set working directory
WORKDIR /app

# Copy requirements first (better caching)
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy rest of the project
COPY . .

# Expose the port (Cloud Run expects 8080)
EXPOSE 8080

# Start using gunicorn (run.py should define 'app')
CMD ["gunicorn", "-b", ":8080", "--timeout", "120", "--preload", "run:app"]