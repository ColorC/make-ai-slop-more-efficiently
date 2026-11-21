[CmdletBinding()]
param(
    [ValidateSet('Verify')]
    [string]$Mode
)

if ($Mode -ne 'Verify') {
    Write-Error 'Mode must be Verify.'
    exit 2
}

$edition = $PSVersionTable.PSEdition
if (-not $edition) {
    $edition = 'Desktop'
}

Write-Output 'DIALECT=PowerShell'
Write-Output ('EDITION={0}' -f $edition)
Write-Output 'RESULT=agent-shell-route-ok'
