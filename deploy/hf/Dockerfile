# Sentinel Twin on a Hugging Face Space (Docker SDK). Demo mode: no keys needed; payments run on the
# labelled mock gateway, SQLite lives inside the container and resets when the Space restarts.
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd -m -u 1000 user
WORKDIR /home/user/app
COPY --chown=user requirements.txt .
RUN pip install -r requirements.txt
COPY --chown=user . .
RUN mkdir -p /home/user/app/data && chown user:user /home/user/app /home/user/app/data
USER user
ENV HOME=/home/user PORT=7860 GAT_EPOCHS=80 TWIN_SEED=3 AUTO_TRAFFIC=1
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=120s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/healthz', timeout=4)"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
