# AI-MRG: API and dashboard image.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 10001 aimrg
WORKDIR /app

COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
COPY packs ./packs
RUN pip install ".[api,postgres,dashboard,docx]"

COPY docker/entrypoint.sh /usr/local/bin/aimrg-entrypoint
RUN chmod 0755 /usr/local/bin/aimrg-entrypoint \
    && mkdir -p /data && chown aimrg:aimrg /data

USER aimrg
VOLUME ["/data"]
EXPOSE 8000 8501
ENTRYPOINT ["aimrg-entrypoint"]
CMD ["api"]
