# Production image for the Wallester UC4 KYC/KYB demo (standard-library HTTP servers, no JS build).
# Build context is the repo root: uc4/orchestrator/providers.py reads ../wallester_uc4_dataset/.
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app/src
COPY uc4/requirements.txt uc4/requirements.txt
RUN pip install pillow anthropic pypdfium2
COPY . /app/src
# tools/dataset_to_applications.py defaults to ../KYB_KYC/wallester_uc4_dataset (the author's local layout); point it at the repo copy.
RUN mkdir -p /app/KYB_KYC && ln -s /app/src/wallester_uc4_dataset /app/KYB_KYC/wallester_uc4_dataset
# sample_documents/ is gitignored; the demo pack manifest is read from it.
RUN cd uc4 && python tools/make_sample_documents.py
EXPOSE 8700 8701
CMD ["sh", "/app/src/deploy/start.sh"]
