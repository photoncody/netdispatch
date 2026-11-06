# netdispatch SMB sidecar: read-only share of extracted ISO trees, used by
# WinPE to run Windows Setup (setup.exe + install.wim) over the network.
FROM alpine:3.22
RUN apk add --no-cache samba-server samba-common-tools tini
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod 0755 /usr/local/bin/entrypoint.sh
EXPOSE 445
ENTRYPOINT ["/sbin/tini", "--", "/usr/local/bin/entrypoint.sh"]
