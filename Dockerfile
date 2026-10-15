# Focus Reader for Hugging Face Spaces (Docker SDK). One gunicorn process
# serves the page and the API on port 7860.
#   docker build -t focus-reader .
#   docker run --rm -p 7860:7860 focus-reader      then open http://localhost:7860/
FROM python:3.12-slim

# Hugging Face runs the container as uid 1000, not root
RUN useradd --create-home --uid 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    PORT=7860
WORKDIR /home/user/app

# dependencies first, so code changes don't reinstall them
COPY --chown=user backend/requirements.txt backend/requirements.txt
# the pip version the requirements were tested with (the base image's is older;
# its "new release available" check is moot in the command that replaces it)
RUN python -m pip install --no-cache-dir --disable-pip-version-check --user pip==26.2.1 \
 && python -m pip install --no-cache-dir --user -r backend/requirements.txt

COPY --chown=user . .
# generated audio is written here; it must exist and belong to the app's user
RUN mkdir -p backend/output

EXPOSE 7860
# 1 worker with 4 threads suits the free CPU tier; synthesizing a section or
# extracting a large PDF can take a while, hence the 120 s timeout.
# gunicorn binds 0.0.0.0:$PORT on its own when PORT is set (7860, above), so
# the JSON form needs no shell: gunicorn is the container's main process and
# receives stop signals directly.
CMD ["gunicorn", "--chdir", "backend", "--workers", "1", "--threads", "4", "--timeout", "120", "server:app"]
