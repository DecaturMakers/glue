FROM python:3.10-slim

ENV PIP_DISABLE_PIP_VERSION_CHECK=on

RUN pip install poetry

WORKDIR /app

COPY rfid-sheet-service-account.json ./

COPY poetry.lock pyproject.toml ./

RUN poetry config virtualenvs.create false
RUN poetry install --no-interaction --no-ansi

COPY . ./

ENV PORT 80
CMD exec gunicorn --bind :$PORT --workers 1 --threads 1 main:app
