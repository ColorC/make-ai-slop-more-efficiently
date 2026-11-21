# omni remote push (apply): write_roots only, back up before overwrite, never delete, verify every write by direct byte comparison.
$dest = __OMNI_DEST__
$stage = __OMNI_STAGE__
$backupDir = __OMNI_BACKUP__
$mode = __OMNI_MODE__
$zipBytes = __OMNI_ZIP_BYTES__
$manifestBytes = __OMNI_MANIFEST_BYTES__
$out = [ordered]@{ schema = 'omni.remote.push/1'; ok = $false; dest = $dest; stage = $stage; backup = $null; new = 0; updated = 0; unchanged = 0
  files = @(); business = 'not-written'; cleanup = $null; error = $null }
$zip = Join-Path $stage 'payload.zip'
$x = Join-Path $stage 'x'
$manifestPath = Join-Path $stage 'manifest.json'
$manifest = @()
$stageOwned = ($mode -ne 'http')
try {
  $dest = Omni-AssertTarget $dest
  $stage = Omni-AssertOwned $stage
  $backupDir = Omni-AssertOwned $backupDir
  foreach ($p in @($dest, $stage, $backupDir)) { Omni-AssertPlain $p | Out-Null }
  if (Test-Path -LiteralPath $dest -PathType Leaf) { throw "omni-remote: push destination is a file, give a directory: $dest" }
  if ($mode -eq 'http') {
    if (Test-Path -LiteralPath $stage) { throw "omni-remote: preexisting stage preserved: $stage" }
    New-Item -ItemType Directory -Path $stage -Force | Out-Null
    $stageOwned = $true
__OMNI_FETCH__
  } elseif (-not (Test-Path -LiteralPath $zip -PathType Leaf)) { throw "omni-remote: prestaged payload missing: $zip" }
  Omni-AssertSize $zip $zipBytes 'payload'
  if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) { throw "omni-remote: manifest missing: $manifestPath" }
  Omni-AssertSize $manifestPath $manifestBytes 'manifest'
  # PS 5.1 ConvertFrom-Json emits a JSON array as ONE object; re-enumerate it through the pipeline
  $parsed = ConvertFrom-Json ([IO.File]::ReadAllText($manifestPath, $OmniUtf8))
  $manifest = @($parsed | ForEach-Object { $_ })
  Add-Type -AssemblyName System.IO.Compression.FileSystem
  [IO.Compression.ZipFile]::ExtractToDirectory($zip, $x)
  Omni-AssertTreePlain $x
  $plan = New-Object System.Collections.Generic.List[object]
  foreach ($m in $manifest) {
    $rel = ([string]$m.rel).Replace('/', '\')
    $incoming = Join-Path $x $rel
    $target = Omni-AssertTarget (Join-Path $dest $rel)
    Omni-AssertPlain $target | Out-Null
    if (-not (Test-Path -LiteralPath $incoming -PathType Leaf)) { throw "omni-remote: payload entry missing: $rel" }
    Omni-AssertSize $incoming ([long]$m.size) "payload entry $rel"
    $action = 'new'
    $bk = $null
    if (Test-Path -LiteralPath $target) {
      if ((Get-Item -LiteralPath $target -Force).PSIsContainer) { throw "omni-remote: target is a directory: $target" }
      if (Omni-SameBytes $incoming $target) { $action = 'unchanged' } else {
        $action = 'updated'
        $bk = Omni-AssertOwned (Join-Path $backupDir $rel)
        if (Test-Path -LiteralPath $bk) { throw "omni-remote: existing backup preserved: $bk" }
      }
    }
    $plan.Add([ordered]@{ rel = [string]$m.rel; incoming = $incoming; target = $target; action = $action; size = [long]$m.size; backup = $bk })
  }
  $files = New-Object System.Collections.Generic.List[object]
  foreach ($e in $plan) {
    if ($e.action -eq 'unchanged') { $out.unchanged++ } else {
      if ($e.action -eq 'updated') {
        New-Item -ItemType Directory -Path (Split-Path -Parent $e.backup) -Force | Out-Null
        Copy-Item -LiteralPath $e.target -Destination $e.backup
        if (-not (Omni-SameBytes $e.backup $e.target)) { throw "omni-remote: backup copy differs from the original, nothing overwritten: $($e.backup)" }
        $out.backup = $backupDir
      }
      New-Item -ItemType Directory -Path (Split-Path -Parent $e.target) -Force | Out-Null
      $out.business = 'write-started'
      Copy-Item -LiteralPath $e.incoming -Destination $e.target -Force
      if (-not (Omni-SameBytes $e.target $e.incoming)) { throw "omni-remote: written file differs from the payload (backup kept): $($e.target)" }
      if ($e.action -eq 'new') { $out.new++ } else { $out.updated++ }
    }
    $files.Add([ordered]@{ rel = $e.rel; target = $e.target; action = $e.action; size = $e.size; backup = $e.backup })
  }
  $out.files = $files.ToArray()
  $out.business = 'written'
  $out.ok = $true
} catch { $out.error = $_.Exception.Message } finally {
  if ($stageOwned) {
    $ownedFiles = New-Object System.Collections.Generic.List[string]
    $ownedDirs = New-Object System.Collections.Generic.List[string]
    $ownedFiles.Add('payload.zip'); $ownedFiles.Add('manifest.json')
    if (Test-Path -LiteralPath $x) {
      $ownedDirs.Add('x')
      foreach ($m in $manifest) {
        $rel = ([string]$m.rel).Replace('/', '\')
        $ownedFiles.Add('x\' + $rel)
        $parts = $rel.Split('\')
        for ($i = 1; $i -lt $parts.Length; $i++) { $ownedDirs.Add('x\' + ($parts[0..($i - 1)] -join '\')) }
      }
    }
    try { $out.cleanup = Omni-RemoveOwnedStage $stage $ownedFiles.ToArray() @($ownedDirs | Select-Object -Unique) } catch { $out.cleanup = 'refused: ' + $_.Exception.Message }
  } else { $out.cleanup = 'not-owned' }
}
Omni-Emit $out
if ($out.ok) { exit 0 } else { exit 1 }
