FROM python:3.12-slim

WORKDIR /app

COPY api/requirements.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

COPY api/main.py ./main.py
COPY api/prompts ./prompts
COPY shared ./shared

EXPOSE 8080

CMD ["ddtrace-run", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
