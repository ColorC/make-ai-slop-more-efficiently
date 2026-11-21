# omni remote job: read-only state of a registered background job (pid + creation time must both match).
$name = __OMNI_JOB_NAME__
$registry = Join-Path __OMNI_JOBS_ROOT__ ($name + '.json')
$tail = [int]__OMNI_TAIL__
$out = [ordered]@{ schema = 'omni.remote.job-state/1'; ok = $false; state = 'unknown'; name = $name; registry = $registry; op_id = $null
  pid = $null; alive = $false; descendants = @(); result = $null; log_tail = @(); console_tail = @(); paths = $null; error = $null }
try {
  if (-not (Test-Path -LiteralPath $registry)) { $out.ok = $true; $out.error = "omni-remote: no registered job named '$name'" } else {
    $j = Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json
    $out.op_id = $j.op_id
    $out.pid = $j.pid
    $out.paths = $j.paths
    $p = Omni-AliveProcess $j.pid $j.created_utc_ticks
    if (Test-Path -LiteralPath $j.paths.result) { $out.result = Get-Content -LiteralPath $j.paths.result -Raw -Encoding UTF8 | ConvertFrom-Json }
    if ($p) {
      $out.alive = $true
      $out.state = 'running'
      $out.descendants = @(Omni-Descendants $p | ForEach-Object { [ordered]@{ pid = [int]$_.ProcessId; name = $_.Name } })
    } elseif ($j.killed_at) { $out.state = 'killed' }
    elseif ($out.result) { $out.state = 'exited' }
    else { $out.state = 'exited-without-result' }
    if ($tail -gt 0) {
      $out.log_tail = @(Omni-TailLines $j.paths.log $tail)
      $out.console_tail = @(Omni-TailLines $j.paths.console $tail)
    }
    $out.ok = $true
  }
} catch { $out.error = $_.Exception.Message }
Omni-Emit $out
if ($out.ok) { exit 0 } else { exit 2 }
