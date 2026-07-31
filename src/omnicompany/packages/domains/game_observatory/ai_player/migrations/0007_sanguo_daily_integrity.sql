ALTER TABLE ai_player_sanguo_daily_continuity_events
    ADD COLUMN operation TEXT;
ALTER TABLE ai_player_sanguo_daily_continuity_events
    ADD COLUMN command_json TEXT;
ALTER TABLE ai_player_sanguo_daily_continuity_events
    ADD COLUMN previous_event_sha256 TEXT;
ALTER TABLE ai_player_sanguo_daily_continuity_events
    ADD COLUMN event_sha256 TEXT;

CREATE UNIQUE INDEX IF NOT EXISTS idx_ai_player_sanguo_daily_event_hash
    ON ai_player_sanguo_daily_continuity_events(event_sha256)
    WHERE event_sha256 IS NOT NULL;
