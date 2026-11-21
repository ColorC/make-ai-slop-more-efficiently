# omni remote occupancy watchdog body (runs as the registered job 'occupancy-watchdog', bounded lifetime).
# Every tick: refresh occupancy.json, stop only consented claims that breached their budget (pid + creation time),
# alert for everything else. Never stops a process by name, never touches unclaimed processes.
$interval = [int]__OMNI_INTERVAL__
$until = (Get-Date).AddHours([double]__OMNI_HOURS__)
Occ-EnsureRoot
"omni-remote occupancy watchdog pid=$PID until=$($until.ToString('s')) interval=${interval}s root=$OccRoot"
$tick = 0
while ((Get-Date) -lt $until) {
  $tick++
  try {
    $snap = Occ-Collect 'watchdog'
    Occ-SyncPolicy
    $alerts = @(Occ-Alert $snap -Act)
    Occ-WriteJson $OccTablePath $snap
    $acted = @($alerts | Where-Object { $_.action -ne 'alert-only' } | ForEach-Object { '{0}:{1}' -f $_.action, $_.entry.pid })
    '{0} tick={1} avail={2}G commit_free={3}G entries={4} alerts={5} {6}' -f (Get-Date).ToString('s'), $tick, $snap.memory.available_gb, $snap.memory.commit_free_gb, @($snap.entries).Count, $alerts.Count, ($acted -join ' ')
  } catch {
    '{0} tick={1} error: {2}' -f (Get-Date).ToString('s'), $tick, $_.Exception.Message
  }
  Start-Sleep -Seconds $interval
}
"omni-remote occupancy watchdog reached its lifetime at $((Get-Date).ToString('s'))"
