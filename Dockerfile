FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY orderfix ./orderfix
COPY AGENT_PROFILE.md .
RUN useradd --create-home orderfix && mkdir -p /app/data && chown -R orderfix:orderfix /app
USER orderfix
CMD ["python", "-m", "orderfix.agent"]
