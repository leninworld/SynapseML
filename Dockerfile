FROM node@sha256:a43f7fc3e52e87d12a905f46eea0275def84ca8f2981a5b53fed44d7fb398ee0 AS node
FROM python@sha256:dde3796db6d2436a0cda34c64b78d748d20f9a593370d45a65db75311752b36e
COPY --from=node /usr/local /opt/node
RUN groupadd --gid 1001 validation && useradd --uid 1001 --gid 1001 --create-home validation && mkdir -p /opt/bootstrap
ENV PATH=/opt/node/bin:/usr/local/bin:/usr/bin:/bin
ENTRYPOINT ["python3", "/packet/bootstrap.py"]
