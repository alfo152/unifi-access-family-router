ARG BUILD_FROM
FROM ${BUILD_FROM}

# EN: the router only uses the Python standard library: no pip install needed.
# IT: il router usa solo la libreria standard di Python: niente pip install.
COPY app /app
COPY run.sh /run.sh
RUN chmod a+x /run.sh

ENV PYTHONPATH=/app \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

CMD [ "/run.sh" ]
