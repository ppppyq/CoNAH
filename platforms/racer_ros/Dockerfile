ARG BASE_IMAGE=osrf/ros:noetic-desktop-full
FROM ${BASE_IMAGE}

ARG BUILD_JOBS=4
ENV DEBIAN_FRONTEND=noninteractive
SHELL ["/bin/bash", "-c"]

# 1. System tools for compiling NLopt, LKH-3, and RACER (Ubuntu focal mirrors)
RUN rm -f /etc/apt/sources.list.d/ros*.list && \
    apt-get update && apt-get install -y --no-install-recommends \
      git wget curl ca-certificates cmake build-essential libdw-dev \
    && rm -rf /var/lib/apt/lists/*

# 2. Build & install NLopt v2.7.1
RUN git clone --depth 1 -b v2.7.1 https://github.com/stevengj/nlopt.git /tmp/nlopt && \
    mkdir -p /tmp/nlopt/build && cd /tmp/nlopt/build && \
    cmake .. && make -j"${BUILD_JOBS}" && make install && ldconfig && \
    rm -rf /tmp/nlopt

# 3. Build & install LKH-3 (from clean GitHub mirror)
RUN git clone --depth 1 https://github.com/cerebis/LKH3.git /tmp/LKH3 && \
    cd /tmp/LKH3 && make -j"${BUILD_JOBS}" && cp LKH /usr/local/bin/LKH && \
    rm -rf /tmp/LKH3

# 4. Clone & compile SYSU-STAR/RACER in /root/racer_ws
RUN mkdir -p /root/racer_ws/src && \
    git clone --depth 1 https://github.com/SYSU-STAR/RACER.git /root/racer_ws/src/RACER && \
    source /opt/ros/noetic/setup.bash && \
    cd /root/racer_ws && \
    catkin_make -j"${BUILD_JOBS}" -DCMAKE_BUILD_TYPE=Release

RUN echo "source /opt/ros/noetic/setup.bash" >> /root/.bashrc && \
    echo "source /root/racer_ws/devel/setup.bash" >> /root/.bashrc

WORKDIR /root/racer_ws
CMD ["/bin/bash"]
