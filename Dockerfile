# Inline the parser build so docker compose build needs only the repository root context.
FROM rust:1.99.1-bookworm AS parser
RUN apt-get update && apt-get install -y --no-install-recommends protobuf-compiler libprotobuf-dev && rm -rf /var/lib/apt/lists/*
WORKDIR /src
COPY prototype/haste-patch/ prototype/haste-patch/
COPY parser/build.sh parser/build.sh
RUN bash parser/build.sh /out

FROM python:3.12-slim
ENV DATA_DIR=/data
WORKDIR /app
COPY --from=parser /out/dldump /usr/local/bin/dldump
COPY app/requirements.txt /app/app/requirements.txt
RUN pip install --no-cache-dir -r /app/app/requirements.txt
COPY . /app/
EXPOSE 8000
CMD ["uvicorn", "app.server.main:app", "--host", "0.0.0.0", "--port", "8000"]
