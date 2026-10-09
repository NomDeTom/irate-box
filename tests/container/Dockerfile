# SPDX-License-Identifier: MIT
# SPDX-FileCopyrightText: 2026 NomDeTom
# The systemd Debian trixie box the container tests (ct-*.sh) run in: the same OS the hub
# installs onto, with systemd as PID 1 so units, transient jobs and journald all work.
# install.sh does the real work at test time; the packages it would apt-install are baked in
# here so each run is fast and needs no network (the hub's own release downloads — ttyd,
# SilverBullet — come from a --download-cache the tests pass, not from here).
# Built and run by tests/container/run.sh; see that script for the recipe.
FROM debian:trixie
ENV DEBIAN_FRONTEND=noninteractive container=docker
# The packages install.sh's core would pull (install.sh pkgs=(...)), plus nginx+openssl and
# Debian's caddy for the --web caddy path, plus git/curl the ct-*.sh themselves use.
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
        systemd systemd-sysv \
        python3 curl ca-certificates git unzip fcgiwrap cgit python3-markdown \
        libjs-highlight.js iw dnsmasq-base nftables procps gpgv \
        nginx openssl caddy \
    >/dev/null && \
    apt-get clean && rm -rf /var/lib/apt/lists/*
# systemd in a container: drop the units that make no sense without real hardware/boot, so
# `systemctl is-system-running` reaches a steady state instead of degraded-forever.
RUN rm -f /lib/systemd/system/multi-user.target.wants/* \
          /etc/systemd/system/*.wants/* \
          /lib/systemd/system/local-fs.target.wants/* \
          /lib/systemd/system/sockets.target.wants/*udev* \
          /lib/systemd/system/sockets.target.wants/*initctl* \
          /lib/systemd/system/sysinit.target.wants/systemd-tmpfiles-setup* \
          /lib/systemd/system/systemd-update-utmp* && \
    systemctl mask systemd-logind.service getty.target 2>/dev/null || true
STOPSIGNAL SIGRTMIN+3
CMD ["/sbin/init"]
