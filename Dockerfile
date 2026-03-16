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
    git \
    libssl-dev \
    unzip \
    netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt

# Install wrk
RUN git clone https://github.com/wg/wrk.git && \
    cd wrk && \
    make && \
    cp wrk /usr/local/bin/

# Install hey
RUN curl -L https://hey-release.s3.us-east-2.amazonaws.com/hey_linux_amd64 \
    -o /usr/local/bin/hey && \
    chmod +x /usr/local/bin/hey

WORKDIR /sandbox

RUN useradd -m grader
USER grader

CMD ["/bin/bash"]