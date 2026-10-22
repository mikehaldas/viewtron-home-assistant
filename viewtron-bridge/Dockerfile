# The base image is set here rather than through a BUILD_FROM build argument.
# Older Home Assistant Supervisor versions pass BUILD_FROM pointing at their
# own base image, which has no Python or pip, so the add-on failed to build.
# Pinning the base keeps the add-on build and a plain `docker build` identical.
FROM python:3.12-alpine

# GHSA-6q48-6c7w-9gw2: patched pip and certifi, before application packages.
# The base image marks its Python as externally managed, same as the install below.
RUN pip3 install --no-cache-dir --break-system-packages --upgrade \
    pip==26.2.2 'certifi>=2026.10.19'

# viewtron 1.4.0 adds plate direction, confidence, list, and vehicle
# attributes, and reports the camera's event time. Pass VIEWTRON_SPEC when
# that release is not on PyPI yet.
ARG VIEWTRON_SPEC="viewtron>=1.4.0"
RUN pip3 install --no-cache-dir --break-system-packages \
    "${VIEWTRON_SPEC}" "paho-mqtt>=2.0" requests pyyaml

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
