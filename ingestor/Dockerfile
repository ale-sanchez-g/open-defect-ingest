FROM python:3.12-slim

WORKDIR /app

COPY ingestor/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY ingestor/main.py ./main.py
COPY shared ./shared

CMD ["ddtrace-run", "python", "main.py"]
