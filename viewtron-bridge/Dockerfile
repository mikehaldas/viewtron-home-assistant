ARG BUILD_FROM
FROM ${BUILD_FROM}

RUN apk add --no-cache python3 py3-pip py3-yaml && \
    pip3 install --no-cache-dir --break-system-packages viewtron paho-mqtt requests

COPY run.sh /
COPY viewtron_bridge.py /

RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
