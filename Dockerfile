FROM python:3.13-slim

WORKDIR /app

# Install deps first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy app
COPY . .

# Don't run as root
RUN useradd -m engram
USER engram

EXPOSE 8000

CMD ["python", "run.py"]
