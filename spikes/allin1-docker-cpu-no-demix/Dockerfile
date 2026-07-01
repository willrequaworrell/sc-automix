FROM --platform=linux/amd64 python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1

WORKDIR /app

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    build-essential \
    ca-certificates \
    cmake \
    ffmpeg \
    git \
    libsndfile1 \
    libsndfile1-dev \
    python3-dev \
  && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --upgrade pip setuptools wheel

# Keep the first attempt intentionally close to the agreed pin set.
RUN python -m pip install --index-url https://download.pytorch.org/whl/cpu torch==2.2.2
RUN python -m pip install numpy==1.26.4 scipy==1.13.1 soundfile==0.12.1 jsonschema==4.23.0
RUN python -m pip install git+https://github.com/CPJKU/madmom
RUN python -m pip install allin1==1.1.0

# allin1 currently declares broad dependencies, so re-assert the spike pins before
# building NATTEN from source. If this fails on Linux CPU, RUNLOG should capture it
# as the blocker instead of silently switching model paths.
RUN python -m pip install --force-reinstall --index-url https://download.pytorch.org/whl/cpu torch==2.2.2 torchaudio==2.2.2
RUN python -m pip install --force-reinstall numpy==1.26.4 scipy==1.13.1 soundfile==0.12.1 jsonschema==4.23.0
RUN python -m pip install --no-build-isolation --no-binary=:all: natten==0.14.6

COPY analyze_no_demix.py /app/analyze_no_demix.py

ENTRYPOINT ["python", "/app/analyze_no_demix.py"]
