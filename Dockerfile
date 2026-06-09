FROM python:3.12-slim

# 不写 .pyc、日志直输出；关闭 pip 进度条线程（规避受限环境下
# "can't start new thread"：pip 的 rich 进度条会额外起线程，触发 PID/线程上限）
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_PROGRESS_BAR=off \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_INPUT=1

WORKDIR /app

# 先装基础依赖，利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 可选：构建时装入 spaCy NER 后端（fast 模式的公司/人名/地址兜底）。
#   docker build --build-arg WITH_SPACY=1 --build-arg SPACY_MODEL=zh_core_web_trf .
# 默认 WITH_SPACY=0：镜像轻量，fast 模式降级为「字典 + 正则」。
# 注意：zh_core_web_trf 约 400MB（含 torch），镜像会显著变大、构建较久。
ARG WITH_SPACY=0
ARG SPACY_MODEL=zh_core_web_trf
COPY requirements-nlp.txt .
RUN if [ "$WITH_SPACY" = "1" ]; then \
        pip install --no-cache-dir -r requirements-nlp.txt && \
        python -m spacy download "$SPACY_MODEL" ; \
    fi
ENV DESENTI_SPACY_MODEL=${SPACY_MODEL}

# 复制应用代码与词典
COPY app ./app
COPY sensitive_dict.txt ./sensitive_dict.txt

# 以非 root 用户运行
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# 健康检查
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=2).status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
