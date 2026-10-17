FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    TZ=Asia/Shanghai \
    BID_DATA_DIR=/app/data \
    BID_PORT=8600

WORKDIR /app

# 国内服务器构建慢时，可加：--build-arg PIP_INDEX=https://mirrors.aliyun.com/pypi/simple/
ARG PIP_INDEX=https://pypi.org/simple
COPY requirements.txt .
RUN pip install --no-cache-dir -i ${PIP_INDEX} -r requirements.txt

COPY app ./app
COPY static ./static
COPY tests/fixtures ./tests/fixtures

VOLUME ["/app/data"]
EXPOSE 8600

HEALTHCHECK --interval=60s --timeout=5s --retries=3 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8600/api/health',timeout=4)"

CMD ["python", "-m", "app.main"]
