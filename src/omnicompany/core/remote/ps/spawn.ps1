# omni remote spawn: start a detached background job (WMI, outside the single-task job object) and register pid + creation time.
$name = __OMNI_JOB_NAME__
$jobsRoot = __OMNI_JOBS_ROOT__
$dir = __OMNI_JOB_DIR__
$shell = __OMNI_SHELL__
$cwd = __OMNI_CWD__
$delivery = __OMNI_DELIVERY__
$registry = Join-Path $jobsRoot ($name + '.json')
$paths = [ordered]@{
  dir = $dir; registry = $registry; job = (Join-Path $dir 'job.json'); runner = (Join-Path $dir 'runner.ps1')
  source = (Join-Path $dir 'source.ps1'); args = (Join-Path $dir 'args.json'); log = (Join-Path $dir 'output.log')
  console = (Join-Path $dir 'console.log'); result = (Join-Path $dir 'result.json')
}
$out = [ordered]@{ schema = 'omni.remote.job/1'; ok = $false; state = $null; name = $name; op_id = __OMNI_OP_ID__; node = __OMNI_NODE__
  pid = $null; created = $null; created_utc_ticks = $null; command_line = $null; cwd = $null; started_at = $null; paths = $paths; error = $null }
try {
  Omni-AssertOwned $jobsRoot | Out-Null
  Omni-AssertOwned $dir | Out-Null
  New-Item -ItemType Directory -Path $jobsRoot -Force | Out-Null
  Omni-AssertPlain $jobsRoot | Out-Null
  if (Test-Path -LiteralPath $registry) {
    $old = Get-Content -LiteralPath $registry -Raw -Encoding UTF8 | ConvertFrom-Json
    if (Omni-AliveProcess $old.pid $old.created_utc_ticks) { throw "omni-remote: job '$name' is still running (pid=$($old.pid)); wait or kill it first" }
  }
  if ($delivery -eq 'inline') {
    if (Test-Path -LiteralPath $dir) { throw "omni-remote: job dir already exists: $dir" }
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    [IO.File]::WriteAllText($paths.source, __OMNI_SCRIPT_TEXT__, $OmniUtf8Bom)
    [IO.File]::WriteAllText($paths.args, __OMNI_ARGS_TEXT__, $OmniUtf8)
    [IO.File]::WriteAllText($paths.runner, __OMNI_RUNNER_TEXT__, $OmniUtf8Bom)
  } else {
__OMNI_FETCH__
    foreach ($expect in @(@($paths.source, __OMNI_SCRIPT_BYTES__), @($paths.args, __OMNI_ARGS_BYTES__), @($paths.runner, __OMNI_RUNNER_BYTES__))) {
      if (-not (Test-Path -LiteralPath $expect[0] -PathType Leaf)) { throw "omni-remote: delivered file missing: $($expect[0])" }
      Omni-AssertSize $expect[0] $expect[1] 'delivered file'
    }
  }
  if ($cwd) { if (-not (Test-Path -LiteralPath $cwd -PathType Container)) { throw "omni-remote: cwd does not exist: $cwd" } } else { $cwd = $dir }
  $out.cwd = $cwd
  if ($dir.Contains(' ') -or $shell.Contains(' ')) { throw "omni-remote: job dir and shell path must not contain spaces: $dir / $shell" }
  $cmd = 'cmd.exe /d /c ' + $shell + ' -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' + $paths.runner + ' 1> ' + $paths.console + ' 2>&1'
  $out.command_line = $cmd
  $createArgs = @{ CommandLine = $cmd; CurrentDirectory = $cwd }
  # -1 = default window; 0 = hidden; 7 = minimized without activation (visible, never takes focus)
  $showWindow = [int]__OMNI_SHOW_WINDOW__
  if ($showWindow -ge 0) { $createArgs.ProcessStartupInformation = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]$showWindow } }
  $out.show_window = $showWindow
  $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments $createArgs
  if ($r.ReturnValue -ne 0) { throw "omni-remote: Win32_Process.Create returned $($r.ReturnValue)" }
  $out.pid = [int]$r.ProcessId
  $p = Get-CimInstance Win32_Process -Filter ("ProcessId=" + $r.ProcessId) -ErrorAction SilentlyContinue
  $out.started_at = (Get-Date).ToString('o')
  if ($p) {
    $out.created = $p.CreationDate.ToUniversalTime().ToString('o')
    $out.created_utc_ticks = [string]$p.CreationDate.ToUniversalTime().Ticks
    $out.state = 'started'
  } else { $out.state = 'started-unverified' }
  $json = $out | ConvertTo-Json -Depth 6
  [IO.File]::WriteAllText($paths.job, $json, $OmniUtf8)
  [IO.File]::WriteAllText($registry, $json, $OmniUtf8)
  $out.ok = $true
} catch { $out.error = $_.Exception.Message; $out.state = 'failed' }
Omni-Emit $out
if ($out.ok) { exit 0 } else { exit 1 }
