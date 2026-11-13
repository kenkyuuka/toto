FROM python:3.14.6-slim AS base

FROM base AS build
RUN python3 -m venv /pyenv
ENV PATH="/pyenv/bin:$PATH"
RUN --mount=type=cache,target=/root/.cache python3 -m pip install -U pip wheel hatch

FROM build AS dev-deps
# tamago (archive extract/repack), kept in its own venv so its dependencies
# stay out of the hatch toolchain. The Makefile passes the latest release as
# TAMAGO_VERSION, so this layer rebuilds only when a new version is out; when
# unset, whatever is newest at build time is installed.
ARG TAMAGO_VERSION=
RUN --mount=type=cache,target=/root/.cache python3 -m venv /opt/tamago \
    && /opt/tamago/bin/pip install "tamagoarc${TAMAGO_VERSION:+==$TAMAGO_VERSION}" \
    && ln -s /opt/tamago/bin/tamago /usr/local/bin/tamago

FROM dev-deps AS dev
COPY . /workspace
WORKDIR /workspace
RUN hatch env create

FROM build AS final
COPY . /src
RUN --mount=type=cache,target=/root/.cache python3 -m pip install /src
WORKDIR /data
ENTRYPOINT ["toto"]
