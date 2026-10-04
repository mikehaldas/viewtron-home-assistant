# The base image is set here rather than through a BUILD_FROM build argument.
# Older Home Assistant Supervisor versions pass BUILD_FROM pointing at their
# own base image, which has no Python or pip, so the add-on failed to build.
# Pinning the base keeps the add-on build and a plain `docker build` identical.
FROM python:3.12-alpine

RUN pip3 install --no-cache-dir --break-system-packages \
    "viewtron>=1.3.1" "paho-mqtt>=2.0" requests pyyaml

COPY run.sh /
COPY viewtron_bridge.py /

RUN chmod a+x /run.sh

ENV PYTHONUNBUFFERED=1

LABEL org.opencontainers.image.title="Viewtron Bridge" \
      org.opencontainers.image.description="Viewtron IP camera AI events to Home Assistant via MQTT" \
      org.opencontainers.image.source="https://github.com/mikehaldas/viewtron-home-assistant" \
      org.opencontainers.image.licenses="MIT"

CMD [ "/run.sh" ]
