# omni remote occupancy library: one read of every process -> machine occupancy table.
# Writes only under the occupancy root (inside the omni-remote staging root). Stops a process only for a claim whose
# owner recorded on_breach=stop, by pid + creation time, never by name. Plain text, no encoded payloads.
$OccRoot = __OMNI_OCC_ROOT__
$OccJobsRoot = __OMNI_JOBS_ROOT__
$OccNode = __OMNI_NODE__
$OccPolicy = (__OMNI_POLICY__) | ConvertFrom-Json
$OccClaimsDir = Join-Path $OccRoot 'claims'
$OccTablePath = Join-Path $OccRoot 'occupancy.json'
$OccAlertsPath = Join-Path $OccRoot 'alerts.jsonl'
$OccAlertStatePath = Join-Path $OccRoot 'alert-state.json'
$OccPolicyPath = Join-Path $OccRoot 'policy.json'
$OccWatchdogJob = 'occupancy-watchdog'

function Occ-Gb($Bytes) { if ($null -eq $Bytes) { return $null }; return [math]::Round(([double]$Bytes) / 1GB, 2) }

function Occ-ProjectPath([string]$CommandLine) {
  if (-not $CommandLine) { return $null }
  $m = [regex]::Match($CommandLine, '(?i)-projectpath\s+(?:"([^"]+)"|(\S+))')
  if (-not $m.Success) { return $null }
  $v = if ($m.Groups[1].Success) { $m.Groups[1].Value } else { $m.Groups[2].Value }
  return ($v -replace '/', '\').TrimEnd('\')
}

function Occ-SameBirth($Proc, [string]$Ticks) {
  if (-not $Proc -or -not $Ticks) { return $false }
  return ([math]::Abs($Proc.CreationDate.ToUniversalTime().Ticks - [int64]::Parse($Ticks)) -le 20000000)
}

function Occ-ReadJson([string]$Path) {
  try { return (Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json) } catch { return $null }
}

function Occ-EnsureRoot {
  Omni-AssertOwned $OccRoot | Out-Null
  if (-not (Test-Path -LiteralPath $OccRoot)) { New-Item -ItemType Directory -Path $OccRoot -Force | Out-Null }
  if (-not (Test-Path -LiteralPath $OccClaimsDir)) { New-Item -ItemType Directory -Path $OccClaimsDir -Force | Out-Null }
  Omni-AssertPlain $OccRoot | Out-Null
  Omni-AssertPlain $OccClaimsDir | Out-Null
}

function Occ-WriteText([string]$Path, [string]$Text) {
  Omni-AssertOwned $Path | Out-Null
  $tmp = $Path + '.tmp'
  [IO.File]::WriteAllText($tmp, $Text, $OmniUtf8)
  if (Test-Path -LiteralPath $Path) { [IO.File]::Replace($tmp, $Path, [NullString]::Value) } else { [IO.File]::Move($tmp, $Path) }
}

function Occ-WriteJson([string]$Path, $Object) { Occ-WriteText $Path ($Object | ConvertTo-Json -Depth 8) }

function Occ-PrefixMatch([string]$Path, $Items) {
  if (-not $Path) { return $null }
  $p = $Path.ToLowerInvariant().TrimEnd('\')
  foreach ($it in @($Items)) {
    if (-not $it) { continue }
    $k = ([string]$it.prefix).ToLowerInvariant().TrimEnd('\')
    if ($k -and ($p -eq $k -or $p.StartsWith($k + '\'))) { return $it }
  }
  return $null
}

function Occ-SyncPolicy {
  $text = $OccPolicy | ConvertTo-Json -Depth 6
  $old = $null
  if (Test-Path -LiteralPath $OccPolicyPath) { $old = [IO.File]::ReadAllText($OccPolicyPath) }
  if ($old -ne $text) { Occ-WriteText $OccPolicyPath $text }
}

function Occ-RetireClaim($Claim) {
  # bookkeeping only: the registered process is gone (or released / stopped), so its claim moves to claims\ended
  try {
    $src = Join-Path $OccClaimsDir ([string]$Claim.claim_id + '.json')
    if (-not (Test-Path -LiteralPath $src -PathType Leaf)) { return }
    $dir = Join-Path $OccClaimsDir 'ended'
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    if (-not $Claim.released_at -and -not $Claim.stopped_at) {
      $Claim | Add-Member -NotePropertyName ended_at -NotePropertyValue ((Get-Date).ToString('o')) -Force
    }
    Occ-WriteJson (Join-Path $dir ([string]$Claim.claim_id + '.json')) $Claim
    Remove-Item -LiteralPath $src -Force
  } catch { }
}

function Occ-Collect([string]$Producer) {
  $now = Get-Date
  $os = Get-CimInstance Win32_OperatingSystem
  $perf = Get-CimInstance Win32_PerfRawData_PerfOS_Memory -ErrorAction SilentlyContinue
  $total = [double]$os.TotalVisibleMemorySize * 1KB
  $avail = if ($perf) { [double]$perf.AvailableBytes } else { [double]$os.FreePhysicalMemory * 1KB }
  $memory = [ordered]@{ total_gb = (Occ-Gb $total); available_gb = (Occ-Gb $avail); commit_gb = $null; commit_limit_gb = $null; commit_free_gb = $null }
  if ($perf) {
    $memory.commit_gb = Occ-Gb ([double]$perf.CommittedBytes)
    $memory.commit_limit_gb = Occ-Gb ([double]$perf.CommitLimit)
    $memory.commit_free_gb = Occ-Gb ([double]$perf.CommitLimit - [double]$perf.CommittedBytes)
  }
  $all = @(Get-CimInstance Win32_Process)
  $byPid = @{}
  $kids = @{}
  foreach ($p in $all) {
    $byPid[[int]$p.ProcessId] = $p
    $pp = [int]$p.ParentProcessId
    if (-not $kids.ContainsKey($pp)) { $kids[$pp] = New-Object System.Collections.Generic.List[object] }
    $kids[$pp].Add($p)
  }
  $taken = New-Object 'System.Collections.Generic.HashSet[int]'
  $entries = New-Object System.Collections.Generic.List[object]
  $stale = New-Object System.Collections.Generic.List[object]

  # root + descendants created after their parent (pid-reuse guard); pids already owned by an entry are skipped
  $entryOf = {
    param($Root, [string]$Kind)
    $tree = New-Object System.Collections.Generic.List[object]
    $seen = New-Object 'System.Collections.Generic.HashSet[int]'
    $queue = New-Object System.Collections.Generic.Queue[object]
    $queue.Enqueue($Root); [void]$seen.Add([int]$Root.ProcessId); $tree.Add($Root)
    while ($queue.Count -gt 0) {
      $parent = $queue.Dequeue()
      $children = $kids[[int]$parent.ProcessId]
      if (-not $children) { continue }
      foreach ($child in $children) {
        $cid = [int]$child.ProcessId
        if ($seen.Contains($cid) -or $taken.Contains($cid) -or $child.CreationDate -lt $parent.CreationDate) { continue }
        [void]$seen.Add($cid); $tree.Add($child); $queue.Enqueue($child)
      }
    }
    $priv = 0.0; $ws = 0.0; $projects = New-Object System.Collections.Generic.List[string]
    foreach ($t in $tree) {
      $priv += [double]$t.PrivatePageCount; $ws += [double]$t.WorkingSetSize
      [void]$taken.Add([int]$t.ProcessId)
      if ($t.Name -ieq 'Unity.exe') { $pj = Occ-ProjectPath ([string]$t.CommandLine); if ($pj -and -not $projects.Contains($pj)) { $projects.Add($pj) } }
    }
    $cl = [string]$Root.CommandLine
    [ordered]@{
      kind = $Kind; name = $Root.Name; pid = [int]$Root.ProcessId
      created = $Root.CreationDate.ToUniversalTime().ToString('o'); created_utc_ticks = [string]$Root.CreationDate.ToUniversalTime().Ticks
      session = [int]$Root.SessionId; parent_pid = [int]$Root.ParentProcessId
      private_gb = (Occ-Gb $Root.PrivatePageCount); tree_private_gb = (Occ-Gb $priv); tree_ws_gb = (Occ-Gb $ws); tree_count = $tree.Count
      project = (Occ-ProjectPath $cl); batchmode = [bool]($cl -match '(?i)(^|\s)-batchmode(\s|$)'); unity_projects = $projects.ToArray()
      owner = $null; owner_source = 'none'; owner_hint = $null; purpose = $null; claim_id = $null; job = $null; budget = $null
      state = 'ok'; breaches = @()
      command_line = $(if ($cl.Length -gt 300) { $cl.Substring(0, 300) + '...' } else { $cl })
    }
  }

  # 1) claims (explicit registrations), unity claims first so a job claim does not swallow a claimed editor
  $claims = @()
  if (Test-Path -LiteralPath $OccClaimsDir) {
    $claims = @(Get-ChildItem -LiteralPath $OccClaimsDir -Filter '*.json' -File | ForEach-Object { Occ-ReadJson $_.FullName } | Where-Object { $_ })
  }
  foreach ($c in @($claims | Sort-Object @{ Expression = { if ($_.kind -eq 'unity') { 0 } else { 1 } } })) {
    if ($c.released_at -or $c.stopped_at) { Occ-RetireClaim $c; continue }
    $p = $byPid[[int]$c.pid]
    if (-not (Occ-SameBirth $p ([string]$c.created_utc_ticks))) {
      $stale.Add([ordered]@{ claim_id = $c.claim_id; owner = $c.owner; pid = $c.pid; kind = $c.kind; purpose = $c.purpose; claimed_at = $c.claimed_at })
      Occ-RetireClaim $c
      continue
    }
    if ($taken.Contains([int]$p.ProcessId)) { continue }
    $e = & $entryOf $p ([string]$c.kind)
    $e.owner = $c.owner; $e.owner_source = 'claim'; $e.purpose = $c.purpose; $e.claim_id = $c.claim_id; $e.job = $c.job; $e.budget = $c.budget
    if ($c.project -and -not $e.project) { $e.project = $c.project }
    $entries.Add($e)
  }
  # 2) live omni remote jobs without a claim
  if (Test-Path -LiteralPath $OccJobsRoot) {
    foreach ($f in @(Get-ChildItem -LiteralPath $OccJobsRoot -Filter '*.json' -File)) {
      $j = Occ-ReadJson $f.FullName
      if (-not $j -or -not $j.pid) { continue }
      $p = $byPid[[int]$j.pid]
      if (-not (Occ-SameBirth $p ([string]$j.created_utc_ticks))) { continue }
      if ($taken.Contains([int]$p.ProcessId)) { continue }
      $e = & $entryOf $p 'job'
      $e.job = $j.name; $e.owner_source = 'job'
      $entries.Add($e)
    }
  }
  # 3) Unity editors nobody registered (workers are children of their editor and ride along)
  foreach ($p in $all) {
    if ($p.Name -ine 'Unity.exe' -or $taken.Contains([int]$p.ProcessId)) { continue }
    $parent = $byPid[[int]$p.ParentProcessId]
    if ($parent -and $parent.Name -ieq 'Unity.exe' -and $p.CreationDate -ge $parent.CreationDate) { continue }
    $e = & $entryOf $p 'unity'
    $hint = Occ-PrefixMatch $e.project $OccPolicy.project_hints
    if ($hint) { $e.owner_hint = [string]$hint.line; $e.owner_source = 'inferred' }
    $entries.Add($e)
  }
  # 4) any other large process (desktop apps, emulators, games)
  $minOther = [double]$OccPolicy.other_min_gb * 1GB
  foreach ($p in @($all | Sort-Object PrivatePageCount -Descending)) {
    if ([double]$p.PrivatePageCount -lt $minOther) { break }
    if ($taken.Contains([int]$p.ProcessId)) { continue }
    $entries.Add((& $entryOf $p 'other'))
  }
  # demogame-unity AgentBridge registrations (%TEMP%\unity-bridge-<hash>.json: projectPath, pid, port) -> port per editor
  $bridges = @{}
  foreach ($bdir in @(@($env:TEMP, [IO.Path]::GetTempPath()) | Where-Object { $_ } | Select-Object -Unique)) {
    if (-not (Test-Path -LiteralPath $bdir -PathType Container)) { continue }
    foreach ($bf in @(Get-ChildItem -LiteralPath $bdir -Filter 'unity-bridge-*.json' -File -ErrorAction SilentlyContinue)) {
      $br = Occ-ReadJson $bf.FullName
      if ($br -and $br.pid -and $br.port) { $bridges[[int]$br.pid] = [ordered]@{ port = [int]$br.port; path = $bf.FullName } }
    }
  }
  foreach ($e in $entries) {
    $hit = $bridges[[int]$e.pid]
    if ($hit) { $e['bridge_port'] = $hit.port; $e['bridge_registration'] = $hit.path }
  }
  # budgets
  $registered = 0.0
  foreach ($e in $entries) {
    $b = $e.budget
    if (-not $b) { continue }
    if ($b.mem_gb) {
      $registered += [double]$b.mem_gb
      if ([double]$e.tree_private_gb -gt [double]$b.mem_gb) { $e.breaches += 'over-memory' }
    } elseif ($b.estimate_gb) { $registered += [double]$b.estimate_gb }
    if ($b.deadline) {
      try { if ([datetime]::Parse([string]$b.deadline).ToUniversalTime() -lt $now.ToUniversalTime()) { $e.breaches += 'past-deadline' } } catch { }
    }
    if (@($e.breaches).Count -gt 0) { $e.state = 'breach' }
  }
  $flags = @()
  if ($memory.available_gb -lt [double]$OccPolicy.reserve_gb) { $flags += 'low-memory' }
  if ($null -ne $memory.commit_free_gb -and $memory.commit_free_gb -lt [double]$OccPolicy.commit_reserve_gb) { $flags += 'low-commit' }
  $watchdog = [ordered]@{ job = $OccWatchdogJob; alive = $false; pid = $null; started_at = $null }
  $wreg = Join-Path $OccJobsRoot ($OccWatchdogJob + '.json')
  if (Test-Path -LiteralPath $wreg) {
    $w = Occ-ReadJson $wreg
    if ($w -and (Occ-SameBirth $byPid[[int]$w.pid] ([string]$w.created_utc_ticks))) { $watchdog.alive = $true; $watchdog.pid = [int]$w.pid; $watchdog.started_at = $w.started_at }
  }
  $sorted = @($entries.ToArray() | Sort-Object @{ Expression = { [double]$_.tree_private_gb }; Descending = $true })
  $observed = 0.0
  foreach ($e in $sorted) { $observed += [double]$e.tree_private_gb }
  return [ordered]@{
    schema = 'omni.remote.occupancy/1'; node = $OccNode; host = $env:COMPUTERNAME; producer = $Producer
    generated_at = $now.ToString('o'); memory = $memory; machine_flags = $flags
    registered_budget_gb = [math]::Round($registered, 2); observed_gb = [math]::Round($observed, 2)
    entries = $sorted; stale_claims = $stale.ToArray(); watchdog = $watchdog
    paths = [ordered]@{ table = $OccTablePath; claims = $OccClaimsDir; alerts = $OccAlertsPath; policy = $OccPolicyPath }
  }
}

# Stop one claimed tree (root verified by pid + creation time; every child re-verified). Returns what happened.
function Occ-StopTree($Entry) {
  $root = Omni-AliveProcess $Entry.pid $Entry.created_utc_ticks
  $result = [ordered]@{ stopped = @(); skipped = @(); root_alive_after = $false }
  if (-not $root) { $result.skipped = @([ordered]@{ pid = $Entry.pid; reason = 'not-running' }); return $result }
  $tree = New-Object System.Collections.Generic.List[object]
  $tree.Add($root)
  foreach ($d in @(Omni-Descendants $root)) { $tree.Add($d) }
  $tree.Reverse()
  $stopped = New-Object System.Collections.Generic.List[object]
  $skipped = New-Object System.Collections.Generic.List[object]
  foreach ($q in $tree) {
    $check = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $q.ProcessId) -ErrorAction SilentlyContinue
    if (-not $check) { $skipped.Add([ordered]@{ pid = [int]$q.ProcessId; name = $q.Name; reason = 'already-exited' }); continue }
    if ($check.CreationDate -ne $q.CreationDate) { $skipped.Add([ordered]@{ pid = [int]$q.ProcessId; name = $q.Name; reason = 'pid-reused' }); continue }
    try { Stop-Process -Id $q.ProcessId -Force -ErrorAction Stop; $stopped.Add([ordered]@{ pid = [int]$q.ProcessId; name = $q.Name }) }
    catch { $skipped.Add([ordered]@{ pid = [int]$q.ProcessId; name = $q.Name; reason = $_.Exception.Message }) }
  }
  Start-Sleep -Milliseconds 300
  $result.stopped = $stopped.ToArray(); $result.skipped = $skipped.ToArray()
  $result.root_alive_after = [bool](Omni-AliveProcess $Entry.pid $Entry.created_utc_ticks)
  return $result
}

function Occ-MarkStopped($Entry, $Stop, [string]$Reason) {
  $at = (Get-Date).ToString('o')
  if ($Entry.claim_id) {
    $path = Join-Path $OccClaimsDir ([string]$Entry.claim_id + '.json')
    $c = Occ-ReadJson $path
    if ($c) {
      $c | Add-Member -NotePropertyName stopped_at -NotePropertyValue $at -Force
      $c | Add-Member -NotePropertyName stopped_reason -NotePropertyValue $Reason -Force
      $c | Add-Member -NotePropertyName stopped -NotePropertyValue $Stop.stopped -Force
      Occ-WriteJson $path $c
    }
  }
  if ($Entry.job) {
    $reg = Join-Path $OccJobsRoot ([string]$Entry.job + '.json')
    $j = Occ-ReadJson $reg
    if ($j -and [int]$j.pid -eq [int]$Entry.pid -and [string]$j.created_utc_ticks -eq [string]$Entry.created_utc_ticks) {
      $j | Add-Member -NotePropertyName killed_at -NotePropertyValue $at -Force
      $j | Add-Member -NotePropertyName killed -NotePropertyValue $Stop.stopped -Force
      $j | Add-Member -NotePropertyName killed_by -NotePropertyValue 'omni remote occupancy watchdog' -Force
      $json = $j | ConvertTo-Json -Depth 6
      Omni-AssertOwned $reg | Out-Null
      [IO.File]::WriteAllText($reg, $json, $OmniUtf8)
      if ($j.paths.job) { Omni-AssertOwned ([string]$j.paths.job) | Out-Null; [IO.File]::WriteAllText([string]$j.paths.job, $json, $OmniUtf8) }
      if ($j.paths.result -and -not (Test-Path -LiteralPath ([string]$j.paths.result))) {
        $marker = [ordered]@{ schema = 'omni.remote.result/1'; producer = 'omni remote occupancy watchdog'; op_id = $j.op_id; phase = 'killed'; status = 'killed'; exit_code = $null; killed_at = $at; reason = $Reason }
        Omni-AssertOwned ([string]$j.paths.result) | Out-Null
        [IO.File]::WriteAllText([string]$j.paths.result, ($marker | ConvertTo-Json -Depth 4), $OmniUtf8)
      }
    }
  }
}

function Occ-Compact($Entry) {
  [ordered]@{ kind = $Entry.kind; name = $Entry.name; pid = $Entry.pid; created_utc_ticks = $Entry.created_utc_ticks
    tree_private_gb = $Entry.tree_private_gb; project = $Entry.project; owner = $Entry.owner; owner_source = $Entry.owner_source
    owner_hint = $Entry.owner_hint; job = $Entry.job; claim_id = $Entry.claim_id; budget = $Entry.budget; purpose = $Entry.purpose }
}

# Alerts for breaches and machine pressure, de-duplicated per (object, kind) for alert_repeat_min. With -Act, claims
# whose owner chose on_breach=stop are stopped first; everyone else only gets the alert.
function Occ-Alert($Snapshot, [switch]$Act) {
  $state = @{}
  $raw = Occ-ReadJson $OccAlertStatePath
  if ($raw) { foreach ($prop in $raw.PSObject.Properties) { $state[$prop.Name] = [string]$prop.Value } }
  $repeat = [double]$OccPolicy.alert_repeat_min
  $now = Get-Date
  $out = New-Object System.Collections.Generic.List[object]
  foreach ($e in @($Snapshot.entries)) {
    if (@($e.breaches).Count -eq 0) { continue }
    $action = 'alert-only'
    $stop = $null
    $consent = ($e.owner_source -eq 'claim' -and $e.budget -and [string]$e.budget.on_breach -eq 'stop')
    if ($Act -and $consent) {
      $stop = Occ-StopTree $e
      Occ-MarkStopped $e $stop (@($e.breaches) -join ',')
      $action = $(if ($stop.root_alive_after) { 'stop-failed' } else { 'stopped' })
    }
    $key = 'breach|' + $e.pid + '|' + $e.created_utc_ticks + '|' + (@($e.breaches) -join ',')
    if ($action -eq 'alert-only' -and $state[$key]) {
      try { if (($now - [datetime]::Parse($state[$key])).TotalMinutes -lt $repeat) { continue } } catch { }
    }
    $state[$key] = $now.ToString('o')
    $out.Add([ordered]@{ at = $now.ToString('o'); node = $OccNode; kind = 'breach'; breaches = @($e.breaches); action = $action
      consent_stop = [bool]$consent; entry = (Occ-Compact $e); stop = $stop; producer = $Snapshot.producer })
  }
  foreach ($flag in @($Snapshot.machine_flags)) {
    $key = 'machine|' + $flag
    if ($state[$key]) { try { if (($now - [datetime]::Parse($state[$key])).TotalMinutes -lt $repeat) { continue } } catch { } }
    $state[$key] = $now.ToString('o')
    $top = @($Snapshot.entries | Select-Object -First 5 | ForEach-Object { Occ-Compact $_ })
    $out.Add([ordered]@{ at = $now.ToString('o'); node = $OccNode; kind = $flag; action = 'alert-only'; memory = $Snapshot.memory; top = $top; producer = $Snapshot.producer })
  }
  if ($out.Count -gt 0) {
    Omni-AssertOwned $OccAlertsPath | Out-Null
    $lines = ($out | ForEach-Object { $_ | ConvertTo-Json -Depth 8 -Compress }) -join "`n"
    [IO.File]::AppendAllText($OccAlertsPath, $lines + "`n", $OmniUtf8)
  }
  $keep = [ordered]@{}
  foreach ($k in $state.Keys) { try { if (($now - [datetime]::Parse($state[$k])).TotalHours -lt 24) { $keep[$k] = $state[$k] } } catch { } }
  Occ-WriteJson $OccAlertStatePath $keep
  return $out.ToArray()
}
