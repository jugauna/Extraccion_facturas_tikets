# Ejecutar desde autodoc-v2: .\start_local.ps1
# El servidor debe arrancar con el cwd en backend/ para que exista el paquete `app`.
Set-Location -Path $PSScriptRoot\backend
python -m uvicorn app.main:app --host 0.0.0.0 --port 8080
