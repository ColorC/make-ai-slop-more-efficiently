# omni remote kill: stop only the registered job's process tree, each pid re-verified by creation time. Never by name.
$name = __OMNI_JOB_NAME__
$registry = Join-Path __OMNI_JOBS_ROOT__ ($name + '.json')
$out = [ordered]@{ schema = 'omni.remote.kill/1'; ok = $false; state = $null; name = $name; registry = $registry; pid = $null; killed = @(); skipped = @(); error = $null }
try {
  if (-not (Test-Path -LiteralPath $registry)) { $out.state = 'unknown'; throw "omni-remote: no registered job named '$name'; nothing killed" }
  $j = Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json
  $out.pid = $j.pid
  $root = Omni-AliveProcess $j.pid $j.created_utc_ticks
  if (-not $root) { $out.state = 'not-running'; $out.ok = $true } else {
    $tree = New-Object System.Collections.Generic.List[object]
    $tree.Add($root)
    foreach ($d in @(Omni-Descendants $root)) { $tree.Add($d) }
    $tree.Reverse()
    $killed = New-Object System.Collections.Generic.List[object]
    $skipped = New-Object System.Collections.Generic.List[object]
    foreach ($q in $tree) {
      $check = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $q.ProcessId) -ErrorAction SilentlyContinue
      # a parent often exits by itself once its child is stopped (cmd.exe /c): that is 'already-exited', not a failure
      if (-not $check) { $skipped.Add([ordered]@{ pid = [int]$q.ProcessId; name = $q.Name; reason = 'already-exited' }); continue }
      if ($check.CreationDate -ne $q.CreationDate) { $skipped.Add([ordered]@{ pid = [int]$q.ProcessId; name = $q.Name; reason = 'pid-reused' }); continue }
      try {
        Stop-Process -Id $q.ProcessId -Force -ErrorAction Stop
        $killed.Add([ordered]@{ pid = [int]$q.ProcessId; name = $q.Name })
      } catch { $skipped.Add([ordered]@{ pid = [int]$q.ProcessId; name = $q.Name; reason = $_.Exception.Message }) }
    }
    $out.killed = $killed.ToArray()
    $out.skipped = $skipped.ToArray()
    Start-Sleep -Milliseconds 300
    $out.root_alive_after = [bool](Omni-AliveProcess $j.pid $j.created_utc_ticks)
    if ($out.root_alive_after) { throw "omni-remote: job root pid $($j.pid) still alive after kill" }
    $out.state = 'killed'
    $j | Add-Member -NotePropertyName killed_at -NotePropertyValue ((Get-Date).ToString('o')) -Force
    $j | Add-Member -NotePropertyName killed -NotePropertyValue $out.killed -Force
    $json = $j | ConvertTo-Json -Depth 6
    [IO.File]::WriteAllText($registry, $json, $OmniUtf8)
    if ($j.paths.job) { [IO.File]::WriteAllText($j.paths.job, $json, $OmniUtf8) }
    if ($j.paths.result -and -not (Test-Path -LiteralPath $j.paths.result)) {
      $marker = [ordered]@{ schema = 'omni.remote.result/1'; producer = 'omni remote kill'; op_id = $j.op_id; phase = 'killed'; status = 'killed'; exit_code = $null; killed_at = $j.killed_at }
      [IO.File]::WriteAllText($j.paths.result, ($marker | ConvertTo-Json -Depth 4), $OmniUtf8)
    }
    $out.ok = $true
  }
} catch { $out.error = $_.Exception.Message }
Omni-Emit $out
if ($out.ok) { exit 0 } else { exit 3 }
