"""The clock: every command that needs the current moment reads it from `now`, so a test freezes one function.

`notes new` names the file after today's date and resolves relative schedules against the current time,
`notes tick` decides what is due, and the drafts stamp their ids and timestamps; all of them call `now()`.
"""

from datetime import datetime


def now() -> datetime:
    """The current local time as an aware datetime carrying the fixed UTC offset of this moment."""
    return datetime.now().astimezone()
