# omni remote: unified runner. Writes result.json next to the script and exits with the real code.
$__started = Get-Date
$__dir = __OMNI_OP_DIR__
$__op = [ordered]@{
  schema = 'omni.remote.result/1'
  producer = 'omni remote'
  op_id = __OMNI_OP_ID__
  node = __OMNI_NODE__
  host = $env:COMPUTERNAME
  user = [Environment]::UserName
  ps_version = $PSVersionTable.PSVersion.ToString()
  mode = __OMNI_MODE__
  delivery = __OMNI_DELIVERY__
  phase = 'prepare'
  status = 'failed'
  exit_code = 1
  exit_source = $null
  first_error = $null
  cwd = $null
  started_at = $__started.ToString('o')
  finished_at = $null
  duration_ms = $null
  args_bound = @()
  args_unbound = @()
  artifacts = @()
  log_bytes = 0
  paths = [ordered]@{
    op_dir = $__dir
    source = (Join-Path $__dir 'source.ps1')
    script = (Join-Path $__dir 'script.ps1')
    args = (Join-Path $__dir 'args.json')
    log = (Join-Path $__dir 'output.log')
    result = (Join-Path $__dir 'result.json')
  }
}
$__paths = $__op.paths
$__code = 1
$__w = $null
$global:OmniOpDir = $__dir
$global:OmniArgs = $null
$global:OmniArtifacts = New-Object System.Collections.Generic.List[string]
function global:Add-OmniArtifact([string]$Path) { $global:OmniArtifacts.Add([IO.Path]::GetFullPath($Path)) }
try {
  Omni-AssertOwned $__dir | Out-Null
  Omni-AssertPlain $__dir | Out-Null
  if ($__op.delivery -eq 'inline') {
    if (Test-Path -LiteralPath $__dir) { throw "omni-remote: op dir already exists: $__dir" }
    New-Item -ItemType Directory -Path $__dir -Force | Out-Null
    [IO.File]::WriteAllText($__paths.source, __OMNI_SCRIPT_TEXT__, $OmniUtf8Bom)
    [IO.File]::WriteAllText($__paths.args, __OMNI_ARGS_TEXT__, $OmniUtf8)
  } else {
__OMNI_FETCH__
    foreach ($__expect in @(@($__paths.source, __OMNI_SCRIPT_BYTES__), @($__paths.args, __OMNI_ARGS_BYTES__))) {
      if (-not (Test-Path -LiteralPath $__expect[0] -PathType Leaf)) { throw "omni-remote: delivered file missing: $($__expect[0])" }
      Omni-AssertSize $__expect[0] $__expect[1] 'delivered file'
    }
  }
  $__text = [IO.File]::ReadAllText($__paths.source)
  [IO.File]::WriteAllText($__paths.script, $__text + "`r`n" + '$global:__OmniReachedEnd = $true' + "`r`n", $OmniUtf8Bom)
  $__tokens = $null
  $__errors = $null
  $__ast = [System.Management.Automation.Language.Parser]::ParseFile($__paths.script, [ref]$__tokens, [ref]$__errors)
  if ($__errors -and $__errors.Count -gt 0) {
    $__e = $__errors[0]
    $__op.phase = 'parse'
    $__op.exit_source = 'parse'
    $__op.first_error = [ordered]@{
      phase = 'parse'; message = $__e.Message; type = 'ParseError'; id = $__e.ErrorId; script = $__paths.script
      line = $__e.Extent.StartLineNumber; column = $__e.Extent.StartColumnNumber; line_text = $__e.Extent.StartScriptPosition.Line.Trim()
    }
  } else {
    $__splat = @{}
    $__argsRaw = [IO.File]::ReadAllText($__paths.args)
    if ($__argsRaw.Trim()) { $global:OmniArgs = $__argsRaw | ConvertFrom-Json }
    $__declared = @()
    if ($__ast.ParamBlock) { $__declared = @($__ast.ParamBlock.Parameters | ForEach-Object { $_.Name.VariablePath.UserPath }) }
    if ($global:OmniArgs -is [System.Management.Automation.PSCustomObject]) {
      foreach ($__p in $global:OmniArgs.PSObject.Properties) {
        $__match = @($__declared | Where-Object { $_ -ieq $__p.Name }) | Select-Object -First 1
        if ($__match) { $__splat[$__match] = $__p.Value; $__op.args_bound += $__p.Name } else { $__op.args_unbound += $__p.Name }
      }
    }
    $env:OMNI_REMOTE_OP_DIR = $__dir
    $env:OMNI_REMOTE_ARGS_FILE = $__paths.args
__OMNI_ENV__
    $__cwd = __OMNI_CWD__
    if (-not $__cwd) { $__cwd = $__dir }
    if (-not (Test-Path -LiteralPath $__cwd -PathType Container)) { throw "omni-remote: cwd does not exist: $__cwd" }
    Set-Location -LiteralPath $__cwd
    $__op.cwd = (Get-Location).ProviderPath
    $__w = New-Object System.IO.StreamWriter($__paths.log, $false, $OmniUtf8)
    $__w.AutoFlush = $true
    $__op.phase = 'script'
    $global:LASTEXITCODE = 0
    $global:__OmniReachedEnd = $false
    try {
      & $__paths.script @__splat 3>&1 4>&1 5>&1 6>&1 | ForEach-Object {
        if ($_ -is [System.Management.Automation.WarningRecord]) { 'WARNING: ' + $_.Message }
        elseif ($_ -is [System.Management.Automation.VerboseRecord]) { 'VERBOSE: ' + $_.Message }
        elseif ($_ -is [System.Management.Automation.DebugRecord]) { 'DEBUG: ' + $_.Message }
        elseif ($_ -is [System.Management.Automation.InformationRecord]) { [string]$_.MessageData }
        else { $_ }
      } | Out-String -Stream -Width 4096 | ForEach-Object { $__w.WriteLine($_) }
      if ($global:__OmniReachedEnd) { $__code = 0; $__op.exit_source = 'end' }
      else { $__code = [int]$global:LASTEXITCODE; $__op.exit_source = 'exit' }
      $__op.phase = 'completed'
    } catch {
      $__code = 1
      $__op.exit_source = 'exception'
      $__op.first_error = Omni-ErrorInfo $_ 'script'
    }
  }
} catch {
  $__code = 1
  if (-not $__op.first_error) { $__op.first_error = Omni-ErrorInfo $_ $__op.phase }
} finally {
  if ($__w) { $__w.Dispose() }
  $__finished = Get-Date
  $__op.finished_at = $__finished.ToString('o')
  $__op.duration_ms = [int]($__finished - $__started).TotalMilliseconds
  $__op.exit_code = $__code
  if ($__code -eq 0) { $__op.status = 'ok' } else { $__op.status = 'failed' }
  $__op.artifacts = @($global:OmniArtifacts)
  if (Test-Path -LiteralPath $__paths.log) { $__op.log_bytes = (Get-Item -LiteralPath $__paths.log).Length }
  try {
    if (Test-Path -LiteralPath $__dir -PathType Container) {
      [IO.File]::WriteAllText($__paths.result, ($__op | ConvertTo-Json -Depth 8), $OmniUtf8)
    } else { $__op.paths.result = $null }
  } catch { $__op.result_write_error = $_.Exception.Message }
  if (__OMNI_EMIT_OUTPUT__) {
    [Console]::Out.WriteLine('OMNI-REMOTE-OUTPUT-BEGIN')
    if ($__op.log_bytes -gt 0) {
      $__limit = __OMNI_OUTPUT_LIMIT__
      $__fs = [IO.File]::Open($__paths.log, 'Open', 'Read', 'ReadWrite')
      try {
        if ($__fs.Length -le $__limit) {
          $__buf = New-Object byte[] $__fs.Length
          [void]$__fs.Read($__buf, 0, $__buf.Length)
          [Console]::Out.Write($OmniUtf8.GetString($__buf))
        } else {
          $__head = [int]($__limit / 4)
          $__tail = $__limit - $__head
          $__buf = New-Object byte[] $__head
          [void]$__fs.Read($__buf, 0, $__head)
          [Console]::Out.Write($OmniUtf8.GetString($__buf))
          [Console]::Out.WriteLine('')
          [Console]::Out.WriteLine("... [omni-remote: output truncated, $($__fs.Length) bytes total, full log: $($__paths.log)] ...")
          [void]$__fs.Seek(-$__tail, 'End')
          $__buf = New-Object byte[] $__tail
          [void]$__fs.Read($__buf, 0, $__tail)
          [Console]::Out.Write($OmniUtf8.GetString($__buf))
        }
      } finally { $__fs.Dispose() }
    }
    [Console]::Out.WriteLine('')
    [Console]::Out.WriteLine('OMNI-REMOTE-OUTPUT-END')
    Omni-Emit $__op
  }
}
exit $__code
