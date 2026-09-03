#!/bin/sh
# The UserPromptSubmit hook of the notes plugin: print the notes worth surfacing for the prompt, or nothing.
#
# Claude Code writes the event as a JSON object on stdin ({"cwd": "...", "prompt": "...", ...}) and adds whatever
# this script prints on stdout to the conversation as context. The script exits 0 without printing whenever it
# cannot help: `notes` or `jq` is not on PATH, there is no vault at ~/.notes, or stdin is empty, not JSON, or not
# an object. Otherwise it hands over to `notes recall`, which stays inside the hook's 10 second budget (it never
# pushes or pulls); from then on the exit status and the stderr are the command's own.

command -v notes >/dev/null 2>&1 || exit 0
command -v jq >/dev/null 2>&1 || exit 0

vault="${HOME}/.notes"
if [ ! -f "$vault/config.toml" ] || [ ! -d "$vault/notes" ]; then
    exit 0
fi

# One read of stdin, kept only when it is a JSON object; the two fields are then taken from the variable.
payload=$(jq -c objects 2>/dev/null) || exit 0
[ -n "$payload" ] || exit 0
cwd=$(printf '%s' "$payload" | jq -r '.cwd // empty' 2>/dev/null) || exit 0
# The prompt is cut to its first 4000 characters: it travels as one argument, and Linux refuses to exec an argument
# past 128 KiB, so a pasted file in the prompt would fail the hook instead of recalling anything. What a query needs
# is its opening words: the terms further in add nothing to a bounded, deterministic block of at most a few notes.
prompt=$(printf '%s' "$payload" | jq -r '.prompt // empty | tostring | .[0:4000]' 2>/dev/null) || exit 0

# `--` keeps a prompt that starts with `-` from being read as an option; an empty cwd makes recall use its own.
exec notes recall --cwd "$cwd" -- "$prompt"
