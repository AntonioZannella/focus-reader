# Focus Reader in a container. Render builds this image from render.yaml on
# every push to main and sets PORT itself; one gunicorn process serves the page
# and the API on that port. The README shows how to build and run it locally.
FROM python:3.12.16-slim-trixie

# run as an unprivileged user, not root
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
# gunicorn binds 0.0.0.0:$PORT on its own when PORT is set (Render sets it;
# the ENV above is the fallback), so the JSON form needs no shell: gunicorn is
# the container's main process and receives stop signals directly.
# --access-logfile - writes one line per request to stdout, into Render's logs.
CMD ["gunicorn", "--chdir", "backend", "--workers", "1", "--threads", "4", "--timeout", "120", "--access-logfile", "-", "server:app"]
