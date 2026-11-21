# omni remote stage: prepare an owned parent before a pre-copy transfer, or remove an exact stage after it.
$action = __OMNI_ACTION__
$parent = __OMNI_PARENT__
$stage = __OMNI_STAGE__
$out = [ordered]@{ schema = 'omni.remote.stage/1'; ok = $false; action = $action; parent = $parent; stage = $stage; cleanup = $null; error = $null }
try {
  $stage = Omni-AssertOwned $stage
  if ($action -eq 'prepare') {
    $parent = Omni-AssertOwned $parent
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    Omni-AssertPlain $parent | Out-Null
    if (Test-Path -LiteralPath $stage) { throw "omni-remote: preexisting stage preserved: $stage" }
  } else {
    $out.cleanup = Omni-RemoveOwnedStage $stage @(__OMNI_OWNED_FILES__) @(__OMNI_OWNED_DIRS__)
  }
  $out.ok = $true
} catch { $out.error = $_.Exception.Message }
Omni-Emit $out
if ($out.ok) { exit 0 } else { exit 1 }
