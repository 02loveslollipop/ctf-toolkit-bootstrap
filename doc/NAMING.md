# Naming

This document defines the human-facing naming convention for OpenCROW skills outside the main toolbox set.

## Rules

- Keep stable skill ids, directory names, and invocation handles unchanged unless a separate compatibility change is planned.
- Name I/O-oriented helper skills as `OpenCROW I/O - %name%`.
- Name runner-oriented helper skills as `OpenCROW Runner - %name%`.

## Current Mappings

- `minecraft-async` -> `OpenCROW I/O - Minecraft Async`
- `netcat-async` -> `OpenCROW I/O - Netcat Async`
- `ssh-async` -> `OpenCROW I/O - SSH Async`
- `sagemath` -> `OpenCROW Runner - SageMath`

## Notes

- These names are display names and documentation labels.
- Existing skill references such as `$netcat-async`, `$ssh-async`, and `$sagemath` remain valid.
