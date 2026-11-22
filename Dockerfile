# Practice Hub: the standard library and pymongo (its database is MongoDB), so the image is Python and the source.
FROM python:3.12-slim

# the tz database: the practice's "now" and "today" are in its own time zone ([practice] timezone, via zoneinfo), not
# the container's (UTC). The hub refuses to open without it rather than date notes in the wrong day.
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/* \
    && python -c "import zoneinfo; zoneinfo.ZoneInfo('America/Halifax')"
RUN useradd --create-home --uid 10001 hub
WORKDIR /app
COPY pyproject.toml ./
COPY practicehub ./practicehub
COPY packs ./packs
COPY modules ./modules
RUN pip install --no-cache-dir . && mkdir -p /state /anchors && chown hub /state /anchors

USER hub
# the anchors live on their own volume: a copy of the state volume alone cannot hide that events were removed
ENV PRACTICEHUB_DATABASE=mongodb://mongo:27017/practicehub?replicaSet=rs0&directConnection=true \
    PRACTICEHUB_STATE=/state PRACTICEHUB_PACKS=/app/packs PRACTICEHUB_MODULES=/app/modules PRACTICEHUB_ANCHORS=/anchors/track.anchors
VOLUME /state /anchors
EXPOSE 8200
# set up once with: docker compose run --rm hub init --pack general-practice
ENTRYPOINT ["practicehub"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8200"]
