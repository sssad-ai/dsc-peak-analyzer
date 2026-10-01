$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
try {
    $taskHealth = Invoke-RestMethod -Uri 'http://127.0.0.1:8501/_stcore/health' -TimeoutSec 2
    if ($taskHealth -eq 'ok') {
        Write-Host 'The local workbench is already running: http://127.0.0.1:8501'
        Start-Process 'http://127.0.0.1:8501'
        exit 0
    }
} catch {
    # No server yet; start it below.
}
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (Test-Path -LiteralPath $taskPython) {
    $taskArgs = @()
} elseif (Get-Command py.exe -ErrorAction SilentlyContinue) {
    $taskPython = 'py.exe'
    $taskArgs = @('-3.14')
    & $taskPython @taskArgs -c 'import sys' 2>$null
    if ($LASTEXITCODE -ne 0) { $taskArgs = @('-3') }
} else {
    $taskPython = 'python'
    $taskArgs = @()
}
& $taskPython @taskArgs -c 'import sys; assert sys.version_info >= (3,10); import streamlit, pandas, numpy, scipy, plotly, openpyxl; assert int(numpy.__version__.split(chr(46))[0]) >= 2'
if ($LASTEXITCODE -ne 0) {
    Write-Host 'Python 3.10+ and dependencies are required. See README.md.'
    exit 1
}
& $taskPython @taskArgs -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.showEmailPrompt false --browser.gatherUsageStats false
