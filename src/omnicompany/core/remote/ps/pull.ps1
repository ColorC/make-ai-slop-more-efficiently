# omni remote pull (pack): read the source without following links; record every entry's byte size while zipping into an owned stage.
$src = __OMNI_SRC__
$stage = __OMNI_STAGE__
$mode = __OMNI_MODE__
$out = [ordered]@{ schema = 'omni.remote.pull/1'; ok = $false; src = $src; kind = $null; files = @(); zip_bytes = $null
  stage = $stage; sent = $false; cleanup = $null; error = $null }
$zip = Join-Path $stage 'payload.zip'
$stageOwned = $false
try {
  if (-not (Test-Path -LiteralPath $src)) { throw "omni-remote: remote source not found: $src" }
  $item = Get-Item -LiteralPath $src -Force
  $out.src = $item.FullName
  Omni-AssertTreePlain $item.FullName
  $stage = Omni-AssertOwned $stage
  if (Test-Path -LiteralPath $stage) { throw "omni-remote: preexisting stage preserved: $stage" }
  New-Item -ItemType Directory -Path $stage -Force | Out-Null
  $stageOwned = $true
  $entries = New-Object System.Collections.Generic.List[object]
  if ($item.PSIsContainer) {
    $out.kind = 'dir'
    $prefix = $item.FullName.TrimEnd('\').Length + 1
    $stack = New-Object 'System.Collections.Generic.Stack[string]'
    $stack.Push($item.FullName)
    while ($stack.Count -gt 0) {
      foreach ($child in @(Get-ChildItem -LiteralPath ($stack.Pop()) -Force)) {
        if ($child.PSIsContainer) { $stack.Push($child.FullName) } else { $entries.Add(@($child.FullName, $child.FullName.Substring($prefix).Replace('\', '/'))) }
      }
    }
  } else {
    $out.kind = 'file'
    $entries.Add(@($item.FullName, $item.Name))
  }
  Add-Type -AssemblyName System.IO.Compression
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  $files = New-Object System.Collections.Generic.List[object]
  $archive = [IO.Compression.ZipFile]::Open($zip, 'Create')
  try {
    foreach ($e in $entries) {
      $entry = $archive.CreateEntry($e[1], [IO.Compression.CompressionLevel]::Fastest)
      $fs = [IO.File]::Open($e[0], 'Open', 'Read', 'ReadWrite')
      $es = $entry.Open()
      try {
        $fs.CopyTo($es)
        $size = $fs.Position
      } finally { $fs.Dispose(); $es.Dispose() }
      $files.Add([ordered]@{ rel = $e[1]; size = $size })
    }
  } finally { $archive.Dispose() }
  $out.files = $files.ToArray()
  $out.zip_bytes = (Get-Item -LiteralPath $zip).Length
__OMNI_SEND__
  $out.ok = $true
} catch { $out.error = $_.Exception.Message } finally {
  if (-not $stageOwned) { $out.cleanup = 'not-owned' }
  elseif ($mode -eq 'http' -or -not $out.ok) {
    try { $out.cleanup = Omni-RemoveOwnedStage $stage @('payload.zip') @() } catch { $out.cleanup = 'refused: ' + $_.Exception.Message }
  } else { $out.cleanup = 'kept-for-copy-out' }
}
Omni-Emit $out
if ($out.ok) { exit 0 } else { exit 1 }
