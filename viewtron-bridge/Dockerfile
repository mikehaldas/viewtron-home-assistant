ARG BUILD_FROM=python:3.12-alpine
FROM ${BUILD_FROM}

RUN apk add --no-cache py3-yaml && \
    pip3 install --no-cache-dir --break-system-packages viewtron paho-mqtt requests pyyaml

COPY run.sh /
COPY viewtron_bridge.py /

RUN chmod a+x /run.sh

ENV PYTHONUNBUFFERED=1

CMD [ "/run.sh" ]
