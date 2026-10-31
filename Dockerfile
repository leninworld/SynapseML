FROM eclipse-temurin@sha256:b7ff112d9bde19e97f7a8e906df4d585ebae3ac674187c43df8f427d61ff5bc5 AS jdk
FROM python@sha256:dde3796db6d2436a0cda34c64b78d748d20f9a593370d45a65db75311752b36e
COPY --from=jdk /opt/java/openjdk /opt/java/openjdk
RUN groupadd --gid 1001 validation && useradd --uid 1001 --gid 1001 --create-home validation && mkdir -p /opt/bootstrap
COPY --chmod=755 sbt conda /usr/local/bin/
ENV JAVA_HOME=/opt/java/openjdk
ENV PATH=/opt/java/openjdk/bin:/usr/local/bin:/usr/bin:/bin
ENTRYPOINT ["python3", "/packet/bootstrap.py"]
