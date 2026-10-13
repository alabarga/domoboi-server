#!/usr/bin/env bash
# Instala las dos tareas cron que recogen los datos de los enchufes Tuya.
#
# Uso (desde cualquier carpeta, con el usuario del servidor que ejecuta Django):
#     bash scripts/install_tuya_cron.sh            # muestra y pide confirmación
#     bash scripts/install_tuya_cron.sh --yes      # instala sin preguntar
#     bash scripts/install_tuya_cron.sh --remove   # quita las tareas instaladas
#
# Es idempotente: si ya hay tareas de este script, las sustituye (no duplica).
# No toca ninguna otra línea del crontab.
#
# Variables opcionales:
#     PYTHON=/ruta/al/python   intérprete a usar (por defecto .venv/bin/python o venv/bin/python)

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MARK_BEGIN="# >>> domoboi tuya_poll >>>"
MARK_END="# <<< domoboi tuya_poll <<<"

mode="install"
assume_yes=0
for arg in "$@"; do
    case "$arg" in
        --yes) assume_yes=1 ;;
        --remove) mode="remove" ;;
        -h|--help) sed -n '2,13p' "${BASH_SOURCE[0]}"; exit 0 ;;
        *) echo "Opción desconocida: $arg" >&2; exit 2 ;;
    esac
done

if ! command -v crontab >/dev/null 2>&1; then
    echo "ERROR: no se encuentra el comando 'crontab' en este servidor." >&2
    exit 1
fi

# Crontab actual sin nuestro bloque (si existía).
current="$( (crontab -l 2>/dev/null || true) | sed "/^${MARK_BEGIN//\//\\/}\$/,/^${MARK_END//\//\\/}\$/d" )"

if [ "$mode" = "remove" ]; then
    printf '%s\n' "$current" | crontab -
    echo "Tareas de tuya_poll eliminadas del crontab."
    exit 0
fi

# --- Intérprete de Python -----------------------------------------------------
if [ -n "${PYTHON:-}" ]; then
    PY="$PYTHON"
elif [ -x "$PROJECT_DIR/.venv/bin/python" ]; then
    PY="$PROJECT_DIR/.venv/bin/python"
elif [ -x "$PROJECT_DIR/venv/bin/python" ]; then
    PY="$PROJECT_DIR/venv/bin/python"
else
    echo "ERROR: no encuentro el entorno virtual (.venv/ o venv/) en $PROJECT_DIR." >&2
    echo "       Indica el Python con:  PYTHON=/ruta/al/python bash $0" >&2
    exit 1
fi

# --- Comprobaciones previas (avisan, no bloquean) ------------------------------
if ! "$PY" -c "import tuya_energy" >/dev/null 2>&1; then
    echo "AVISO: el paquete 'tuya_energy' no está instalado en ese entorno." >&2
    echo "       Instálalo antes (pip install -e /ruta/a/domoboi-tuya o el requirements.txt)." >&2
fi
if ! grep -qs '^TUYA_ACCESS_ID=' "$PROJECT_DIR/.env" 2>/dev/null; then
    echo "AVISO: no veo TUYA_ACCESS_ID en $PROJECT_DIR/.env (puede estar en otro sitio)." >&2
fi

mkdir -p "$PROJECT_DIR/logs"
LOG="$PROJECT_DIR/logs/tuya_poll.log"
FLOCK="$(command -v flock || true)"
LOCK_PREFIX=""
[ -n "$FLOCK" ] && LOCK_PREFIX="$FLOCK -n /tmp/tuya_poll.lock "

# cron trata % como salto de línea: hay que escaparlo.
block="$MARK_BEGIN
# Histórico real de Tuya, cada día a las 03:17. Mira 2 días atrás para que un fallo se recupere solo.
17 3 * * * cd $PROJECT_DIR && ${LOCK_PREFIX}$PY manage.py tuya_poll --history --since \"\$(date -d '2 days ago' +\\%F)\" >> $LOG 2>&1
# Lectura en vivo cada 15 minutos (mantiene el estado is_active del admin).
*/15 * * * * cd $PROJECT_DIR && $PY manage.py tuya_poll --once >> $LOG 2>&1
# Rotación semanal del log.
0 4 * * 0 cd $PROJECT_DIR && mv $LOG $LOG.1 2>/dev/null
$MARK_END"

echo "Se instalarán estas líneas en el crontab de $(id -un):"
echo
printf '%s\n' "$block"
echo

if [ "$assume_yes" -ne 1 ]; then
    read -r -p "¿Instalar? [s/N] " answer
    case "$answer" in s|S|si|SI|y|Y) ;; *) echo "Cancelado."; exit 0 ;; esac
fi

if [ -n "$current" ]; then
    printf '%s\n%s\n' "$current" "$block" | crontab -
else
    printf '%s\n' "$block" | crontab -
fi

echo "Hecho. Compruébalo con:  crontab -l"
echo "Y en unos minutos:       tail -f $LOG   (debe verse 'stored X/Y reading(s)')"
