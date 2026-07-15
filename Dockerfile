# fleetctl web GUI. The CLI/TUI (./fleetctl) run directly on the host — this
# image is only for the Flask front end, but both share fleetlib.py, which
# now needs sqlcipher3-binary (the DB is encrypted at rest) regardless of
# which front end is running.
FROM python:3.12-slim

WORKDIR /app

COPY web/requirements.txt web/requirements.txt
RUN pip install --no-cache-dir -r web/requirements.txt

COPY . .

EXPOSE 4299

CMD ["python3", "web/app.py"]
