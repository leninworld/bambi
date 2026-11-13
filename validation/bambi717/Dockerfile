FROM python@sha256:dddbf9c143d4d3dff1c68e104645bac612823d33ef09571c71eff41faf07c194
LABEL purpose="bambi717-native-baseline"
RUN useradd --uid 1000 --create-home worker && mkdir /work && chown 1000:1000 /work
USER 1000:1000
WORKDIR /work
ENTRYPOINT ["/bin/sleep", "infinity"]
