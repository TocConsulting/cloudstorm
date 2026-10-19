FROM python:3.12-slim AS build
WORKDIR /src
COPY pyproject.toml README.md LICENSE ./
COPY cloudstorm ./cloudstorm
RUN pip install --no-cache-dir build && python -m build --wheel --outdir /dist .

FROM python:3.12-slim
LABEL org.opencontainers.image.title="cloudstorm" \
      org.opencontainers.image.description="Distributed load testing across AWS regions" \
      org.opencontainers.image.source="https://github.com/TocConsulting/cloudstorm" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="1.0.0"
COPY --from=build /dist/*.whl /tmp/
RUN pip install --no-cache-dir /tmp/*.whl && rm -f /tmp/*.whl \
    && useradd --create-home --uid 1000 cloudstorm
USER cloudstorm
WORKDIR /work
ENTRYPOINT ["cloudstorm"]
CMD ["--help"]
