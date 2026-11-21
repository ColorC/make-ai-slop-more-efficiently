# Recovery provider extensions

Drop one JSON manifest per additional session store in this directory. Manifests
only describe roots and archive filters; they never execute code. Parser plugins
are normal Python packages registered through the
`omni_recover.providers` entry-point group.

Example:

```json
{
  "name": "my-agent",
  "roots": ["%USERPROFILE%/.my-agent"],
  "includes": ["sessions/**", "attachments/**"],
  "excludes": ["credentials/**", "cache/**"]
}
```

For a per-developer directory, set `OMNI_RECOVERY_PROVIDER_CONFIG` to one or
more directories separated by the operating-system path separator. Duplicate
provider names fail closed unless a manifest explicitly sets `"replace": true`.
