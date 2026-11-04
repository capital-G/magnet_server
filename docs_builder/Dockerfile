# The image which builds the docs of one package (docs_plan.md): `magnet install`
# fetches the package and its dependencies, `sclang -l sclang_conf.yaml render.scd`
# renders its SCDoc help. A headless SuperCollider (no Qt, no X11, like
# ~/github/sc-docs-dev) and the magnet CLI, built from ../magnet_cli:
#
#   make docs-builder
#
# Bumping SC_VERSION means a new image tag and rebuilding the docs of every package.

ARG SC_VERSION=3.14.1
ARG DEBIAN=bookworm

FROM debian:${DEBIAN} AS supercollider
ARG SC_VERSION
ARG MAKE_JOBS=4

RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
        ca-certificates \
        git \
        build-essential \
        cmake \
        libsndfile1-dev \
        libfftw3-dev \
        libasound2-dev \
        libavahi-client-dev \
        libjack-jackd2-dev \
        libudev-dev && \
    rm -rf /var/lib/apt/lists/*

RUN git clone --depth 1 --branch "Version-${SC_VERSION}" --recurse-submodules \
        https://github.com/supercollider/supercollider.git /src

# only sclang (and scsynth, which can't be turned off) matter; NATIVE stays off, so the
# image runs on other machines of the same architecture
RUN cmake -S /src -B /build \
        -DCMAKE_BUILD_TYPE=Release \
        -DCMAKE_INSTALL_PREFIX=/opt/supercollider \
        -DNATIVE=OFF \
        -DSC_QT=OFF \
        -DSC_IDE=OFF \
        -DNO_X11=ON \
        -DSUPERNOVA=OFF \
        -DSCLANG_SERVER=OFF \
        -DSC_ABLETON_LINK=OFF \
        -DSC_HIDAPI=OFF \
        -DENABLE_TESTSUITE=OFF \
        -DSC_EL=OFF \
        -DSC_VIM=OFF && \
    cmake --build /build --parallel "${MAKE_JOBS}" && \
    cmake --install /build

# Without Qt (and Link), CMake leaves out every class folder named GUI (and Ableton), but
# packages extend and subclass those classes (Window, EZSlider, Pen, JITGui, LinkClock…),
# so their class library wouldn't compile. They're plain sclang: compiling them needs no
# Qt, only calling their primitives would fail, which documenting doesn't do.
RUN cd /src/SCClassLibrary && \
    find . -type d \( -name GUI -o -name Ableton \) -prune \
        -exec cp -R --parents {} /opt/supercollider/share/SuperCollider/SCClassLibrary/ \;


FROM rust:1-${DEBIAN} AS cli

WORKDIR /src
# the build context `magnet_cli` (../magnet_cli), without its target/
COPY --from=magnet_cli Cargo.toml Cargo.lock build.rs ./
COPY --from=magnet_cli schemes schemes
COPY --from=magnet_cli src src
RUN cargo build --release --locked


FROM debian:${DEBIAN}-slim
ARG SC_VERSION

# runtime libraries of sclang and magnet (`ldd`), certificates for the index/downloads
RUN apt-get update && \
    apt-get install --yes --no-install-recommends \
        ca-certificates \
        libsndfile1 \
        libfftw3-single3 \
        libasound2 \
        libavahi-client3 \
        libjack-jackd2-0 \
        libudev1 \
        libssl3 && \
    rm -rf /var/lib/apt/lists/*

COPY --from=supercollider /opt/supercollider /usr/local
COPY --from=cli /src/target/release/magnet /usr/local/bin/magnet
COPY render.scd /opt/magnet/render.scd

# builds run as this user unless the runner maps another one (`--userns=keep-id`);
# HOME should be a writable tmpfs, sclang creates its user app support folder there
RUN useradd --uid 1000 --create-home builder
USER builder
ENV HOME=/tmp/home \
    SC_VERSION=${SC_VERSION}
LABEL org.opencontainers.image.title="magnet docs builder" \
      org.supercollider.version=${SC_VERSION}
