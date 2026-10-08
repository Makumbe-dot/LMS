# The Django API, served by gunicorn. Normally built by the docker-compose.yml at
# the repository root; on its own:  docker build -t lms-backend backend
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    ACCEPT_EULA=Y

# The Microsoft ODBC driver (msodbcsql18) is what pyodbc reaches SQL Server
# through; mssql-tools18 adds sqlcmd, which the nightly batch uses for backups.
# The Microsoft repository is picked to match the base image's Debian release.
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl \
 && . /etc/os-release \
 && curl -fsSL "https://packages.microsoft.com/config/debian/${VERSION_ID}/packages-microsoft-prod.deb" -o /tmp/msprod.deb \
 && dpkg -i /tmp/msprod.deb && rm /tmp/msprod.deb \
 && apt-get update \
 && apt-get install -y --no-install-recommends msodbcsql18 mssql-tools18 unixodbc \
 && rm -rf /var/lib/apt/lists/*
ENV PATH="/opt/mssql-tools18/bin:${PATH}"

WORKDIR /app/backend
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .

# Static files (the admin's, the browsable API's) are collected at build time and
# served by WhiteNoise, so no second web server or shared volume is needed.
RUN SECRET_KEY=collectstatic-only DEBUG=1 python manage.py collectstatic --noinput

# The same uid as SQL Server's own image (mssql, 10001), so the backups volume the
# two containers share is writable by both without a chmod 777.
RUN useradd --uid 10001 --create-home --shell /usr/sbin/nologin app \
 && mkdir -p /app/backend/media /backups \
 && chown app:app /app/backend/media /backups
USER app

EXPOSE 8000
ENTRYPOINT ["/app/backend/docker/entrypoint.sh"]
CMD ["gunicorn", "lms_backend.wsgi:application", "--bind", "0.0.0.0:8000", \
     "--workers", "3", "--timeout", "120", "--access-logfile", "-"]
