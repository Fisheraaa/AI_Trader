FROM python:3.12-slim

WORKDIR /app

ENV TZ=Asia/Shanghai
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DEFAULT_TIMEOUT=120
ENV PIP_NO_CACHE_DIR=1

COPY requirements.txt /app/requirements.txt

# 多镜像源回退安装：阿里 -> 清华 -> 官方
RUN python -m pip install --upgrade pip setuptools wheel && \
    (pip install -r /app/requirements.txt -i https://mirrors.aliyun.com/pypi/simple --trusted-host mirrors.aliyun.com || \
     pip install -r /app/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple --trusted-host pypi.tuna.tsinghua.edu.cn || \
     pip install -r /app/requirements.txt -i https://pypi.org/simple)

COPY . /app

CMD ["python3", "scheduler.py"]