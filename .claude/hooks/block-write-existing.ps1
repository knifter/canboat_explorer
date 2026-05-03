$input_json = [Console]::In.ReadToEnd() | ConvertFrom-Json
$file_path = $input_json.tool_input.file_path

if (Test-Path $file_path) {
    @{
        hookSpecificOutput = @{
            hookEventName         = "PreToolUse"
            permissionDecision    = "deny"
            permissionDecisionReason = "File already exists: $file_path — use Edit for existing files, not Write."
        }
    } | ConvertTo-Json -Depth 5
    exit 0
}

exit 0
