FROM python:3.12-slim
WORKDIR /app
COPY ledger ./ledger
ENV LEDGER_HOME=/data TZ=America/Toronto PYTHONUNBUFFERED=1
VOLUME /data
EXPOSE 8765
# Needs LEDGER_DASHBOARD_PASSWORD (it refuses to listen on the network without one)
CMD ["python", "-m", "ledger", "run", "--no-browser", "--host", "0.0.0.0"]
