FROM alpine:3.20
RUN apk add --no-cache socat
EXPOSE 1883
# ENTRYPOINT (not CMD alone): Railway startCommand overrides must not leave bare `socat` with 0 args.
# alpine/socat image ENTRYPOINT is `socat`; empty startCommand caused: "exactly 2 addresses required (there are 0)".
ENTRYPOINT ["socat", "TCP-LISTEN:1883,fork,reuseaddr", "TCP:mqtt.railway.internal:1883"]
