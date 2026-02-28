FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y \
    build-essential \
    gcc \
    make \
    python3 \
    apache2-utils \
    bison \
    flex \
    curl \
    iproute2 \
    iputils-ping \
    net-tools \
    vim \
    procps \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /sandbox

RUN useradd -m grader
USER grader

CMD ["/bin/bash"]