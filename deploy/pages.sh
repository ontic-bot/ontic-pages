#!/bin/sh
# Install the ontic-pages gateway on the box, or check that it could be. As root, on the box.
#
#   deploy/pages.sh check     [secrets.env]   validate, change nothing (the default)
#   deploy/pages.sh install   [secrets.env]   write /etc/ontic-pages, build, start, add the Caddy site
#   deploy/pages.sh status
#
# secrets.env defaults to deploy/secrets.env next to this script. Never touches
# /etc/caddy/Caddyfile and never restarts Caddy (validate, then reload). Never prints a secret.
set -eu
case $- in *x*) echo "pages.sh: refusing to run under 'set -x' (it would print secrets)" >&2; exit 2 ;; esac

HERE=$(cd "$(dirname "$0")" && pwd)
CHECKOUT=$(dirname "$HERE")
MODE=${1:-check}
SRC=${2:-$HERE/secrets.env}
ETC=/etc/ontic-pages
QUADLET=/etc/containers/systemd
SITE=/etc/caddy/conf.d/pages.caddy
IMAGE=localhost/ontic-pages:latest
UNITS="pages.network pages-gateway.container pages-oauth2-proxy.container"
REQUIRED="GOOGLE_CLIENT_ID GOOGLE_CLIENT_SECRET OAUTH2_COOKIE_SECRET OAUTH2_EMAIL_DOMAIN"
REQUIRED="$REQUIRED ONTIC_PAGES_BUCKET ONTIC_PAGES_ENDPOINT ONTIC_PAGES_REGION AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY"
REQUIRED="$REQUIRED ONTIC_PAGES_TOKEN_SECRET"
OPTIONAL="ONTIC_PAGES_PREFIX ONTIC_PAGES_GITHUB_TOKEN ONTIC_PAGES_GITHUB_REPO ONTIC_PAGES_GITHUB_OWNER ONTIC_PAGES_DIRECT_FILES"

die() { echo "pages.sh: $*" >&2; exit 1; }

# The value of KEY in the secrets file. Parsed, never sourced.
val() { sed -n "s/^$1=//p" "$SRC" | tail -n 1; }

check_secrets() {
    [ -r "$SRC" ] || die "no secrets file at $SRC (cp $HERE/secrets.env.example $SRC; chmod 600; fill in)"
    perms=$(stat -c '%a' "$SRC" 2>/dev/null || stat -f '%Lp' "$SRC")
    case "$perms" in *00) ;; *) die "$SRC is mode $perms; chmod 600 it" ;; esac
    # shellcheck disable=SC2013  # keys are single words
    for key in $(grep -v -e "^#" -e "^\$" "$SRC" | cut -d= -f1); do
        case " $REQUIRED $OPTIONAL " in *" $key "*) ;; *) die "$SRC: unknown key '$key'" ;; esac
    done
    for key in $REQUIRED; do
        v=$(val "$key")
        [ -n "$v" ] || die "$SRC: $key is empty"
        case "$v" in *\"*|*\\*|*\|*|*\&*) die "$SRC: $key contains one of \" \\ | &" ;; esac
    done
    [ "$(val ONTIC_PAGES_TOKEN_SECRET | tr -d '\n' | wc -c | tr -d ' ')" -ge 32 ] \
        || die "$SRC: ONTIC_PAGES_TOKEN_SECRET is too short (openssl rand -base64 32)"
    if [ -n "$(val ONTIC_PAGES_GITHUB_TOKEN)" ]; then
        case "$(val ONTIC_PAGES_GITHUB_TOKEN)" in github_pat_*|ghp_*) ;;
            *) die "$SRC: ONTIC_PAGES_GITHUB_TOKEN is not a GitHub token (github_pat_...); leave it empty for no sync" ;; esac
        val ONTIC_PAGES_GITHUB_OWNER | grep -Eq '^[^@ ]+@[^@ ]+$' \
            || die "$SRC: ONTIC_PAGES_GITHUB_TOKEN needs ONTIC_PAGES_GITHUB_OWNER (an email)"
    fi
    echo "ok    $SRC is complete (no value printed)"
}

