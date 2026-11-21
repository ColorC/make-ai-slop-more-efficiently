# omni remote occupancy: snapshot / summary / claim / release / alerts. summary and alerts are read-only.
$action = __OMNI_ACTION__
$params = (__OMNI_PARAMS__) | ConvertFrom-Json
$out = [ordered]@{ schema = 'omni.remote.occupancy-op/1'; ok = $false; action = $action; node = $OccNode; error = $null }
try {
  switch ($action) {
    'snapshot' {
      Occ-EnsureRoot
      $snap = Occ-Collect 'snapshot'
      Occ-SyncPolicy
      $out.new_alerts = @(Occ-Alert $snap)
      Occ-WriteJson $OccTablePath $snap
      $out.snapshot = $snap
    }
    'summary' {
      $out.table = $null
      if (Test-Path -LiteralPath $OccTablePath) { $out.table = Occ-ReadJson $OccTablePath }
      $out.alerts = @()
      if (Test-Path -LiteralPath $OccAlertsPath) {
        $out.alerts = @(Omni-TailLines $OccAlertsPath ([int]$params.limit) | ForEach-Object { try { $_ | ConvertFrom-Json } catch { } })
      }
      $out.watchdog_alive = $false
      $wreg = Join-Path $OccJobsRoot ($OccWatchdogJob + '.json')
      if (Test-Path -LiteralPath $wreg) { $w = Occ-ReadJson $wreg; if ($w) { $out.watchdog_alive = [bool](Omni-AliveProcess $w.pid ([string]$w.created_utc_ticks)) } }
    }
    'alerts' {
      $out.alerts = @()
      if (Test-Path -LiteralPath $OccAlertsPath) {
        $out.alerts = @(Omni-TailLines $OccAlertsPath ([int]$params.limit) | ForEach-Object { try { $_ | ConvertFrom-Json } catch { } })
      }
      $out.path = $OccAlertsPath
    }
    'claim' {
      Occ-EnsureRoot
      $p = $null
      if ($params.pid) {
        $p = Get-CimInstance Win32_Process -Filter ("ProcessId=" + [int]$params.pid) -ErrorAction SilentlyContinue
        if (-not $p) { throw "omni-remote: pid $($params.pid) is not running; nothing claimed" }
        if ($params.created_utc_ticks -and -not (Occ-SameBirth $p ([string]$params.created_utc_ticks))) { throw "omni-remote: pid $($params.pid) was reused (creation time differs); nothing claimed" }
      } elseif ($params.project) {
        $want = ([string]$params.project -replace '/', '\').TrimEnd('\')
        $editors = @(Get-CimInstance Win32_Process -Filter "Name='Unity.exe'" | Where-Object { (Occ-ProjectPath ([string]$_.CommandLine)) -ieq $want })
        $roots = @($editors | Where-Object { $pp = $_.ParentProcessId; -not ($editors | Where-Object { $_.ProcessId -eq $pp }) })
        if ($roots.Count -ne 1) { throw "omni-remote: expected exactly one Unity editor for project $want, found $($roots.Count); pass --pid" }
        $p = $roots[0]
      } else { throw 'omni-remote: claim needs a pid or a Unity project path' }
      $ticks = [string]$p.CreationDate.ToUniversalTime().Ticks
      foreach ($f in @(Get-ChildItem -LiteralPath $OccClaimsDir -Filter '*.json' -File)) {
        $c = Occ-ReadJson $f.FullName
        if (-not $c -or $c.released_at -or $c.stopped_at -or [int]$c.pid -ne [int]$p.ProcessId -or [string]$c.created_utc_ticks -ne $ticks) { continue }
        if ([string]$c.owner -ne [string]$params.owner -and -not $params.force) { throw "omni-remote: pid $($p.ProcessId) is already claimed by $($c.owner) (claim $($c.claim_id)); nothing changed" }
      }
      $claim = [ordered]@{
        schema = 'omni.remote.claim/1'; claim_id = [string]$params.claim_id; owner = [string]$params.owner; kind = [string]$params.kind
        pid = [int]$p.ProcessId; created_utc_ticks = $ticks; created = $p.CreationDate.ToUniversalTime().ToString('o'); name = $p.Name
        project = $(if ($params.project) { ([string]$params.project -replace '/', '\').TrimEnd('\') } else { Occ-ProjectPath ([string]$p.CommandLine) })
        budget = $params.budget; purpose = [string]$params.purpose; job = $params.job
        claimed_at = (Get-Date).ToString('o'); claimed_from = [string]$params.machine; released_at = $null
      }
      $path = Join-Path $OccClaimsDir ([string]$params.claim_id + '.json')
      Occ-WriteJson $path $claim
      $out.claim = $claim
      $out.path = $path
    }
    'release' {
      $path = Join-Path $OccClaimsDir ([string]$params.claim_id + '.json')
      if (-not (Test-Path -LiteralPath $path)) { throw "omni-remote: no claim named $($params.claim_id)" }
      $c = Occ-ReadJson $path
      if ([string]$c.owner -ne [string]$params.owner -and -not $params.force) { throw "omni-remote: claim $($params.claim_id) belongs to $($c.owner); only its owner releases it" }
      $c | Add-Member -NotePropertyName released_at -NotePropertyValue ((Get-Date).ToString('o')) -Force
      $c | Add-Member -NotePropertyName released_by -NotePropertyValue ([string]$params.owner) -Force
      Occ-WriteJson $path $c
      $out.claim = $c
      $out.path = $path
    }
    default { throw "omni-remote: unknown occupancy action $action" }
  }
  $out.ok = $true
} catch { $out.error = $_.Exception.Message }
'OMNI-OCC-BEGIN'
($out | ConvertTo-Json -Depth 8)
'OMNI-OCC-END'
if ($out.ok) { exit 0 } else { exit 2 }
