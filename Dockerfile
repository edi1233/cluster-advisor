FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY platform_patch.py .

EXPOSE 8000

CMD ["python", "platform_patch.py"]
