# calibre-cli: your Calibre instance (desktop GUI + Content Server, via
# linuxserver/calibre) plus a small HTTP API for bookkeep to call.
#
# Built FROM linuxserver/calibre rather than reimplementing its GUI/VNC
# stack — calibredb/ebook-convert/calibre-server all come from that base
# image already. We only add a small FastAPI service, wired in as an
# additional s6-supervised service via linuxserver's own extension
# mechanism (https://docs.linuxserver.io/general/container-customization/),
# so the GUI and Content Server behave exactly as they do in a stock
# linuxserver/calibre container.
FROM lscr.io/linuxserver/calibre:latest

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /agent
COPY requirements.txt ./
RUN python3 -m venv /agent/venv && /agent/venv/bin/pip install --no-cache-dir -r requirements.txt

COPY app.py ./

# Runs as an additional service alongside Calibre's own GUI and Content
# Server — s6-overlay (this base image's init system) picks up any
# executable dropped here automatically.
COPY custom-services.d/calibre-cli /custom-services.d/calibre-cli
RUN chmod +x /custom-services.d/calibre-cli

# 8080/8181 (GUI) and 8081 (Content Server) are already EXPOSEd by the base
# image; this adds only the agent's own port.
EXPOSE 8100
