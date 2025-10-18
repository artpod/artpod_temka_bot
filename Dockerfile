FROM python:3.11-slim
WORKDIR /app
RUN apt-get update -y && apt-get install -y --no-install-recommends tzdata ca-certificates && rm -rf /var/lib/apt/lists/*
COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY . /app
CMD ["python", "temka_bot.py"]
