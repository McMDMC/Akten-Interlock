# Akten-Interlock mit vollständigem PDF/A-Stack (OCRmyPDF, Tesseract deutsch, Ghostscript).
FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      git ghostscript tesseract-ocr tesseract-ocr-deu qpdf pngquant unpaper \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-cache-dir ".[pdf,foto]"

# Kein root im Betrieb.
RUN useradd --create-home --uid 10001 akte
USER akte
WORKDIR /home/akte

ENTRYPOINT ["akten-interlock"]
CMD ["demo"]
