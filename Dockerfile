FROM python:3.9-slim  

WORKDIR /app

COPY requirements.txt .
RUN pip install -r requirements.txt

# Copy both the script and the source code folder
COPY worker.py .
COPY src/ src/
COPY service-account-key.json /app/

# Add /app to Python path so it can find src/
ENV PYTHONPATH=/app
ENV GOOGLE_APPLICATION_CREDENTIALS=/app/service-account-key.json

CMD ["python", "worker.py"]


