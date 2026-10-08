# The base image is set here rather than through a BUILD_FROM build argument.
# Older Home Assistant Supervisor versions pass BUILD_FROM pointing at their
# own base image, which has no Python or pip, so the add-on failed to build.
# Pinning the base keeps the add-on build and a plain `docker build` identical.
FROM python:3.12-alpine

# viewtron 1.4.0 adds plate direction, confidence, list, vehicle
# attributes, and camera event timestamps. VIEWTRON_SPEC can be a git
# URL when that release is not on PyPI yet. A normal build uses the pin.
ARG VIEWTRON_SPEC="viewtron>=1.4.0"
RUN set -eu; \
    need_git=0; \
    case "$VIEWTRON_SPEC" in \
      *git+*|*github.com*) need_git=1 ;; \
    esac; \
    if [ "$need_git" -eq 1 ]; then apk add --no-cache --virtual .viewtron-git git; fi; \
    pip3 install --no-cache-dir --break-system-packages \
        "$VIEWTRON_SPEC" "paho-mqtt>=2.0" requests pyyaml; \
    if [ "$need_git" -eq 1 ]; then apk del .viewtron-git; fi

COPY run.sh /
COPY viewtron_bridge.py /

RUN chmod a+x /run.sh

ENV PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.title="Viewtron Bridge" \
      org.opencontainers.image.description="Viewtron IP camera AI events to Home Assistant via MQTT" \
      org.opencontainers.image.source="https://github.com/mikehaldas/viewtron-home-assistant" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.version="1.1.1"

CMD [ "/run.sh" ]
