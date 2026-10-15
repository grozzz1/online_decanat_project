#!/usr/bin/env bash
set -Eeuo pipefail

if [[ $(id -u) -ne 0 ]]; then
    echo "Используй sudo site ${*:-status}" >&2
    exit 1
fi
app_dir=/opt/online-decanat
python="$app_dir/.venv/bin/python"
manage="$app_dir/deploy/manage.py"
action=${1:-status}
shift || true

case "$action" in
    start)
        if [[ $("$python" "$manage" ai-enabled) == 1 ]]; then systemctl start online-decanat-ai; else systemctl stop online-decanat-ai; fi
        systemctl start online-decanat
        ;;
    stop)
        systemctl stop online-decanat
        systemctl stop online-decanat-ai
        ;;
    restart)
        systemctl stop online-decanat
        if [[ $("$python" "$manage" ai-enabled) == 1 ]]; then systemctl restart online-decanat-ai; else systemctl stop online-decanat-ai; fi
        systemctl start online-decanat
        ;;
    status)
        if [[ $("$python" "$manage" ai-enabled) == 1 ]]; then
            systemctl --no-pager --full status online-decanat online-decanat-ai
        else
            systemctl --no-pager --full status online-decanat
            echo "Локальная модель выключена в конфигурации."
        fi
        ;;
    logs)
        case "${1:-app}" in
            app) journalctl -u online-decanat -n 100 -f ;;
            ai) journalctl -u online-decanat-ai -n 100 -f ;;
            all) journalctl -u online-decanat -u online-decanat-ai -n 100 -f ;;
            *) echo "Используй site logs app|ai|all" >&2; exit 2 ;;
        esac
        ;;
    doctor)
        runuser -u online-decanat -- "$python" "$manage" doctor
        ;;
    config)
        nano /etc/online-decanat.env
        echo "После изменений выполни: site restart"
        ;;
    url)
        "$python" "$manage" url "${1:?Используй site url https://example.org}"
        systemctl restart online-decanat
        ;;
    domain)
        "$python" "$manage" domain "${1:?Используй site domain example.org admin@example.org}" "${2:?Укажи email для сертификата}"
        systemctl restart online-decanat
        ;;
    update)
        source_dir=$(realpath -- "${1:?Используй site update /путь/к/новым/файлам}")
        exec bash "$source_dir/deploy/install.sh"
        ;;
    *)
        echo "site start|stop|restart|status|logs [app|ai|all]|doctor|config|url URL|domain DOMAIN EMAIL|update DIRECTORY"
        exit 2
        ;;
esac