check_box() {
    for c in podman systemctl caddy; do command -v "$c" >/dev/null || die "$c is missing"; done
    grep -Eq '^[[:space:]]*import[[:space:]]+(/etc/caddy/)?conf\.d/\*' /etc/caddy/Caddyfile \
        || die "/etc/caddy/Caddyfile does not import conf.d/*; add the import or install $SITE by hand"
    caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1 \
        || die "/etc/caddy/Caddyfile does not validate as it stands; fix that first"
    grep -Eq 'ask[[:space:]]+http://127\.0\.0\.1:8790/_tls-ask' /etc/caddy/Caddyfile \
        || die "/etc/caddy/Caddyfile has no global 'on_demand_tls { ask http://127.0.0.1:8790/_tls-ask }'; add it (deploy/README.md)"
    echo "ok    podman, systemctl, caddy; Caddyfile imports conf.d, asks /_tls-ask and validates"
}

check_download_cors() {
    setting=$(val ONTIC_PAGES_DIRECT_FILES | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')
    case "$setting" in ""|1|true|yes) ;; *) return ;; esac
    command -v python3 >/dev/null || die "python3 is required for the live download CORS check"
    python3 "$HERE/check-cors.py" "$(val ONTIC_PAGES_ENDPOINT)" "$(val ONTIC_PAGES_BUCKET)" \
        || die "download CORS is not ready; services were not changed"
}

write_etc() {
    umask 077
    mkdir -p "$ETC"
    for key in $REQUIRED $OPTIONAL; do
        case $key in ONTIC_PAGES_*|AWS_*) v=$(val "$key"); [ -z "$v" ] || echo "$key=$v" ;; esac
    done > "$ETC/gateway.env"
    sed -e "s|^client_id .*|client_id     = \"$(val GOOGLE_CLIENT_ID)\"|" \
        -e "s|^client_secret .*|client_secret = \"$(val GOOGLE_CLIENT_SECRET)\"|" \
        -e "s|^cookie_secret .*|cookie_secret = \"$(val OAUTH2_COOKIE_SECRET)\"|" \
        -e "s|^email_domains .*|email_domains = [\"$(val OAUTH2_EMAIL_DOMAIN)\"]|" \
        "$HERE/oauth2-proxy.cfg.example" > "$ETC/oauth2-proxy.cfg"
    chown 65532:65532 "$ETC/oauth2-proxy.cfg"
    echo "ok    wrote $ETC/gateway.env and $ETC/oauth2-proxy.cfg (mode 600)"
}

do_install() {
    [ "$(id -u)" = 0 ] || die "install must run as root"
    check_secrets
    check_box
    check_download_cors
    write_etc
    podman build -t "$IMAGE" --build-arg "REVISION=$(git -C "$CHECKOUT" rev-parse --short HEAD)" \
        --ignorefile "$HERE/Dockerfile.dockerignore" -f "$HERE/Dockerfile" "$CHECKOUT"
    for u in $UNITS; do install -m 0644 "$HERE/$u" "$QUADLET/$u"; done
    systemctl daemon-reload
    backup=$(mktemp)
    [ -f "$SITE" ] && cp "$SITE" "$backup"
    install -m 0640 -o root -g caddy "$HERE/pages.caddy" "$SITE"
    if ! caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1; then
        if [ -s "$backup" ]; then cp "$backup" "$SITE"; else rm -f "$SITE"; fi
        rm -f "$backup"
        die "caddy validate failed with the new $SITE; restored the previous one"
    fi
    rm -f "$backup"
    systemctl reload caddy
    # Install log redaction before the new gateway can issue signed redirects.
    systemctl restart pages-network.service pages-gateway.service pages-oauth2-proxy.service
    echo "ok    units started"
    echo "ok    caddy reloaded; https://pages.onticlabs.io/ should redirect to Google sign-in"
}

case $MODE in
check) check_secrets; check_box; check_download_cors; echo "check passed; run: $0 install" ;;
install) do_install ;;
status)
    for u in pages-network pages-oauth2-proxy pages-gateway; do
        printf '%-20s %s\n' "$u" "$(systemctl is-active "$u" 2>/dev/null || true)"
    done
    curl -fsS http://127.0.0.1:8790/_health || echo "gateway: not answering" ;;
*) sed -n '2,9p' "$0"; exit 2 ;;
esac
