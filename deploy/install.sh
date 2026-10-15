#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

if [[ $(id -u) -ne 0 ]]; then
    echo "Запусти: sudo bash deploy/install.sh" >&2
    exit 1
fi
. /etc/os-release
if [[ ${ID:-} != ubuntu || ${VERSION_ID:-} != 24.04 ]]; then
    echo "Этот установщик рассчитан на Ubuntu 24.04 x64 с systemd." >&2
    exit 1
fi
if [[ $(uname -m) != x86_64 ]]; then
    echo "Нужен сервер x86_64." >&2
    exit 1
fi

source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
app_dir=/opt/online-decanat
env_file=/etc/online-decanat.env
service_user=online-decanat
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3.12 python3.12-venv tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng tesseract-ocr-osd libgomp1 ca-certificates curl rsync nginx certbot python3-certbot-nginx nano

if ! id "$service_user" >/dev/null 2>&1; then
    useradd --system --home-dir /var/lib/online-decanat --shell /usr/sbin/nologin "$service_user"
fi
install -d -m 0755 "$app_dir"
install -d -o "$service_user" -g "$service_user" -m 0750 /var/lib/online-decanat
systemctl stop online-decanat.service online-decanat-ai.service 2>/dev/null || true
if [[ $source_dir != "$app_dir" ]]; then
    rsync -a --safe-links \
        --exclude='.git/' --exclude='.idea/' --exclude='.venv/' --exclude='.env' \
        --exclude='.tmp/' --exclude='private_data/' --exclude='uploads/' --exclude='logs/' \
        --exclude='models/' --exclude='tools/tesseract/' --exclude='tools/llama.cpp/' \
        --exclude='.local_ai/' --exclude='benchmark_results/' --exclude='__pycache__/' \
        --exclude='results.json' "$source_dir/" "$app_dir/"
fi
python3.12 -m venv "$app_dir/.venv"
"$app_dir/.venv/bin/python" -m pip install --disable-pip-version-check -r "$app_dir/requirements.txt"
"$app_dir/.venv/bin/python" -m pip check
if [[ ! -f $env_file ]]; then
    if [[ -f $source_dir/.env ]]; then
        install -o root -g "$service_user" -m 0640 "$source_dir/.env" "$env_file"
    else
        install -o root -g "$service_user" -m 0640 "$app_dir/.env.example" "$env_file"
    fi
fi
"$app_dir/.venv/bin/python" "$app_dir/deploy/manage.py" init
chown root:"$service_user" "$env_file"
chmod 0640 "$env_file"

cat > /etc/systemd/system/online-decanat.service <<'UNIT'
[Unit]
Description=Online Decanat bot and web application
After=network-online.target online-decanat-ai.service
Wants=network-online.target

[Service]
Type=simple
User=online-decanat
Group=online-decanat
WorkingDirectory=/opt/online-decanat
EnvironmentFile=/etc/online-decanat.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
ExecStart=/opt/online-decanat/.venv/bin/python /opt/online-decanat/main.py
Restart=on-failure
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=45
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/online-decanat

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/online-decanat-ai.service <<'UNIT'
[Unit]
Description=Online Decanat local Qwen model
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=online-decanat
Group=online-decanat
WorkingDirectory=/opt/online-decanat
EnvironmentFile=/etc/online-decanat.env
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=LOCAL_AI_THREADS=2
ExecStart=/opt/online-decanat/.venv/bin/python /opt/online-decanat/scripts/start_local_ai.py --threads ${LOCAL_AI_THREADS}
Restart=on-failure
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=45
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/online-decanat

[Install]
WantedBy=multi-user.target
UNIT

if [[ -L /etc/nginx/sites-enabled/default ]] && [[ $(readlink -f /etc/nginx/sites-enabled/default) == /etc/nginx/sites-available/default ]]; then
    mv /etc/nginx/sites-enabled/default /etc/nginx/online-decanat-previous-default
fi
"$app_dir/.venv/bin/python" "$app_dir/deploy/manage.py" nginx
ln -sfn /etc/nginx/sites-available/online-decanat /etc/nginx/sites-enabled/online-decanat
nginx -t
systemctl enable --now nginx
systemctl reload nginx
install -o root -g root -m 0755 "$app_dir/deploy/site.sh" /usr/local/bin/site
systemctl daemon-reload

if [[ $("$app_dir/.venv/bin/python" "$app_dir/deploy/manage.py" ai-enabled) == 1 ]]; then
    "$app_dir/.venv/bin/python" "$app_dir/scripts/setup_local_ai.py"
    systemctl enable online-decanat-ai.service
else
    systemctl disable online-decanat-ai.service 2>/dev/null || true
fi
chown -hR root:"$service_user" "$app_dir"
chmod -R g+rX,o-rwx "$app_dir"
runuser -u "$service_user" -- "$app_dir/.venv/bin/python" "$app_dir/deploy/manage.py" doctor --offline
systemctl enable online-decanat.service
site start
for attempt in $(seq 1 60); do
    if curl --silent --fail http://127.0.0.1:5000/healthz >/dev/null; then
        echo "Приложение запущено. Управление: site status, site logs, site restart."
        exit 0
    fi
    sleep 1
done
echo "Не удалось дождаться запуска. Проверь: site logs" >&2
exit 1
