# Sincroniza tu base de datos local con una foto de produccion.
#
# Exporta dispositivos + medidas de produccion via `psql \copy` y las
# importa a tu base local con import_all_csv. Ese comando solo anade filas
# nuevas (ignore_conflicts=True + el campo `timestamp` ya arreglado), asi
# que puedes relanzar este script tantas veces como quieras sin duplicar
# nada.
#
# Uso:
#   .\scripts\sync_from_production.ps1                     # todo el historico
#   .\scripts\sync_from_production.ps1 -Since "2026-09-25" # solo desde esa fecha (mas rapido)
#
# Te pedira la contrasena de produccion de forma interactiva (el prompt
# propio de psql) -- no se guarda en ningun sitio.

param(
    [string]$Since = ""
)

$ErrorActionPreference = "Stop"

$ProdHost = "opal9.opalstack.com"
$ProdPort = "5432"
$ProdDb   = "domoboi"
$ProdUser = "domoboi"

$DevicesCsv      = "dispositivos_export.csv"
$MeasurementsCsv = "medidas_todos.csv"

$ConnInfo = "host=$ProdHost port=$ProdPort dbname=$ProdDb user=$ProdUser"

Write-Host "Exportando dispositivos desde produccion..."
psql $ConnInfo -c "\copy (SELECT d.device_id, d.model, d.device_type, COALESCE(l.description,'') AS location_desc, COALESCE(l.address,'') AS location_address FROM nilm_device d LEFT JOIN nilm_location l ON l.id = d.location_id) TO '$DevicesCsv' WITH CSV HEADER"

$MeasurementsQuery = "SELECT d.device_id, m.start_time, m.end_time, m.value, m.readings, m.features, m.telemetry FROM nilm_measurement m JOIN nilm_device d ON d.id = m.device_id"
if ($Since -ne "") {
    $MeasurementsQuery += " WHERE m.start_time >= '$Since'"
    Write-Host "Exportando medidas desde produccion (desde $Since)..."
} else {
    Write-Host "Exportando TODAS las medidas desde produccion (puede tardar varios minutos)..."
}
psql $ConnInfo -c "\copy ($MeasurementsQuery) TO '$MeasurementsCsv' WITH CSV HEADER"

Write-Host "Importando a tu base de datos local..."
python manage.py import_all_csv $DevicesCsv $MeasurementsCsv

Write-Host "Listo. Tu base local ya refleja produccion."
