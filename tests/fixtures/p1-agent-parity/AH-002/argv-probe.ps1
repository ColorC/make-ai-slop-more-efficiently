[CmdletBinding()]
param(
    [switch]$Help,
    [string]$Keys
)

if ($Help) {
    Write-Output 'USAGE: .\argv-probe.ps1 -Keys <comma-separated-string>'
    Write-Output 'CONTRACT: Keys must arrive as one string argument.'
    exit 0
}

if (-not $PSBoundParameters.ContainsKey('Keys')) {
    Write-Error 'Keys is required.'
    exit 2
}

Write-Output 'ARG_COUNT=1'
Write-Output ('ITEM_COUNT={0}' -f $Keys.Split(',').Count)
Write-Output ('VALUE={0}' -f $Keys)
